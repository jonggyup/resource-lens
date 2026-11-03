from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from resourcelens.model import (CollectionScope, ProcessIdentity, CgroupIdentity,
                                TargetRef, Coverage, SourceQuality, Constraint, source_quality)
from resourcelens.recording import Recorder, Replay, validate_sample
from resourcelens.ui import View


FIXTURES = Path(__file__).parent / 'fixtures'


class ModelTests(unittest.TestCase):
    def test_process_identity_survives_rename_but_not_reuse_or_scope_change(self):
        process = ProcessIdentity('session-a', 42, 100)
        target = TargetRef('process', process, 'original')
        self.assertEqual(target, replace(target, label='renamed'))
        self.assertEqual(hash(target), hash(replace(target, label='renamed')))
        self.assertNotEqual(process, replace(process, start_ticks=101))
        self.assertNotEqual(process, replace(process, scope_id='session-b'))

    def test_replacement_cgroup_identity_includes_device_and_observed_generation(self):
        group = CgroupIdentity('session', '/test.service/child', 1, 2)
        for values in ({'device': 2}, {'inode': 3}, {'generation': 1}, {'scope_id': 'other'}):
            with self.subTest(values=values):
                self.assertNotEqual(group, replace(group, **values))

    def test_kind_mismatch_and_unsafe_identity_values_rejected(self):
        with self.assertRaises(ValueError):
            TargetRef('cgroup', ProcessIdentity('scope', 1, 1))
        for pid in (0, -1, True, 1.5):
            with self.subTest(pid=pid), self.assertRaises(ValueError):
                ProcessIdentity('scope', pid, 0)
        for path in ('relative', '/a/../b', '/a/./b', '//a', '/a/', '/a\x00b'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                CgroupIdentity('scope', path, 1, 2)
        self.assertEqual(CgroupIdentity('scope', '/', 1, 2).path, '/')

    def test_target_json_roundtrip_preserves_identity(self):
        for target in (TargetRef('process', ProcessIdentity('scope', 42, 100), 'worker'),
                       TargetRef('cgroup', CgroupIdentity('scope', '/job', 1, 2, 3))):
            encoded = json.loads(json.dumps(target.to_dict(), allow_nan=False))
            self.assertEqual(TargetRef.from_dict(encoded), target)

    def test_scope_unknowns_stay_unknown_and_partial_root_identity_is_rejected(self):
        scope = CollectionScope('session')
        self.assertIsNone(scope.to_dict()['boot_id'])
        self.assertIsNone(scope.to_dict()['cgroup_root_inode'])
        with self.assertRaises(ValueError):
            CollectionScope('session', cgroup_root_inode=42)

    def test_freshness_does_not_overwrite_partial_status_or_cross_clocks(self):
        quality = SourceQuality('cpu', 'partial', 'permission_denied', scope_id='live',
                                observed_at_wall=1000, observed_at_monotonic=20)
        self.assertEqual(quality.freshness(21, 5, 'live'), 'fresh')
        self.assertEqual(quality.freshness(26, 5, 'live'), 'stale')
        self.assertEqual(quality.status, 'partial')
        self.assertEqual(quality.freshness(26, 5, 'replay'), 'unknown')
        self.assertEqual(quality.freshness(19, 5, 'live'), 'unknown')
        self.assertEqual(SourceQuality('cpu').freshness(26, 5, 'live'), 'unknown')

    def test_invalid_quality_cannot_look_measured(self):
        for values in ({'status': 'live'}, {'status': 'ok', 'reason': 'counter_reset'},
                       {'status': 'partial', 'reason': None}, {'observed_at_wall': float('nan')},
                       {'duration_ms': -1}, {'duration_ms': True}, {'sequence': -1},
                       {'interval_start': 2}, {'interval_start': 2, 'interval_end': 1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                SourceQuality('cpu', **values)
        with self.assertRaises(ValueError):
            Coverage(omitted=-1)

    def test_quality_roundtrip_and_no_synthetic_coverage(self):
        quality = SourceQuality('processes', 'partial', 'budget_exceeded', scope_id='scope',
                                sequence=0, interval_start=1, interval_end=2,
                                coverage=Coverage(examined=100, truncation_reason='budget'))
        restored = SourceQuality.from_dict(json.loads(json.dumps(quality.to_dict(), allow_nan=False)))
        self.assertEqual(restored, quality)
        self.assertIsNone(restored.coverage.omitted)
        self.assertEqual(source_quality({'source_quality': {'processes': restored.to_dict()}}, 'processes'), quality)
        with self.assertRaises(ValueError):
            source_quality({'source_quality': {'other': restored.to_dict()}}, 'other')

    def test_limit_zero_unlimited_unavailable_and_unknown_are_distinct(self):
        origin = TargetRef('cgroup', CgroupIdentity('scope', '/job', 1, 2))
        zero = Constraint('memory', 'memory.max', origin, 0, 'bytes', 'known', SourceQuality('limits'))
        states = [zero] + [replace(zero, value=None, state=state)
                           for state in ('unlimited', 'unavailable', 'unknown')]
        self.assertEqual(len(set(states)), 4)
        for constraint in states:
            self.assertEqual(Constraint.from_dict(json.loads(json.dumps(constraint.to_dict()))), constraint)
        with self.assertRaises(ValueError):
            replace(zero, state='unlimited')
        with self.assertRaises(ValueError):
            replace(zero, value=float('inf'))
        with self.assertRaises(ValueError):
            replace(zero, quality=SourceQuality('limits', scope_id='other'))

    def test_m1_identity_scenarios_enforce_reuse_and_movement_boundaries(self):
        cases = json.loads((FIXTURES / 'target-context-scenarios.json').read_text())['cases']
        for case in cases:
            expected = case['expected']
            with self.subTest(case=case['id']):
                if 'same_process_identity' in expected:
                    identities = [ProcessIdentity('scope', case[side]['pid'], case[side]['start_ticks'])
                                  for side in ('before', 'after')]
                    self.assertEqual(identities[0] == identities[1], expected['same_process_identity'])
                if 'same_cgroup_identity' in expected:
                    identities = [CgroupIdentity('scope', **case[side]) for side in ('before', 'after')]
                    self.assertEqual(identities[0] == identities[1], expected['same_cgroup_identity'])


class MetadataCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.legacy = json.loads((FIXTURES / 'legacy-sample-v1.json').read_text())

    def test_old_scalar_values_do_not_gain_inferred_quality(self):
        self.assertEqual(validate_sample(self.legacy), self.legacy)
        quality = source_quality(self.legacy, 'cpu')
        self.assertEqual(quality.status, 'unknown')
        self.assertEqual(quality.reason, 'not_recorded')
        self.assertIsNone(quality.observed_at_wall)
        self.assertEqual(self.legacy['cpu'][0]['busy_pct'], 0)
        view = View()
        view.update(self.legacy)
        self.assertEqual(list(view.history['cpu']), [0])

    def test_optional_metadata_and_legacy_recordings_roundtrip_without_live_reads(self):
        extended = {**self.legacy, 'collection_scope': CollectionScope('recorded').to_dict(),
                    'source_quality': {'cpu': SourceQuality('cpu', 'ok', None, scope_id='recorded',
                                                           observed_at_wall=1000).to_dict()},
                    'collector': {**self.legacy['collector'],
                                  'stages': {'cpu': {'duration_ms': .1, 'cpu_ms': .05}},
                                  'counts': {'logical_cpus': 1}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'recording.jsonl'
            writer = Recorder(path)
            for sample in (self.legacy, extended):
                writer.write('sample', sample)
            writer.close()
            with patch('resourcelens.collect.Collector', side_effect=AssertionError('live read')):
                replay = Replay(path)
                try:
                    self.assertEqual(replay.sample_at(0), self.legacy)
                    self.assertEqual(replay.sample_at(1), extended)
                    self.assertEqual(source_quality(replay.sample_at(0), 'cpu').status, 'unknown')
                    self.assertEqual(source_quality(replay.sample_at(1), 'cpu').status, 'ok')
                finally:
                    replay.close()
