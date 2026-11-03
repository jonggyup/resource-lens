"""Bounded local evidence decoding. Imported data never selects a live backend."""
import json
import math
import os
from pathlib import Path
import stat
import hashlib


MAX_JSON = 32 * 1024 * 1024


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_tree(value, depth=0, budget=None):
    budget = [500000] if budget is None else budget
    budget[0] -= 1
    if budget[0] < 0 or depth > 32:
        raise ValueError('Evidence exceeds nesting/item budget')
    if isinstance(value, dict):
        if any(not isinstance(k,str) or len(k)>4096 for k in value):
            raise ValueError('Invalid evidence key')
        for item in value.values():validate_tree(item,depth+1,budget)
    elif isinstance(value,list):
        for item in value:validate_tree(item,depth+1,budget)
    elif isinstance(value,str):
        if len(value)>262144:raise ValueError('Evidence string exceeds size limit')
    elif type(value) in (int,float):
        if not finite(value):raise ValueError('Evidence contains a nonfinite number')
    elif value is not None and type(value) is not bool:
        raise ValueError('Invalid evidence value')
    return value


def decode(text):
    def reject(value):raise ValueError('Nonfinite JSON number: '+value)
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate evidence key: '+key)
            result[key]=value
        return result
    try:return validate_tree(json.loads(text,parse_constant=reject,object_pairs_hook=unique))
    except (RecursionError,OverflowError) as exc:raise ValueError('Evidence exceeds numeric/nesting budget') from exc


def file_identity(path):
    info=Path(path).lstat()
    if not stat.S_ISREG(info.st_mode):raise ValueError('Evidence must be a regular file, not a symlink or device')
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)


def read_json(path, limit=MAX_JSON, expected=None,expected_digest=None,with_digest=False):
    path=Path(path)
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW),'rb') as file:
        info=os.fstat(file.fileno())
        identity=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
        if not stat.S_ISREG(info.st_mode) or info.st_size>limit:
            raise ValueError('Evidence is not a regular file within the size budget')
        if expected is not None and tuple(expected)!=identity:raise ValueError('Evidence changed since selection; refresh the list')
        data=file.read(limit+1)
        if len(data)>limit:raise ValueError('Evidence exceeds size budget')
        after=os.fstat(file.fileno())
        if identity!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
            raise ValueError('Evidence changed while being read; retry after capture completes')
    digest=hashlib.sha256(data).hexdigest()
    if expected_digest is not None and digest!=expected_digest:raise ValueError('Evidence content changed since selection; refresh the list')
    value=decode(data)
    return (value,digest) if with_digest else value


def validate_profile(record):
    validate_tree(record)
    if not isinstance(record,dict) or type(record.get('schema')) is not int or record['schema']!=1:raise ValueError('Unsupported profile schema')
    for key,kind in (('target',dict),('metrics',dict),('environment',dict),('profile',dict),('label',str)):
        if not isinstance(record.get(key),kind):raise ValueError('Invalid profile field: '+key)
    if record.get('status') not in ('complete','partial','cancelled','collecting'):
        raise ValueError('Invalid profile status')
    target=record['target'];pid,group=target.get('pid'),target.get('cgroup')
    if (pid is None)==(group is None) or pid is not None and (type(pid) is not int or pid<=0) or group is not None and (not isinstance(group,str) or not group.startswith('/')):
        raise ValueError('Profile requires one recorded PID or cgroup scope')
    if not isinstance(record['profile'].get('status'),str):raise ValueError('Invalid CPU profile status')
    if any(not finite(v) for v in record['metrics'].values()):raise ValueError('Profile metrics must be finite numbers')
    if len(record['metrics'])>4096:raise ValueError('Profile metric budget exceeded')
    outcomes=record.get('outcomes',{})
    if not isinstance(outcomes,dict) or any(not finite(v) for v in outcomes.values()):raise ValueError('Invalid workload outcomes')
    for key in ('requested_seconds','elapsed_seconds','started'):
        if key in record and (not finite(record[key]) or record[key]<0):raise ValueError('Invalid profile duration/timestamp')
    samples=record.get('samples',[])
    if not isinstance(samples,list) or len(samples)>100000:raise ValueError('Invalid profile observation count')
    previous=-1
    for item in samples:
        if not isinstance(item,dict) or not finite(item.get('elapsed')) or item['elapsed']<previous:
            raise ValueError('Invalid profile observation interval')
        previous=item['elapsed']
    if not isinstance(record.get('warnings',[]),list) or any(not isinstance(v,str) for v in record.get('warnings',[])):
        raise ValueError('Invalid profile warnings')
    for key in ('energy','scheduler'):
        if key in record and not isinstance(record[key],dict):raise ValueError('Invalid '+key+' evidence')
        if key in record and not isinstance(record[key].get('status'),str):raise ValueError('Invalid '+key+' status')
    domains=record.get('energy',{}).get('domains',[])
    if not isinstance(domains,list) or any(not isinstance(d,dict) or any(not isinstance(d.get(k),str) for k in ('id','label','source')) for d in domains):
        raise ValueError('Invalid energy domain identity')
    metadata=record.get('outcome_metadata',{})
    if not isinstance(metadata,dict) or any(not isinstance(m,dict) or not isinstance(m.get('unit'),str) or not isinstance(m.get('provenance'),str) for m in metadata.values()):
        raise ValueError('Invalid measured outcome units/provenance')
    return record


