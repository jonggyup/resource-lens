"""Bounded JSONL recording and indexed, read-only replay. No executable content."""
import json
import math
import os
import threading
import stat
import hashlib

from .evidence import decode, finite, validate_tree

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
    if not isinstance(sample, dict) or type(sample.get('schema_version')) is not int or sample['schema_version'] != 1:
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
    try:
        validate_tree(sample)
        numeric={'busy_pct','iowait_pct','steal_pct','cpu_pct','memory_pct','active_pct','read_bps','write_bps',
                 'rx_bps','tx_bps','memory_current','throttled_usec_per_s','watts','avg10','avg60','avg300',
                 'used','total','buffers','cache','swap_used','swap_total','elapsed_s',
                 'inode','generation','pid','ppid','starttime','rss_bytes','memory_used','memory_total',
                 'power_w','temperature_c','frequency_mhz','nice'}
        def check(value):
            if isinstance(value,dict):
                for key,item in value.items():
                    if key in numeric and item is not None and not finite(item):
                        raise ValueError('Invalid numeric sample field: '+key)
                    check(item)
            elif isinstance(value,list):
                for item in value:check(item)
        check(sample)
        if sample['memory'].get('available') is not None and not finite(sample['memory']['available']):raise ValueError('Invalid available RAM')
        for field,identity,kind in (('cpu','id',str),('processes','pid',int),('disks','name',str),('network','name',str)):
            if any(not isinstance(row.get(identity),kind) for row in sample[field]):
                raise ValueError('Missing or invalid '+field+' identity')
        for field in ('workloads',):
            if field in sample and (not isinstance(sample[field],list) or any(not isinstance(row,dict) or not isinstance(row.get('id'),str) for row in sample[field])):
                raise ValueError('Invalid '+field+' rows')
        for field in ('source_quality','collection_scope','cgroup_tree','cpu_hardware','resource_topology','energy'):
            if field in sample and sample[field] is not None and not isinstance(sample[field],dict):
                raise ValueError('Invalid sample object: '+field)
        for field in ('pressure',):
            if any(not isinstance(v,dict) for v in sample[field].values()):raise ValueError('Invalid pressure row')
            if any(not isinstance(v,dict) for row in sample[field].values() for v in row.values()):raise ValueError('Invalid pressure scope')
        for field,table in (('cpu_hardware','cpus'),('cgroup_tree','nodes'),('energy','domains')):
            rows=(sample.get(field) or {}).get(table,[])
            if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):raise ValueError('Invalid '+field+' table')
        if not isinstance((sample.get('cpu_hardware') or {}).get('policies',{}),dict):raise ValueError('Invalid CPU policies')
        context=sample.get('target_context')
        if context is not None:
            if not isinstance(context,dict):raise ValueError('Invalid target context')
            if context.get('status') not in ('unavailable','error'):
                from .model import TargetRef
                for key in ('target','selected_group'):TargetRef.from_dict(context[key])
                if not isinstance(context.get('request'),list) or not isinstance(context.get('ancestors'),list):
                    raise ValueError('Invalid recorded target request/ancestry')
                for node in context['ancestors']:
                    if not isinstance(node,dict) or not isinstance(node.get('path'),str):raise ValueError('Invalid ancestor')
                    TargetRef.from_dict(node['identity'])
                    for key in ('limits','counters','deltas','files'):
                        if not isinstance(node.get(key,{}),dict):raise ValueError('Invalid ancestor '+key)
                    for row in node.get('files',{}).values():
                        if not isinstance(row,dict) or row.get('value') is not None and not isinstance(row['value'],str):raise ValueError('Invalid evidence file contents')
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError) as exc:
        raise RecordingError('Invalid nested sample evidence: '+str(exc)) from exc
    return sample


