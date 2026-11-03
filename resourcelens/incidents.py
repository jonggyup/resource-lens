"""Opt-in sustained pressure capture; bounded prehistory, files and cooldown."""
from collections import deque
from pathlib import Path
import math
import json
import time
from .recording import Recorder


class IncidentCapture:
    def __init__(self, directory, threshold=20, duration=10, cooldown=60, post=10, max_files=10, interval=1,quota_ms_per_s=None,memory_events=1):
        if not all(math.isfinite(v) for v in (threshold, duration, cooldown, post, interval)) or not (0 < threshold <= 100 and duration > 0 and cooldown >= 0 and post >= 0 and interval > 0 and 1 <= max_files <= 100):
            raise ValueError('Invalid incident threshold/duration/cooldown/post/file limit')
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.threshold, self.duration, self.cooldown, self.post = threshold, duration, cooldown, post
        self.quota_ms_per_s=threshold*10 if quota_ms_per_s is None else quota_ms_per_s
        if not math.isfinite(self.quota_ms_per_s) or self.quota_ms_per_s<=0 or type(memory_events) is not int or memory_events<=0:
            raise ValueError('Quota threshold uses positive ms/s; memory threshold uses positive event counts')
        self.memory_events=memory_events
        from .resource_findings import ResourceHistory
        self.resources=ResourceHistory()
        self.emitted={};self.last_scope=None
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
        scope=sample.get('collection_scope',{}).get('id')
        if self.last_scope!=scope:self.since.clear()
        self.last_scope=scope
        self.resources.observe(sample)
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
        signals = {kind + ' PSI': (sample.get('pressure', {}).get(kind, {}).get('some', {}).get('avg10'),self.threshold,'PSI some.avg10 percent',None) for kind in ('cpu', 'memory', 'io')}
        for group in sample.get('workloads', []):
            value = group.get('throttled_usec_per_s')
            signals[f"quota {group['id']} inode={group.get('inode')} device={group.get('device')}"] = (value / 1000 if value is not None else None,self.quota_ms_per_s,'throttled ms/s',None)
        from .resource_findings import explain
        request=(sample.get('target_context') or {}).get('request')
        findings=explain(self.resources,sample,request,psi_threshold=self.threshold,duration=max(3,self.duration)) if request else []
        for item in findings:
            if item.relevance!='observed':continue
            value=item.window.get('events_delta',item.window.get('mean_psi_pct'))
            threshold=self.memory_events if 'events_delta' in item.window else self.threshold
            signals[item.id]=(value,threshold,item.window['unit'],item)
        self.since = {key: value for key, value in self.since.items() if key in signals}
        for key, (value,threshold,unit,item) in signals.items():
            if value is None or not math.isfinite(value) or value < threshold:
                self.since.pop(key, None)
                continue
            self.since.setdefault(key, stamp)
            fingerprint=(item.evidence[-1].get('after'),item.evidence[-1].get('interval_end')) if item else None
            if item and 'events_delta' in item.window and self.emitted.get(key)==fingerprint:continue
            persistent=bool(item) or stamp-self.since[key]>=self.duration
            if not persistent or stamp-self.last_trigger < self.cooldown:
                continue
            # Stop at the budget, never delete user evidence automatically.
            if len(list(self.directory.glob('incident-*.jsonl'))) >= self.max_files:
                self.status = 'Incident file budget reached; move old captures to re-arm'
                return None
            path = self.directory / f'incident-{time.time_ns()}.jsonl'
            self.writer = Recorder(path, max_bytes=16*1024*1024)
            for prior in self.history:
                self.writer.write('sample', prior)
            target=None
            if item:
                identity=(sample['target_context']['selected_group']['identity'])
                target=dict(id=identity['path'],inode=identity['inode'],device=identity.get('device'))
            event = dict(timestamp=stamp, label=('Observed '+item.title if item else 'Sustained '+key), target=target, status='alert',
                         threshold=threshold,unit=unit,observed_value=value,
                         findings=[item.to_dict()] if item else [],deduplication_key=key,
                         persistence_seconds=item.window['covered_seconds'] if item else stamp-self.since[key])
            if item:
                self.emitted[key]=fingerprint
                if len(self.emitted)>512:self.emitted.pop(next(iter(self.emitted)))
            self.writer.write('event', event)
            self.end, self.last_trigger = stamp+self.post, stamp
            self.status = 'Capturing ' + str(path)
            return event
        return None

    def close(self):
        if self.writer:
            self.writer.close()
            self.writer = None
