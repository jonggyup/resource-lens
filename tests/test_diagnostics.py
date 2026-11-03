from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from resourcelens.model import TargetRef, CgroupIdentity, ProcessIdentity, SourceQuality
from resourcelens.diagnostics import DiagnosticHistory, explain, project
from resourcelens.recording import Recorder, Replay


REQUEST = ('process', 123, 99, '/demo.service/jobs')


def snapshot(index, *, local_delta=50000, parent_delta=0, quota=50, scope='scope', generation=0):
    sample = json.loads((Path(__file__).parent / 'fixtures/legacy-sample-v1.json').read_text())
    sample['timestamp'] = 100 + index + .01
    sample['collection_scope'] = {'id': scope}
    sample['processes'] = [dict(pid=123, starttime=99, name='worker', ppid=1, state='S',
                                cpu_pct=100, memory=128, cgroup=REQUEST[3])]
    sample['pressure'] = {'cpu': {'some': {'avg10': 99}}}
    nodes = []
    for n, (path, cap, increment) in enumerate(((REQUEST[3], quota, local_delta), ('/demo.service', 25, parent_delta))):
        ref = TargetRef('cgroup', CgroupIdentity(scope, path, 1, n+10, generation)).to_dict()
        raw = f'{int(cap*1000)} 100000' if cap is not None else 'max 100000'
        nodes.append(dict(path=path, identity=ref, observed_at_monotonic=10+index,
            observed_at_wall=100+index, interval_s=1 if index else None,
            limits={'cpu.max': {'state': 'known' if cap is not None else 'unlimited', 'value': cap, 'reason': None}},
            files={'cpu.max': {'value': raw, 'reason': None},
                   'cpu.stat': {'value': f'usage_usec {index*1000000}\nthrottled_usec {index*increment}\nnr_throttled {index}\nnr_periods {index*10}\n', 'reason': None},
                   'memory.high': {'value': 'max', 'reason': None},
                   'memory.max': {'value': 'max', 'reason': None}, 'io.max': {'value': '', 'reason': None}},
            counters={'cpu.stat': {'usage_usec': 1000000*index, 'throttled_usec': index*increment,
                                  'nr_throttled': index, 'nr_periods': index*10}},
            deltas={'cpu.stat': {'throttled_usec': increment if index else None,
                                'nr_throttled': 1 if index else None, 'nr_periods': 10 if index else None}}))
    context = dict(request=list(REQUEST), target=TargetRef('process', ProcessIdentity(scope, 123, 99)).to_dict(),
        selected_group=nodes[0]['identity'], ancestors=nodes, exact_cgroup=REQUEST[3],
        service={'path': '/demo.service', 'unit': 'demo.service', 'manager': 'system'},
        mount={'root': '/', 'ancestry_visibility': 'to_visible_root'},
        ancestry_visibility='partial', observed_at_wall=100+index, observed_at_monotonic=10+index,
        status='partial', reason='not_exposed',
        limits={'quota_pct': min(quota,25) if quota is not None else 25,
                'quota_source': REQUEST[3] if quota is not None and quota<25 else '/demo.service', 'quota_state': 'known',
                'memory_max': None, 'memory_source': None, 'memory_state': 'unknown'})
    sample['target_context'] = context
    sample['source_quality'] = {'target_context': SourceQuality('target_context', 'partial', 'not_exposed',
        scope_id=scope, sequence=index+1, observed_at_wall=100+index, observed_at_monotonic=10+index).to_dict()}
    sample['cgroup_tree'] = {'nodes': [dict(path=n['path'], inode=n['identity']['identity']['inode'],
                                              device=1, cpu_pct=0, memory_current=0) for n in nodes]}
    sample['workloads'] = [dict(id='/demo.service', path='/demo.service', inode=11, device=1, unit='demo.service',
                                pids=[123], cpu_pct=0, memory_current=0)]
    return sample


def observe(count=4, **kwargs):
    history = DiagnosticHistory()
    for i in range(count):
        sample = snapshot(i, **kwargs)
        history.observe(sample)
    return history, sample


