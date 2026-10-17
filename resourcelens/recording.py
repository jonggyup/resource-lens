"""Bounded JSONL recording and indexed, read-only replay. No executable content."""
import json
import math
import os
import threading

MAX_LINE = 16 * 1024 * 1024


class RecordingError(ValueError):
    pass


class Recorder:
    def __init__(self, path, max_bytes=256 * 1024 * 1024):
        self.path = str(path)
        self.lock = threading.Lock()
        self.error = None
        self.max_bytes = max_bytes
        self.bytes = 0
        self.file = os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w')
        self.write('header', {'format': 'resource-lens', 'version': 1})

    def write(self, kind, value):
        with self.lock:
            if self.error or self.file.closed:
                return
            try:
                row = dict(type=kind, **value) if kind == 'header' else {'type': kind, kind: value}
                text = json.dumps(row, allow_nan=False, separators=(',', ':')) + '\n'
                length = len(text.encode('utf-8'))
                if length > MAX_LINE or self.bytes + length > self.max_bytes:
                    self.error = 'Recording stopped at size limit; monitoring continues.'
                    return
                self.file.write(text)
                self.file.flush()
                self.bytes += length
            except (OSError, ValueError) as exc:
                self.error = f'Recording stopped: {exc}'

    def close(self):
        with self.lock:
            self.file.close()


def validate_sample(sample):
    if not isinstance(sample, dict) or sample.get('schema_version') != 1:
        raise RecordingError('Unsupported or missing sample schema.')
    stamp = sample.get('timestamp')
    if not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
        raise RecordingError('Invalid sample timestamp.')
    for key in ('cpu', 'disks', 'gpus', 'network', 'processes', 'load', 'temperatures', 'warnings'):
        if not isinstance(sample.get(key), list):
            raise RecordingError('Missing sample list: ' + key)
    for key in ('memory', 'pressure'):
        if not isinstance(sample.get(key), dict):
            raise RecordingError('Missing sample object: ' + key)
    for key in ('cpu', 'disks', 'gpus', 'network', 'processes', 'temperatures'):
        if any(not isinstance(item, dict) for item in sample[key]):
            raise RecordingError('Invalid sample entries: ' + key)
    return sample


class Replay:
    """Index offsets rather than loading all snapshots into memory."""
    def __init__(self, path):
        self.file = open(path, 'rb')
        self.offsets, self.events, self.warnings = [], [], []
        self.index = 0
        try:
            header = json.loads(self.file.readline(MAX_LINE + 1))
            if header != {'type': 'header', 'format': 'resource-lens', 'version': 1}:
                raise RecordingError('Not a supported Resource Lens recording.')
            line_number = 1
            while True:
                offset = self.file.tell()
                line = self.file.readline(MAX_LINE + 1)
                if not line:
                    break
                line_number += 1
                if len(line) > MAX_LINE:
                    raise RecordingError(f'Recording line {line_number} exceeds size limit.')
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError) as exc:
                    if not line.endswith(b'\n') and not self.file.read(1):
                        self.warnings.append('Ignored interrupted final recording line.')
                        break
                    raise RecordingError(f'Invalid recording line {line_number}.') from exc
                if not isinstance(row, dict):
                    raise RecordingError(f'Invalid record at line {line_number}.')
                if row.get('type') == 'sample':
                    validate_sample(row.get('sample'))
                    self.offsets.append(offset)
                    if len(self.offsets) > 1000000:
                        raise RecordingError('Recording exceeds one million samples.')
                elif row.get('type') == 'event' and isinstance(row.get('event'), dict):
                    event = row['event']
                    if not isinstance(event.get('timestamp'), (int, float)) or not math.isfinite(event['timestamp']) or not isinstance(event.get('label'), str) or not isinstance(event.get('status'), str):
                        raise RecordingError('Invalid timeline event.')
                    target = event.get('target')
                    if target is not None and (not isinstance(target, dict) or not isinstance(target.get('id'), str)):
                        raise RecordingError('Invalid event target.')
                    if len(self.events) < 100000:
                        self.events.append(row['event'])
                    else:
                        raise RecordingError('Too many recorded events.')
                else:
                    raise RecordingError(f'Unknown record at line {line_number}.')
            if not self.offsets:
                raise RecordingError('Recording contains no samples.')
        except Exception:
            self.file.close()
            raise

    def sample_at(self, index):
        self.file.seek(self.offsets[index])
        return json.loads(self.file.readline(MAX_LINE + 1))['sample']

    def window(self, index):
        self.index = max(0, min(len(self.offsets) - 1, index))
        return [self.sample_at(i) for i in range(max(0, self.index - 119), self.index + 1)]

    def close(self):
        self.file.close()
