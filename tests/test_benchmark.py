import json
import unittest

from scripts.benchmark_collection import distribution, run_case


class SimulatedHost:
    def __init__(self, work=.8, cpu_work=.05):
        self.wall = 0.0
        self.cpu = 0.0
        self.work = work
        self.cpu_work = cpu_work
        self.samples = 0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.wall += seconds

    def sample(self):
        self.samples += 1
        self.wall += self.work
        self.cpu += self.cpu_work
        return {'hostname': 'must-not-appear-in-report', 'warnings': [],
                'collector': {'duration_ms': self.work * 1000, 'cpu_ms': self.cpu_work * 1000,
                              'rss_bytes': 1024,
                              'stages': {'slow_source': {'duration_ms': self.work * 1000,
                                                        'cpu_ms': self.cpu_work * 1000}},
                              'counts': {'processes': 3}},
                'cgroup_tree': {'truncated': True, 'errors': 2}}

    def run(self, interval, mode='collect'):
        return run_case(self, interval=interval, samples=3, warmup=2, mode=mode,
                        wall_clock=lambda: self.wall, cpu_clock=lambda: self.cpu,
                        process_cpu_clock=lambda: self.cpu, sleep=self.sleep)


class BenchmarkTests(unittest.TestCase):
    def test_percentiles_keep_real_zero_and_exclude_missing(self):
        result = distribution([None, 0, 10, 20, 30])
        self.assertEqual(result['count'], 4)
        self.assertEqual(result['median'], 15)
        self.assertEqual(result['p95'], 30)
        self.assertEqual(result['min'], 0)
        self.assertIsNone(distribution([None])['median'])
        with self.assertRaises(ValueError):
            distribution([float('nan')])

    def test_warmup_and_wait_excluded_from_collection_but_included_in_cadence(self):
        host = SimulatedHost()
        result = host.run(1)
        self.assertEqual(host.samples, 5)
        self.assertAlmostEqual(result['measured_window_s'], 3)
        self.assertAlmostEqual(result['monitor_process_cpu_ms'], 150)
        self.assertAlmostEqual(result['monitor_cpu_one_core_pct'], 5)
        self.assertEqual(result['collection_overruns'], 0)
        self.assertEqual(result['metrics']['collection_ms']['median'], 800)
        self.assertEqual(result['metrics']['start_interval_ms']['median'], 1000)
        self.assertEqual(result['metrics']['json_ms']['count'], 0)
        self.assertEqual(result['coverage']['hierarchy_truncated_samples'], 3)
        self.assertEqual(result['coverage']['hierarchy_errors'], 6)
        self.assertEqual(result['stages']['slow_source']['cpu_ms']['count'], 3)

    def test_overrun_keeps_minimum_rest_and_reports_actual_interval(self):
        host = SimulatedHost()
        result = host.run(.2)
        self.assertEqual(result['collection_overruns'], 3)
        self.assertEqual(result['pipeline_overruns'], 3)
        self.assertEqual(result['metrics']['start_interval_ms']['median'], 850)
        self.assertEqual(result['metrics']['start_delay_ms']['median'], 650)
        self.assertTrue(all(abs(value - .05) < 1e-9 for value in host.sleeps))

    def test_json_mode_encodes_but_does_not_leak_identifiers_into_report(self):
        result = SimulatedHost().run(1, mode='json')
        self.assertGreater(result['metrics']['json_bytes']['median'], 0)
        self.assertEqual(result['metrics']['json_ms']['count'], 3)
        text = json.dumps(result, allow_nan=False)
        self.assertNotIn('must-not-appear-in-report', text)
        self.assertNotIn('hostname', text)

    def test_invalid_request_is_rejected_before_sampling(self):
        host = SimulatedHost()
        for values in ({'interval': float('nan'), 'samples': 1},
                       {'interval': .1, 'samples': 1}, {'interval': 1, 'samples': 0},
                       {'interval': 1, 'samples': 1, 'warmup': 0}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                run_case(host, **values)
        self.assertEqual(host.samples, 0)