class DiagnosticsTests(unittest.TestCase):
    def test_quota_and_parent_scope_are_distinct_from_process_delay(self):
        history, sample = observe(parent_delta=25000)
        findings = explain(history, sample, REQUEST)
        local = next(f for f in findings if f.rule_id == 'local_throttling')
        parent = next(f for f in findings if f.rule_id.startswith('ancestor_throttling:'))
        self.assertEqual(local.relevance, 'observed')
        self.assertEqual(parent.relevance, 'possible')
        self.assertEqual(local.window['covered_seconds'], 3)
        self.assertEqual(local.window['throttled_ms_per_second'], 50)
        self.assertEqual(local.evidence[0]['before'], 0)
        self.assertIn('not per-process', ' '.join(local.limitations))
        config = next(f for f in findings if f.rule_id == 'configured_quota')
        self.assertIn('Inherited', config.title)
        self.assertEqual(config.evidence[0]['path'], '/demo.service')

    def test_configured_quota_busy_cpu_and_host_psi_do_not_prove_throttling(self):
        history, sample = observe(local_delta=0, parent_delta=0)
        findings = explain(history, sample, REQUEST)
        self.assertTrue(any(f.rule_id == 'configured_quota' for f in findings))
        self.assertFalse(any('throttling' in f.rule_id for f in findings))
        self.assertTrue(any(f.relevance == 'insufficient_evidence' for f in findings))

    def test_cached_context_cannot_count_as_additional_intervals(self):
        history, sample = observe(count=2)
        for _ in range(20): history.observe(sample)
        self.assertEqual(len(history.frames), 2)
        self.assertFalse(any(f.rule_id == 'local_throttling' for f in explain(history, sample, REQUEST)))

    def test_counter_rate_is_not_clamped_to_wall_time_percent(self):
        history, sample = observe(local_delta=2000000)
        local = next(f for f in explain(history, sample, REQUEST) if f.rule_id == 'local_throttling')
        self.assertEqual(local.window['throttled_ms_per_second'], 2000)

    def test_zero_delta_without_a_counter_value_remains_unknown(self):
        history, sample = observe(local_delta=0)
        sample['target_context']['ancestors'][0]['counters']['cpu.stat']['throttled_usec'] = None
        findings = explain(history, sample, REQUEST)
        self.assertFalse(any(f.rule_id == 'no_local_increase' for f in findings))
        self.assertTrue(any(f.rule_id == 'insufficient_evidence' for f in findings))

    def test_resets_missing_counters_scope_generation_and_gaps_break_persistence(self):
        for change in ('reset', 'permission', 'generation', 'scope', 'gap', 'configuration'):
            with self.subTest(change=change):
                history, sample = observe(count=3)
                sample = snapshot(3)
                node = sample['target_context']['ancestors'][0]
                if change == 'reset': node['deltas']['cpu.stat']['throttled_usec'] = None
                elif change == 'permission': node['files']['cpu.stat']['reason'] = 'permission_denied'
                elif change == 'generation':
                    node['identity']['identity']['generation'] = 1
                    sample['target_context']['selected_group'] = deepcopy(node['identity'])
                elif change == 'scope': sample = snapshot(3, scope='other')
                elif change == 'gap':
                    sample = snapshot(6)
                else: node['files']['cpu.max']['value'] = '60000 100000'
                history.observe(sample)
                self.assertFalse(any(f.rule_id == 'local_throttling' for f in explain(history, sample, REQUEST)))

    def test_stale_moved_or_invalid_evidence_cannot_reuse_positive_history(self):
        history, sample = observe()
        self.assertEqual([f.rule_id for f in explain(history, sample, REQUEST, now=200)], ['insufficient_evidence'])
        moved = deepcopy(sample); moved['processes'][0]['cgroup'] = '/elsewhere'
        self.assertEqual([f.rule_id for f in explain(history, moved, REQUEST)], ['insufficient_evidence'])
        for value in ([], {'state': 'known', 'value': float('nan')}):
            malformed = deepcopy(sample)
            malformed['target_context']['ancestors'][0]['limits']['cpu.max'] = value
            self.assertIsNone(project(malformed))
            self.assertEqual([f.rule_id for f in explain(history, malformed, REQUEST)], ['insufficient_evidence'])

    def test_replay_reproduces_findings_and_ancestor_navigation_offline(self):
        history, sample = observe(parent_delta=25000)
        expected = [f.to_dict() for f in explain(history, sample, REQUEST)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'evidence.jsonl'
            writer = Recorder(path)
            for i in range(4): writer.write('sample', snapshot(i, parent_delta=25000))
            writer.close()
            with patch('pathlib.Path.open', side_effect=AssertionError('unexpected live read')):
                # Replay uses built-in open; rules themselves perform no filesystem reads.
                replay = Replay(path)
                try:
                    replayed = DiagnosticHistory()
                    for item in replay.window(3): replayed.observe(item)
                    last = replay.sample_at(3)
                    self.assertEqual([f.to_dict() for f in explain(replayed, last, REQUEST)], expected)
                    group = ('cgroup', '/demo.service', 11, 1)
                    related = explain(replayed, last, group)
                    self.assertTrue(any(f.rule_id == 'local_throttling' for f in related))
                finally: replay.close()

    def test_rule_ids_ignore_display_labels_and_history_is_bounded(self):
        history, sample = observe()
        first = next(f.id for f in explain(history, sample, REQUEST) if f.rule_id == 'local_throttling')
        renamed = deepcopy(sample)
        renamed['target_context']['target']['label'] = 'renamed'
        renamed['processes'][0]['name'] = 'renamed'
        self.assertEqual(next(f.id for f in explain(history, renamed, REQUEST) if f.rule_id == 'local_throttling'), first)
        bounded = DiagnosticHistory(max_frames=2, max_bytes=4096)
        for i in range(20): bounded.observe(snapshot(i))
        self.assertLessEqual(len(bounded.frames), 2)
        self.assertLessEqual(bounded.bytes, 4096)
