from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from resourcelens.model import TargetRef
from resourcelens.sources import ReadResult
from resourcelens.targets import TargetResolver, mount_context, context_lines, selection_request
from resourcelens.ui import View
from resourcelens.collect import Collector
from resourcelens.cgroup_browser import CgroupBrowser
from resourcelens.recording import Recorder, Replay
from test_navigation import Screen
import curses
import resourcelens.targets as targets


DEFAULTS = {
    'cpu.max': 'max 100000', 'cpu.weight': '100', 'cpuset.cpus.effective': '0-3',
    'memory.max': 'max', 'memory.high': 'max', 'memory.swap.max': 'max', 'io.max': '',
    'memory.low':'0','memory.min':'0','memory.swap.current':'0','memory.peak':'0',
    'cpu.stat': 'usage_usec 100\nnr_throttled 0\nthrottled_usec 0\n',
    'memory.current': '0', 'memory.events': 'high 0\nmax 0\noom 0\noom_kill 0\n',
    'memory.events.local': 'high 0\nmax 0\noom 0\noom_kill 0\n',
    'memory.stat': 'anon 0\nfile 0\n', 'cgroup.type': 'domain',
    **{kind + '.pressure': 'some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n'
       for kind in ('cpu', 'memory', 'io')},
}


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'cg'
        self.proc = Path(self.temp.name) / 'proc'
        self.root.mkdir()
        self.proc.mkdir()
        self.now = [10.0]
        self.mount = dict(root='/', ancestry_visibility='to_system_root', memory_events_scope='subtree')
        self.resolver = TargetResolver(self.proc, self.root, 'scope', mount=self.mount, clock=lambda: self.now[0])

    def group(self, path, values=None):
        directory = self.root / path.lstrip('/')
        directory.mkdir(parents=True, exist_ok=True)
        for name, value in {**DEFAULTS, **(values or {})}.items():
            file = directory / name
            if value is not None:
                file.write_text(value)
        return directory

    def process(self, path, start=100):
        directory = self.proc / '42'
        directory.mkdir(exist_ok=True)
        values = ['0'] * 22
        values[0], values[19] = 'S', str(start)
        (directory / 'stat').write_text('42 (worker (test)) ' + ' '.join(values))
        (directory / 'cgroup').write_text('0::' + path + '\n')

    def request(self, path):
        info = (self.root / path.lstrip('/')).stat()
        return ('cgroup', path, info.st_ino, info.st_dev)

    def test_prepared_m1_constraint_scenarios(self):
        fixture = Path(__file__).parent / 'fixtures/target-context-scenarios.json'
        for case in json.loads(fixture.read_text())['cases']:
            if 'groups' not in case:
                continue
            with self.subTest(case=case['id']), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for group, properties in case['groups'].items():
                    directory = root / group.lstrip('/')
                    directory.mkdir(parents=True, exist_ok=True)
                    for name, value in {**DEFAULTS, **{k: v for k, v in properties.items() if '.' in k}}.items():
                        if value is not None:
                            (directory / name).write_text(value)
                self.process(case['process']['membership'])
                mount = dict(self.mount, ancestry_visibility=case['visibility'])
                resolver = TargetResolver(self.proc, root, 'scope', mount=mount)
                original = targets.read_text
                def read(path, **kwargs):
                    relative = '/' + str(Path(path).parent.relative_to(root)) if Path(path).is_relative_to(root) else None
                    if relative == '/.':
                        relative = '/'
                    properties = case['groups'].get(relative, {})
                    if Path(path).name in properties and properties[Path(path).name] is None:
                        return ReadResult(None, properties.get('reason', 'not_exposed'))
                    return original(path, **kwargs)
                with patch('resourcelens.targets.read_text', side_effect=read):
                    result = resolver.resolve(('process', 42, 100, case['process']['membership'])).value
                expected = case['expected']
                self.assertTrue(result['ancestors'], result)
                for key in ('exact_cgroup', 'ancestry_visibility'):
                    if key in expected:
                        self.assertEqual(result[key], expected[key])
                if 'service_path' in expected:
                    actual = result['service']['path'] if result['service']['unit'] else None
                    self.assertEqual(actual, expected['service_path'])
                for key in ('quota_pct', 'quota_source', 'memory_max'):
                    if key in expected:
                        self.assertEqual(result['limits'][key], expected[key])
                if 'constraint_state' in expected:
                    self.assertEqual(result['limits']['quota_state'], expected['constraint_state'])

    def test_counter_intervals_scope_and_resets(self):
        directory = self.group('/job.service')
        request = self.request('/job.service')
        first = self.resolver.resolve(request).value
        self.assertIsNone(first['ancestors'][0]['interval_s'])
        (directory / 'cpu.stat').write_text('usage_usec 400\nnr_throttled 2\nthrottled_usec 1000\n')
        (directory / 'memory.events').write_text('high 3\noom 1\n')
        self.now[0] += 2
        second = self.resolver.resolve(request).value['ancestors'][0]
        self.assertEqual(second['interval_s'], 2)
        self.assertEqual(second['deltas']['cpu.stat']['throttled_usec'], 1000)
        self.assertEqual(second['deltas']['memory.events']['high'], 3)
        self.assertEqual(second['memory_events_scope'], 'subtree')
        self.assertEqual(second['memory_events_local_scope'], 'local')
        (directory / 'cpu.stat').write_text('usage_usec 1\nthrottled_usec 1\n')
        self.now[0] += 1
        reset = self.resolver.resolve(request).value['ancestors'][0]
        self.assertIsNone(reset['deltas']['cpu.stat']['throttled_usec'])

    def test_movement_and_pid_reuse_discard_all_ancestor_evidence(self):
        self.group('/old.service')
        self.process('/old.service')
        for after, expected in (((100, '/new.service'), 'membership_changed'),
                                ((200, '/old.service'), 'identity_changed')):
            with self.subTest(expected=expected), patch.object(self.resolver, '_process', side_effect=[
                    ((100, '/old.service'), None), (after, None)]):
                result = self.resolver.resolve(('process', 42, 100, '/old.service'))
                self.assertEqual(result.reason, expected)
                self.assertEqual(result.value['ancestors'], [])
        self.process('/old.service', start=200)
        self.assertEqual(self.resolver.resolve(('process', 42, 100, '/old.service')).reason, 'identity_changed')

    def test_group_identity_rechecked_after_reads(self):
        directory = self.group('/job.service')
        request = self.request('/job.service')
        original = targets.read_text
        changed = []
        def replace(path, **kwargs):
            result = original(path, **kwargs)
            if Path(path) == directory / 'memory.stat' and not changed:
                directory.rename(self.root / 'retired')
                self.group('/job.service')
                changed.append(True)
            return result
        with patch('resourcelens.targets.read_text', side_effect=replace):
            result = self.resolver.resolve(request)
        self.assertEqual(result.reason, 'identity_changed')
        self.assertEqual(result.value['ancestors'], [])
        self.assertFalse(self.resolver.previous)

    def test_observed_disappearance_retires_generation_even_when_inode_returns(self):
        directory = self.group('/job.service')
        request = self.request('/job.service')
        first = self.resolver.resolve(request).value['selected_group']
        directory.rename(self.root / 'retired')
        self.assertEqual(self.resolver.resolve(request).reason, 'disappeared')
        (self.root / 'retired').rename(directory)
        second = self.resolver.resolve(request).value['selected_group']
        self.assertNotEqual(TargetRef.from_dict(first), TargetRef.from_dict(second))

    def test_depth_byte_and_malformed_data_are_partial(self):
        self.group('/parent')
        directory = self.group('/parent/job.service')
        request = self.request('/parent/job.service')
        self.resolver.max_depth = 1
        result = self.resolver.resolve(request)
        self.assertEqual(result.reason, 'budget_exceeded')
        self.assertEqual(result.value['ancestry_visibility'], 'partial')
        self.assertIsNone(result.coverage.omitted)
        self.resolver.max_depth = 32
        self.resolver.max_bytes = 1
        result = self.resolver.resolve(request)
        self.assertEqual(result.reason, 'budget_exceeded')
        self.assertLessEqual(result.value['bytes_read'], 2)
        self.resolver.max_bytes = 1048576
        (directory / 'cpu.max').write_text('max 0')
        (directory / 'cpu.pressure').write_text('some avg10=NaN')
        result = self.resolver.resolve(request).value
        node = result['ancestors'][0]
        self.assertEqual(node['limits']['cpu.max']['reason'], 'parse_error')
        self.assertEqual(node['files']['cpu.pressure']['reason'], 'parse_error')
        self.assertIsNone(node['counters']['cpu.pressure'])

    def test_outside_mount_traversal_symlink_and_unknown_mapping_are_rejected(self):
        directory = self.group('/job.service')
        request = self.request('/job.service')
        self.resolver.mount_override = dict(self.mount, root='/hidden')
        self.assertEqual(self.resolver.resolve(request).reason, 'not_exposed')
        self.resolver.mount_override = self.mount
        self.assertEqual(self.resolver.resolve(('cgroup', '/../escape', 1, None)).reason, 'parse_error')
        with tempfile.TemporaryDirectory() as outside:
            (self.root / 'escape').symlink_to(outside)
            self.assertEqual(self.resolver.resolve(('cgroup', '/escape', 1, None)).reason, 'not_exposed')
        self.resolver.mount_override = None
        self.assertEqual(self.resolver.resolve(request).reason, 'not_exposed')

    def test_threaded_and_local_memory_event_scope_remain_explicit(self):
        self.group('/job.service', {'cgroup.type': 'domain threaded'})
        self.resolver.mount_override = dict(self.mount, memory_events_scope='local')
        result = self.resolver.resolve(self.request('/job.service')).value
        self.assertEqual(result['ancestors'][0]['memory_events_scope'], 'local')
        self.assertIn('Threaded', ' '.join(result['limitations']))

    def test_visible_browser_paths_are_mapped_separately_from_membership_paths(self):
        directory = self.group('/job.service')
        info = directory.stat()
        self.resolver.mount_override = dict(self.mount, root='/tenant', ancestry_visibility='to_visible_root')
        result = self.resolver.resolve(('cgroup', '/job.service', info.st_ino, info.st_dev, 'visible')).value
        self.assertEqual(result['exact_cgroup'], '/tenant/job.service')
        self.assertEqual(result['ancestors'][0]['path'], '/tenant/job.service')
        self.assertEqual(result['ancestors'][-1]['path'], '/tenant')

    def test_mount_mapping_handles_subtrees_escaped_paths_and_hidden_ancestry(self):
        text = '10 1 0:20 /tenant /tmp/some\\040path rw - cgroup2 cgroup rw,memory_localevents\n'
        context = mount_context(text, Path('/tmp/some path/cg'))
        self.assertEqual(context['root'], '/tenant/cg')
        self.assertEqual(context['ancestry_visibility'], 'to_visible_root')
        self.assertEqual(context['memory_events_scope'], 'local')
        self.assertIsNone(mount_context(text, Path('/elsewhere')))

    def test_recorded_context_render_and_selection_never_read_live_data(self):
        self.group('/job.service', {'cpu.max': '50000 100000'})
        request = self.request('/job.service')
        context = self.resolver.resolve(request).value
        sample = dict(timestamp=context['observed_at_wall'] + 2, target_context=context)
        with patch('resourcelens.targets.read_text', side_effect=AssertionError('live read')):
            text = '\n'.join(context_lines(sample, request))
            self.assertIn('50%', text)
            self.assertIn('2.0s', text)
            self.assertNotIn('50%', '\n'.join(context_lines(sample, ('cgroup', '/other', 1, None))))
            malformed = deepcopy(sample)
            malformed['target_context'].pop('limits')
            self.assertIn('invalid', ' '.join(context_lines(malformed, request)))
        view = View()
        view.snapshot = {'processes': [dict(pid=42, starttime=100, cgroup='/job.service')]}
        view.page, view.application_process = 7, (42, 100)
        self.assertEqual(selection_request(view), ('process', 42, 100, '/job.service'))
        view.replay = object()
        self.assertIsNone(selection_request(view))
        view.replay, view.paused = None, True
        self.assertIsNone(selection_request(view))

    def test_browser_constraints_keyboard_mouse_and_back_preserve_target(self):
        directory = self.group('/job.service', {'cpu.max': '50000 100000'})
        info = directory.stat()
        request = ('cgroup', '/job.service', info.st_ino, info.st_dev, 'visible')
        context = self.resolver.resolve(request).value
        sample = dict(timestamp=context['observed_at_wall'], target_context=context, processes=[],
                      cgroup_tree={'nodes': [dict(path='/job.service', inode=info.st_ino,
                                                 device=info.st_dev, cpu_pct=0, memory_current=0)]})
        browser = CgroupBrowser(sample, '/job.service')
        browser.key(ord('D'), sample, 24)
        self.assertTrue(browser.context_detail)
        screen = Screen(24, 80)
        browser.draw(screen, sample, 80, 24)
        self.assertIn('50%', ' '.join(screen.lines.values()))
        browser.key(27, sample, 24)
        self.assertFalse(browser.context_detail)
        browser.mouse(2, 4, curses.BUTTON1_CLICKED, sample, 24)
        self.assertTrue(browser.context_detail)
        browser.mouse(2, 2, curses.BUTTON1_CLICKED, sample, 24)
        self.assertFalse(browser.context_detail)
        self.assertEqual((browser.path, browser.inode), ('/job.service', info.st_ino))

    def test_detail_worker_publication_recording_and_selection_cancellation(self):
        self.group('/job.service', {'cpu.max': '50000 100000'})
        request = self.request('/job.service')
        collector = Collector(self.proc, Path(self.temp.name) / 'sys', nvidia=False, background=True)
        collector.target_resolver = TargetResolver(self.proc, self.root, collector.scope.id, mount=self.mount)
        try:
            collector.request_target(request)
            source = collector.sources['target_context']
            with source.condition:
                self.assertTrue(source.condition.wait_for(lambda: source.record is not None, timeout=1))
            sample = collector.sample()
            self.assertEqual(sample['target_context']['request'], list(request))
            file = Path(self.temp.name) / 'recording.jsonl'
            writer = Recorder(file)
            writer.write('sample', sample)
            writer.close()
            with patch('resourcelens.targets.read_text', side_effect=AssertionError('live read')):
                replay = Replay(file)
                try:
                    recorded = replay.sample_at(0)
                    self.assertEqual(recorded['target_context'], sample['target_context'])
                    self.assertIn('50%', ' '.join(context_lines(recorded, request)))
                finally:
                    replay.close()
            collector.request_target(None)
            self.assertNotIn('target_context', collector.sample())
        finally:
            collector.close()
            for source in collector.sources.values():
                if source.thread:
                    source.thread.join(1)