class Replay:
    """Index offsets rather than loading all snapshots into memory."""
    def __init__(self, path, max_bytes=256*1024*1024):
        self.file = os.fdopen(os.open(path, os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW), 'rb')
        self.offsets, self.events, self.warnings = [], [], []
        self.sizes=[]
        self.digests=[]
        self.index = 0
        try:
            info=os.fstat(self.file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size>max_bytes:
                raise RecordingError('Recording must be a regular file within the 256 MiB import budget')
            self.identity=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
            header = decode(self.file.readline(MAX_LINE + 1))
            if header != {'type': 'header', 'format': 'resource-lens', 'version': 1} or type(header.get('version')) is not int:
                raise RecordingError('Not a supported Resource Lens recording.')
            line_number = 1
            event_bytes=0
            while True:
                offset = self.file.tell()
                line = self.file.readline(MAX_LINE + 1)
                if not line:
                    break
                line_number += 1
                if len(line) > MAX_LINE:
                    raise RecordingError(f'Recording line {line_number} exceeds size limit.')
                if self.file.tell()>max_bytes:raise RecordingError('Recording grew past import size budget')
                try:
                    row = decode(line)
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
                    self.sizes.append(len(line))
                    self.digests.append(hashlib.blake2s(line,digest_size=16).digest())
                    if len(self.offsets) > 1000000:
                        raise RecordingError('Recording exceeds one million samples.')
                elif row.get('type') == 'event' and isinstance(row.get('event'), dict):
                    event_bytes+=len(line)
                    if event_bytes>8*1024*1024:raise RecordingError('Recorded event metadata exceeds 8 MiB import budget')
                    event = row['event']
                    if not isinstance(event.get('timestamp'), (int, float)) or not math.isfinite(event['timestamp']) or not isinstance(event.get('label'), str) or not isinstance(event.get('status'), str):
                        raise RecordingError('Invalid timeline event.')
                    target = event.get('target')
                    if target is not None and (not isinstance(target, dict) or not isinstance(target.get('id'), str)):
                        raise RecordingError('Invalid event target.')
                    for key in ('started_at','completed_at','verified_at','settling_s'):
                        if event.get(key) is not None and not finite(event[key]):raise RecordingError('Invalid event timing: '+key)
                    if event.get('domain') is not None and event['domain'] not in ('cpu','disk','placement','hardware','memory'):
                        raise RecordingError('Unknown control domain')
                    for key in ('before','after'):
                        if event.get(key) is not None and not isinstance(event[key],dict):raise RecordingError('Invalid configuration snapshot')
                    findings=event.get('findings',[])
                    if not isinstance(findings,list) or any(not isinstance(f,dict) or not isinstance(f.get('rule_id'),str) or type(f.get('rule_version')) is not int or not isinstance(f.get('title'),str) for f in findings):
                        raise RecordingError('Invalid captured findings')
                    if len(self.events) < 100000:
                        self.events.append(row['event'])
                    else:
                        raise RecordingError('Too many recorded events.')
                else:
                    raise RecordingError(f'Unknown record at line {line_number}.')
            if not self.offsets:
                raise RecordingError('Recording contains no samples.')
            current=os.fstat(self.file.fileno())
            if self.identity!=(current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns):
                raise RecordingError('Recording changed during import; retry after recording completes')
        except Exception:
            self.file.close()
            raise

    def sample_at(self, index):
        info=os.fstat(self.file.fileno())
        if (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=self.identity:
            raise RecordingError('Recording changed after import; reopen it after recording completes')
        self.file.seek(self.offsets[index])
        line=self.file.readline(MAX_LINE+1)
        if hashlib.blake2s(line,digest_size=16).digest()!=self.digests[index]:raise RecordingError('Recorded sample content changed after import')
        return decode(line)['sample']

    def window(self, index, max_samples=120, max_bytes=16*1024*1024):
        self.index = max(0, min(len(self.offsets) - 1, index))
        start=self.index;size=self.sizes[start]
        while start>0 and self.index-start+1<max_samples and size+self.sizes[start-1]<=max_bytes:
            start-=1;size+=self.sizes[start]
        self.window_truncated=start>max(0,self.index-max_samples+1)
        return [self.sample_at(i) for i in range(start, self.index + 1)]

    def close(self):
        self.file.close()
