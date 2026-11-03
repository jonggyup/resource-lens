"""Collection cost instrumentation, independent of counter/rate clocks.

Timing a source does not establish that its measurements are available or fresh.
Bounded source jobs carry their own observation quality and timestamps.
"""

import time
import threading
from copy import deepcopy
from dataclasses import dataclass, field

from .model import SourceQuality, Coverage
from .sources import error_reason


class StageTimings:
    """One sample's non-overlapping stage costs; never retain earlier samples."""

    def __init__(self, wall_clock=None, cpu_clock=None):
        self.wall_clock = wall_clock or time.monotonic
        self.cpu_clock = cpu_clock or time.thread_time
        self.stages = {}

    def measure(self, name, function, *args):
        if name in self.stages:
            raise ValueError('Duplicate collection stage: ' + name)
        wall_start, cpu_start = self.wall_clock(), self.cpu_clock()
        try:
            return function(*args)
        finally:
            self.stages[name] = {
                'duration_ms': (self.wall_clock() - wall_start) * 1000,
                'cpu_ms': (self.cpu_clock() - cpu_start) * 1000,
            }


@dataclass
class Observation:
    value: object
    status: str = 'ok'
    reason: str | None = None
    coverage: Coverage = field(default_factory=Coverage)

    def __post_init__(self):
        SourceQuality('observation', self.status, self.reason, coverage=self.coverage)


class SampleSource:
    """A bounded cache, optionally read by one lazy daemon worker.

    There is at most one running and one latest pending job. Changed keys retire
    old results. Read errors can retain a successful value with its original age.
    close() never waits on an uninterruptible kernel read.
    """

    def __init__(self, name, scope_id, *, ttl=1, background=False, clock=time.monotonic, retain_on_error=True):
        self.name, self.scope_id = name, scope_id
        self.ttl, self.background, self.clock = ttl, background, clock
        self.retain_on_error = retain_on_error
        self.condition = threading.Condition()
        self.key = None
        self.revision = 0
        self.pending = self.running = self.record = None
        self.thread = None
        self.closed = False
        self.sequence = 0

    def request(self, key, function):
        with self.condition:
            if self.closed:
                return
            if key != self.key:
                self.key = key
                self.revision += 1
                self.record = None
                self.pending = None
            if self.running == self.revision or self.pending is not None:
                return
            if self.record is not None and 0 <= self.clock() - self.record['finished'] < self.ttl:
                return
            job = (self.revision, key, function, self.clock())
            if self.background:
                self.pending = job
                if self.thread is None:
                    self.thread = threading.Thread(target=self._loop, daemon=True,
                                                   name='resource-lens-' + self.name)
                    self.thread.start()
                self.condition.notify()
                return
            self.running = self.revision
        self._execute(job)

    def _loop(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                job, self.pending = self.pending, None
                self.running = job[0]
            self._execute(job)

    def _execute(self, job):
        revision, key, function, queued = job
        start, wall_start, cpu_start = self.clock(), time.monotonic(), time.thread_time()
        try:
            result = function()
            if not isinstance(result, Observation):
                result = Observation(result)
        except OSError as exc:
            result = Observation(None, 'error', error_reason(exc))
        except Exception:
            # A broken optional reader must not terminate its worker or the UI.
            result = Observation(None, 'error', 'parse_error')
        finished, stamp = self.clock(), time.time()
        duration, cpu = (time.monotonic() - wall_start) * 1000, (time.thread_time() - cpu_start) * 1000
        with self.condition:
            self.running = None
            if self.closed or revision != self.revision:
                return
            self.sequence += 1
            quality = SourceQuality(self.name, result.status, result.reason, scope_id=self.scope_id,
                                    sequence=self.sequence, observed_at_wall=stamp,
                                    observed_at_monotonic=finished, duration_ms=duration,
                                    coverage=result.coverage).to_dict()
            value = result.value
            if (self.retain_on_error and result.status in ('unavailable', 'error') and self.record is not None
                    and result.reason not in ('not_exposed', 'disappeared', 'identity_changed', 'membership_changed')):
                old = self.record
                if old['value'] is not None:
                    value = old['value']
                    for name in ('observed_at_wall', 'observed_at_monotonic'):
                        quality[name] = old['quality'][name]
            self.record = dict(key=key, value=value, quality=quality, finished=finished,
                               activity=dict(sequence=self.sequence, attempted_at_wall=stamp,
                                             duration_ms=duration, cpu_ms=cpu,
                                             queue_delay_ms=max(0, start - queued) * 1000))
            self.condition.notify_all()

    def snapshot(self, default=None):
        with self.condition:
            if self.record is None:
                quality = SourceQuality(self.name, 'unavailable', 'warmup', scope_id=self.scope_id).to_dict()
                return deepcopy(default), quality, {'pending': self.pending is not None or self.running is not None}
            return (deepcopy(self.record['value']), deepcopy(self.record['quality']),
                    dict(self.record['activity'], pending=self.pending is not None or self.running is not None))

    def invalidate(self):
        with self.condition:
            self.revision += 1
            self.record = self.pending = None

    def clear(self):
        with self.condition:
            self.revision += 1
            self.key = self.record = self.pending = None

    def close(self):
        with self.condition:
            self.closed = True
            self.pending = None
            self.condition.notify_all()