def load_profile(path, expected=None,expected_digest=None):
    path=Path(path)
    file=path/'session.json' if path.is_dir() else path
    record,digest=read_json(file,expected=expected,expected_digest=expected_digest,with_digest=True)
    record=dict(validate_profile(record),_artifact_digest=digest)
    captured=file.parent/'capture-context.json'
    if captured.exists():
        context=read_json(captured,limit=2*1024*1024)
        if not isinstance(context,dict) or context.get('schema')!=1 or not isinstance(context.get('findings',[]),list) or any(not isinstance(f,dict) for f in context.get('findings',[])):
            raise ValueError('Invalid captured interpretation sidecar')
        record=dict(record,capture_context=context)
    sidecar=file.parent/'outcomes.json'
    if sidecar.exists():
        values=read_json(sidecar,limit=65536)
        if not isinstance(values,dict) or values.get('schema')!=1 or not isinstance(values.get('outcomes'),dict):
            raise ValueError('Invalid outcome sidecar')
        if values.get('profile_identity') != list(file_identity(file)) or values.get('profile_digest')!=digest:
            raise ValueError('Outcome sidecar belongs to a changed profile; re-enter the outcome')
        metadata=values['outcomes']
        for name,item in metadata.items():
            if not isinstance(item,dict) or not finite(item.get('value')) or any(not isinstance(item.get(k),str) or not item[k].strip() for k in ('unit','provenance')):
                raise ValueError('Outcome requires a value, unit and provenance')
            window=item.get('window')
            if not isinstance(window,list) or len(window)!=2 or not all(finite(v) for v in window) or window[1]<window[0]:
                raise ValueError('Invalid measured outcome window')
        record=dict(record,outcomes=dict(record.get('outcomes',{}),**{k:v['value'] for k,v in metadata.items()}),outcome_metadata=metadata)
    return record


def profile_runs(path):
    path=Path(path)
    if path.is_file():return [load_profile(path)]
    if (path/'session.json').is_file():return [load_profile(path)]
    files=[]
    with os.scandir(path) as entries:
        for index,entry in enumerate(entries):
            if index>=2000:raise ValueError('Repeat directory exceeds scan budget')
            if entry.is_dir(follow_symlinks=False) and (Path(entry.path)/'session.json').is_file():
                files.append(Path(entry.path)/'session.json')
                if len(files)>1000:raise ValueError('Expected at most 1000 saved sessions per side')
    if not files:raise ValueError('No saved profiles found in this directory')
    if sum(file_identity(file)[2] for file in files)>64*1024*1024:raise ValueError('Repeat set exceeds 64 MiB read budget')
    return [load_profile(file) for file in sorted(files)]
