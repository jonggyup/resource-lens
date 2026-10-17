from copy import deepcopy
import contextlib
import curses
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from resourcelens.collect import Collector
from resourcelens.focus import candidates
from resourcelens.investigation import Investigation, indicators
from resourcelens.recording import Recorder, Replay, RecordingError
from resourcelens.resource_panels import resource_lines
from resourcelens.ui import View


class InvestigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = Collector(nvidia=False).sample()

    def test_thermal_counter_intervals_reset_and_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            collector = Collector(nvidia=False)
            collector.sys = Path(directory)
            path = collector.sys / 'devices/system/cpu/cpu0/thermal_throttle'
            path.mkdir(parents=True)
            counter = path / 'core_throttle_count'
            counter.write_text('3')
            self.assertIsNone(collector.thermal_throttling()[0]['core_events'])
            counter.write_text('5')
            self.assertEqual(collector.thermal_throttling()[0]['core_events'], 2)
            self.assertEqual(collector.thermal_throttling()[0]['core_events'], 0)
            counter.write_text('1')
            self.assertIsNone(collector.thermal_throttling()[0]['core_events'])
            counter.unlink()
            self.assertIsNone(collector.thermal_throttling()[0]['core_events'])

    def test_replay_hardware_menu_is_explicitly_read_only(self):
        view = View(control=True)
        view.replay = object()
        with patch.object(view.hardware, 'snapshot', side_effect=AssertionError('live read')):
            view.hardware_menu()
        self.assertIn('Replay is read-only', view.modal['message'])

    def test_record_roundtrip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            recorder = Recorder(path)
            recorder.write('sample', self.sample)
            event = dict(timestamp=self.sample['timestamp'], label='baseline', status='marker', target=None)
            recorder.write('event', event)
            recorder.close()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                Recorder(path)
            replay = Replay(path)
            self.addCleanup(replay.close)
            self.assertEqual(replay.sample_at(0), self.sample)
            self.assertEqual(replay.events, [event])
            self.assertEqual(replay.window(99), [self.sample])

    def test_record_limit_does_not_break_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = Recorder(Path(directory) / 'limited', max_bytes=100)
            writer.write('sample', self.sample)
            self.assertIn('size limit', writer.error)
            self.assertLessEqual(writer.bytes, 100)
            writer.close()

    def test_interrupted_tail_is_reported_but_prior_samples_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'partial'
            writer = Recorder(path)
            writer.write('sample', self.sample)
            writer.close()
            with path.open('ab') as file:
                file.write(b'{"type":')
            replay = Replay(path)
            self.addCleanup(replay.close)
            self.assertEqual(len(replay.offsets), 1)
            self.assertTrue(replay.warnings)

    def test_invalid_record_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad'
            path.write_text('{}\n')
            with self.assertRaises(RecordingError):
                Replay(path)
            path.write_text('{"type":"header","format":"resource-lens","version":1}\n[]\n')
            with self.assertRaises(RecordingError):
                Replay(path)

    def test_replay_cli_never_constructs_live_collector(self):
        from resourcelens.__main__ import main
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session'
            writer = Recorder(path)
            writer.write('sample', self.sample)
            writer.close()
            with patch('sys.argv', ['resource-lens', '--replay', str(path), '--json', '--count', '1']), \
                 patch('resourcelens.__main__.Collector', side_effect=AssertionError('live read')), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(), 0)
            self.assertEqual(json.loads(output.getvalue()), self.sample)
            with patch('sys.argv', ['resource-lens', '--replay', str(path), '--control']), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    main()
                self.assertEqual(error.exception.code, 2)

    def test_replay_blocks_control_jobs_even_with_stale_modal(self):
        view = View(control=True)
        view.replay = object()
        called = []
        view.job('apply', lambda: called.append(True))
        self.assertFalse(called)
        self.assertIn('read-only', view.modal['message'])

    def test_comparison_counts_missing_data_and_service_replacement(self):
        investigation = Investigation()
        for stamp, usage in [(80, 20), (90, None), (110, 40), (120, 60)]:
            sample = deepcopy(self.sample)
            sample['timestamp'] = stamp
            sample['cpu'][0]['busy_pct'] = usage
            investigation.observe(sample)
        event = investigation.mark('test', timestamp=100)
        pair, counts = investigation.compare(event)
        self.assertEqual(counts, [2, 2])
        self.assertEqual(pair[0]['Host CPU %'], (20, 1))
        self.assertEqual(pair[1]['Host CPU %'], (50, 2))
        event['target'] = {'id': '/missing', 'inode': 123}
        pair, _ = investigation.compare(event)
        self.assertEqual(pair, [{}, {}])

    def test_thermal_and_pressure_unknown_vs_zero(self):
        sample = deepcopy(self.sample)
        sample['pressure'] = {}
        sample['thermal_throttling'] = []
        self.assertIn('unknown', ' '.join(indicators(sample)))
        sample['thermal_throttling'] = [{'core_events': 0, 'package_events': 0}]
        self.assertIn('no events', indicators(sample)[-1])
        sample['thermal_throttling'][0]['package_events'] = 1
        self.assertIn('events observed', indicators(sample)[-1])

    def test_focus_filters_devices_and_preserves_other_summaries(self):
        view = View()
        sample = deepcopy(self.sample)
        if not sample['disks']:
            self.skipTest('No disk fixture')
        first = sample['disks'][0]
        sample['disks'] = [first, {**first, 'name': 'test-other-disk', 'identity': 'other'}]
        view.update(sample)
        view.page = 3
        view.focus = ('disk', first['name'])
        text = '\n'.join(resource_lines(view, 'disk', 100))
        self.assertNotIn('test-other-disk', text)
        view.cycle_focus(1)
        self.assertEqual(view.focus, ('disk', 'test-other-disk'))
        view.open_focus()
        self.assertIsNone(view.focus)
        view.open_focus()
        self.assertEqual(view.modal['stage'], 'focus')
        view.focus_key(10)
        self.assertEqual(view.focus, ('disk', first['name']))

    def test_device_histories_separate_and_prune_replacements(self):
        view = View()
        sample = deepcopy(self.sample)
        if not sample['disks']:
            self.skipTest('No disk fixture')
        first = sample['disks'][0]
        sample['disks'] = [{**first, 'active_pct': 10}, {**first, 'name': 'other', 'identity': 'other', 'active_pct': 90}]
        view.update(sample)
        self.assertEqual(list(view.device_history('disk', sample['disks'][0], 'active_pct')), [10])
        self.assertEqual(list(view.device_history('disk', sample['disks'][1], 'active_pct')), [90])
        sample['disks'] = []
        view.update(sample)
        self.assertFalse(any(key.startswith('device:disk:') for key in view.history if isinstance(key, str)))

    def test_overlay_never_attaches_to_reused_cgroup(self):
        view = View()
        sample = deepcopy(self.sample)
        sample['workloads'] = [dict(id='/service', inode=1, cpu_pct=0, limits={'ancestors': [{'cpuset.cpus.effective': '0,2'}]})]
        view.update(sample)
        view.overlay = {'id': '/service', 'inode': 1}
        self.assertEqual(view.overlay_cpus(), {0, 2})
        sample['workloads'][0]['inode'] = 2
        self.assertIsNone(view.overlay_group())
        self.assertIsNone(view.overlay_cpus())

    def test_markers_and_frozen_analysis(self):
        view = View()
        sample = deepcopy(self.sample)
        view.update(sample)
        view.key(ord('M'))
        self.assertIn('|', view.marker_line(30))
        view.paused = True
        next_sample = deepcopy(sample)
        next_sample['timestamp'] += 1
        view.update(next_sample)
        self.assertEqual(view.snapshot['timestamp'], sample['timestamp'])
        self.assertEqual(len(view.investigation.samples), 2)

    def test_control_failures_are_marked_unconfirmed(self):
        view = View()
        view.pending_action = dict(timestamp=1, target=None, plan={'setting': 'governor', 'value': 'performance'})
        view.results.put(('hardware_apply', None, 'denied'))
        view.poll_actions()
        event = view.investigation.events[-1]
        self.assertEqual(event['status'], 'unconfirmed')
        self.assertIn('governor=performance', event['label'])
