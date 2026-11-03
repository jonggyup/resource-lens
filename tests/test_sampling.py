import json
from pathlib import Path
import tempfile
import unittest
import threading
import time
from unittest.mock import patch

from resourcelens.collect import Collector
from resourcelens.sampling import StageTimings
from resourcelens.sampling import Observation
from resourcelens.usability import capability_lines


class SamplingTests(unittest.TestCase):
    def test_stage_preserves_result_and_separates_wait_from_cpu_cost(self):
        wall, cpu = [10.0], [2.0]
        timings = StageTimings(lambda: wall[0], lambda: cpu[0])
        result = {'missing': None, 'zero': 0}
        def read():
            wall[0] += .8
            cpu[0] += .02
            return result
        self.assertIs(timings.measure('slow', read), result)
        self.assertAlmostEqual(timings.stages['slow']['duration_ms'], 800)
        self.assertAlmostEqual(timings.stages['slow']['cpu_ms'], 20)

    def test_instrumentation_does_not_swallow_source_errors(self):
        timings = StageTimings()
        def fail():
            raise OSError('source failed')
        with self.assertRaisesRegex(OSError, 'source failed'):
            timings.measure('failed', fail)
        self.assertIn('failed', timings.stages)
        with self.assertRaises(ValueError):
            timings.measure('failed', lambda: None)

    def test_live_metadata_is_additive_and_per_sample_not_accumulating(self):
        with tempfile.TemporaryDirectory() as directory:
            proc, sys = Path(directory) / 'proc', Path(directory) / 'sys'
            proc.mkdir()
            (proc / 'stat').write_text('cpu 1 0 0 9 0 0 0 0\ncpu0 1 0 0 9 0 0 0 0\n')
            now = [100.0]
            collector = Collector(proc, sys, nvidia=False, clock=lambda: now[0])
            try:
                first = collector.sample()
                now[0] += 2
                (proc / 'stat').write_text('cpu 2 0 0 18 0 0 0 0\ncpu0 2 0 0 18 0 0 0 0\n')
                second = collector.sample()
                self.assertEqual(second['elapsed_s'], 2)
                self.assertEqual(second['cpu'][0]['busy_pct'], 10)
                self.assertIsNone(first['cpu'][0]['busy_pct'])
                self.assertEqual(second['source_quality']['cpu']['status'], 'ok')
                self.assertEqual(second['collection_scope'], first['collection_scope'])
                self.assertIsNone(second['collection_scope']['boot_id'])
                self.assertIsNot(first['collector']['stages'], second['collector']['stages'])
                self.assertEqual(set(second['collector']['stages']), {
                    'cpu', 'frequencies', 'memory', 'disks', 'network', 'processes', 'workloads',
                    'root_filesystem', 'load', 'gpus', 'cpu_hardware', 'cgroup_tree',
                    'resource_topology', 'thermal_throttling', 'pressure', 'temperatures',
                    'energy', 'capabilities'})
                for metric in ('duration_ms', 'cpu_ms'):
                    costs = [stage[metric] for stage in second['collector']['stages'].values()]
                    self.assertTrue(all(value >= 0 for value in costs))
                    self.assertLessEqual(sum(costs), second['collector'][metric])
                self.assertEqual(second['collector']['counts']['logical_cpus'], 1)
                self.assertEqual(second['collector']['counts']['processes'], 0)
                self.assertIn('Slowest collection stages', '\n'.join(capability_lines(second)))
                self.assertEqual(json.loads(json.dumps(second, allow_nan=False))['schema_version'], 1)
            finally:
                collector.close()

    def test_collectors_have_distinct_scope_even_without_boot_visibility(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Collector(directory, directory, nvidia=False)
            second = Collector(directory, directory, nvidia=False)
            try:
                self.assertNotEqual(first.scope.id, second.scope.id)
                self.assertIsNone(first.scope.boot_id)
            finally:
                first.close()
                second.close()

    def test_blocked_optional_sources_do_not_block_cpu_or_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'stat').write_text('cpu 1 0 0 9 0 0 0 0\ncpu0 1 0 0 9 0 0 0 0\n')
            now = [10.0]
            collector = Collector(root, root / 'sys', nvidia=False, background=True, clock=lambda: now[0])
            entered_frequency, entered_gpu, release = threading.Event(), threading.Event(), threading.Event()
            def frequency(ids):
                entered_frequency.set()
                release.wait(2)
                return Observation({'values': {'cpu0': 1000}, 'reasons': {}})
            def gpu():
                entered_gpu.set()
                release.wait(2)
                return Observation([])
            try:
                with patch.object(collector, 'frequencies', side_effect=frequency), patch.object(collector, 'gpu_observation', side_effect=gpu):
                    start = time.monotonic()
                    first = collector.sample()
                    self.assertLess(time.monotonic() - start, .25)
                    self.assertTrue(entered_frequency.wait(1))
                    self.assertTrue(entered_gpu.wait(1))
                    now[0] += 1
                    (root / 'stat').write_text('cpu 2 0 0 18 0 0 0 0\ncpu0 2 0 0 18 0 0 0 0\n')
                    second = collector.sample()
                    self.assertEqual(second['cpu'][0]['busy_pct'], 10)
                    self.assertEqual(first['source_quality']['frequencies']['reason'], 'warmup')
                    self.assertIsNone(second['cpu'][1]['frequency_mhz'])
                    start = time.monotonic()
                    collector.close()
                    self.assertLess(time.monotonic() - start, .1)
            finally:
                release.set()
                collector.close()
                for source in collector.sources.values():
                    if source.thread:
                        source.thread.join(1)

    def test_inventory_cache_online_invalidation_and_fresh_hierarchy_intervals(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, sys = Path(tmp) / 'proc', Path(tmp) / 'sys'
            proc.mkdir()
            (proc / 'stat').write_text('cpu 1 0 0 9 0 0 0 0\n')
            cpu_root, group = sys / 'devices/system/cpu', sys / 'fs/cgroup'
            cpu_root.mkdir(parents=True)
            group.mkdir(parents=True)
            (cpu_root / 'online').write_text('0')
            (group / 'cpu.stat').write_text('usage_usec 100\n')
            now = [10.0]
            collector = Collector(proc, sys, nvidia=False, background=True, clock=lambda: now[0])
            try:
                with patch.object(collector, 'hardware_inventory', wraps=collector.hardware_inventory) as inventory:
                    first = collector.sample()
                    now[0] += .2
                    (group / 'cpu.stat').write_text('usage_usec 1000100\n')
                    cached = collector.sample()
                    self.assertEqual(cached['source_quality']['cpu_hardware']['observed_at_monotonic'],
                                     first['source_quality']['cpu_hardware']['observed_at_monotonic'])
                    self.assertIsNone(cached['cgroup_tree']['nodes'][0]['cpu_pct'])
                    self.assertEqual(inventory.call_count, 1)
                    now[0] += .8
                    refreshed = collector.sample()
                    self.assertEqual(refreshed['cgroup_tree']['nodes'][0]['cpu_pct'], 100)
                    (cpu_root / 'online').write_text('0-1')
                    collector.sample()
                    self.assertEqual(inventory.call_count, 2)
                    collector.invalidate_inventory()
                    collector.sample()
                    self.assertEqual(inventory.call_count, 3)
            finally:
                collector.close()
                for source in collector.sources.values():
                    if source.thread:
                        source.thread.join(1)

    def test_process_budget_and_explicit_fast_source_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            for pid in (1, 2):
                directory = proc / str(pid)
                directory.mkdir()
                (directory / 'stat').write_text('malformed')
            collector = Collector(proc, proc / 'sys', nvidia=False, process_limit=1)
            try:
                sample = collector.sample()
                quality = sample['source_quality']
                self.assertEqual(quality['cpu']['reason'], 'not_exposed')
                self.assertEqual(quality['memory.available']['reason'], 'not_exposed')
                self.assertEqual(quality['processes']['reason'], 'budget_exceeded')
                self.assertEqual(quality['processes']['coverage']['examined'], 1)
                self.assertIsNone(quality['processes']['coverage']['omitted'])
            finally:
                collector.close()
