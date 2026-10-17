"""Opt-in sustained pressure capture; bounded prehistory, files and cooldown."""
from collections import deque
from pathlib import Path
import math
import json
import time
from .recording import Recorder


class IncidentCapture:
    def __init__(self, directory, threshold=20, duration=10, cooldown=60, post=10, max_files=10, interval=1):
        if not all(math.isfinite(v) for v in (threshold, duration, cooldown, post, interval)) or not (0 < threshold <= 100 and duration > 0 and cooldown >= 0 and post >= 0 and interval > 0 and 1 <= max_files <= 100):
            raise ValueError('Invalid incident threshold/duration/cooldown/post/file limit')
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.threshold, self.duration, self.cooldown, self.post = threshold, duration, cooldown, post
        self.max_files = max_files
        self.history = deque()
        self.history_sizes = deque()
        self.history_bytes = 0
        self.since = {}
        self.last_sample = None
        self.max_gap = max(5, interval*3)
        self.last_trigger = -math.inf
        self.writer = None
        self.end = None
        self.status = 'Armed (PSI and workload quota pressure)'

    def observe(self, sample):
        stamp = sample['timestamp']
        if self.last_sample is not None and (stamp <= self.last_sample or stamp-self.last_sample > self.max_gap):
            self.since.clear()
        self.last_sample = stamp
        length = len(json.dumps(sample, allow_nan=False).encode('utf-8'))
        self.history.append(sample)
        self.history_sizes.append(length)
        self.history_bytes += length
        while self.history and (len(self.history)>120 or self.history_bytes>8*1024*1024):
            self.history.popleft()
            self.history_bytes -= self.history_sizes.popleft()
        if self.writer:
            self.writer.write('sample', sample)
            if self.writer.error:
                self.status = self.writer.error
                self.close()
            elif stamp >= self.end:
                self.close()
                self.status = 'Capture complete; cooldown active'
            return None
        signals = {kind + ' PSI': sample.get('pressure', {}).get(kind, {}).get('some', {}).get('avg10') for kind in ('cpu', 'memory', 'io')}
        for group in sample.get('workloads', []):
            value = group.get('throttled_usec_per_s')
            signals[f"quota {group['id']} inode={group.get('inode')}"] = value / 10000 if value is not None else None
        self.since = {key: value for key, value in self.since.items() if key in signals}
        for key, value in signals.items():
            if value is None or not math.isfinite(value) or value < self.threshold:
                self.since.pop(key, None)
                continue
            self.since.setdefault(key, stamp)
            if stamp-self.since[key] < self.duration or stamp-self.last_trigger < self.cooldown:
                continue
            # Stop at the budget, never delete user evidence automatically.
            if len(list(self.directory.glob('incident-*.jsonl'))) >= self.max_files:
                self.status = 'Incident file budget reached; move old captures to re-arm'
                return None
            path = self.directory / f'incident-{time.time_ns()}.jsonl'
            self.writer = Recorder(path, max_bytes=16*1024*1024)
            for prior in self.history:
                self.writer.write('sample', prior)
            event = dict(timestamp=stamp, label='Sustained ' + key, target=None, status='alert')
            self.writer.write('event', event)
            self.end, self.last_trigger = stamp+self.post, stamp
            self.status = 'Capturing ' + str(path)
            return event
        return None

    def close(self):
        if self.writer:
            self.writer.close()
            self.writer = None
