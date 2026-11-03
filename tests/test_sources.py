import errno
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from resourcelens.sources import read_text
from resourcelens.sampling import SampleSource, Observation


class ReadTests(unittest.TestCase):
    def test_empty_is_readable_but_missing_and_oversized_are_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'value'
            file.write_text('')
            self.assertEqual(read_text(file).text, '')
            self.assertIsNone(read_text(file).reason)
            file.write_text('123456')
            result = read_text(file, limit=3)
            self.assertEqual(result.reason, 'budget_exceeded')
            self.assertEqual(result.bytes_read, 4)
            self.assertIsNone(result.text)
            self.assertEqual(read_text(Path(tmp) / 'missing').reason, 'not_exposed')
            self.assertEqual(read_text(Path(tmp) / 'missing', missing='disappeared').reason, 'disappeared')

    def test_errno_and_invalid_encoding_are_preserved(self):
        with patch.object(Path, 'open', side_effect=PermissionError(errno.EACCES, 'denied')):
            self.assertEqual(read_text('/fixture').reason, 'permission_denied')
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'value'
            file.write_bytes(b'\xff')
            self.assertEqual(read_text(file).reason, 'parse_error')


class SourceCacheTests(unittest.TestCase):
    def test_cache_age_error_retention_and_invalidation(self):
        now = [10.0]
        source = SampleSource('test', 'scope', ttl=1, clock=lambda: now[0])
        self.addCleanup(source.close)
        source.request('a', lambda: 42)
        value, first, _ = source.snapshot()
        self.assertEqual(value, 42)
        now[0] = 10.5
        calls = []
        source.request('a', lambda: calls.append(True) or -1)
        self.assertFalse(calls)
        self.assertEqual(source.snapshot()[1]['observed_at_monotonic'], 10)
        now[0] = 11
        source.request('a', lambda: Observation(None, 'error', 'permission_denied'))
        value, quality, activity = source.snapshot()
        self.assertEqual(value, 42)
        self.assertEqual(quality['reason'], 'permission_denied')
        self.assertEqual(quality['observed_at_monotonic'], first['observed_at_monotonic'])
        self.assertEqual(activity['sequence'], 2)
        source.invalidate()
        source.request('a', lambda: 0)
        self.assertEqual(source.snapshot()[0], 0)
        source.request('b', lambda: Observation(None, 'unavailable', 'not_exposed'))
        self.assertIsNone(source.snapshot()[0])

    def test_unavailable_target_does_not_retain_old_context(self):
        source = SampleSource('target', 'scope', ttl=0, retain_on_error=False)
        self.addCleanup(source.close)
        source.request('a', lambda: {'ancestors': ['old']})
        source.request('a', lambda: Observation({'ancestors': []}, 'unavailable', 'disappeared'))
        self.assertEqual(source.snapshot()[0]['ancestors'], [])

    def test_worker_coalesces_requests_and_discards_retired_results(self):
        source = SampleSource('test', 'scope', ttl=0, background=True)
        entered, release = threading.Event(), threading.Event()
        calls = []
        def blocked():
            calls.append('first')
            entered.set()
            release.wait(2)
            return 'retired'
        source.request('first', blocked)
        self.assertTrue(entered.wait(1))
        thread = source.thread
        try:
            for value in range(100):
                source.request(value, lambda value=value: calls.append(value) or value)
            self.assertIs(source.thread, thread)
            self.assertIsNone(source.snapshot()[0])
            release.set()
            with source.condition:
                self.assertTrue(source.condition.wait_for(lambda: source.record is not None, timeout=1))
            self.assertEqual(source.snapshot()[0], 99)
            self.assertEqual(calls, ['first', 99])
        finally:
            release.set()
            source.close()
            thread.join(1)

    def test_close_does_not_join_a_blocked_read_or_publish_after_close(self):
        source = SampleSource('test', 'scope', background=True)
        entered, release = threading.Event(), threading.Event()
        def blocked():
            entered.set()
            release.wait(2)
            return 1
        source.request('key', blocked)
        self.assertTrue(entered.wait(1))
        try:
            start = time.monotonic()
            source.close()
            self.assertLess(time.monotonic() - start, .1)
        finally:
            release.set()
            source.thread.join(1)
        self.assertIsNone(source.record)
