"""Saved artifact discovery, bounded import and private supplementary outcomes."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile

from .evidence import file_identity,load_profile,profile_runs,read_json,finite
from .recording import Replay


MAX_ENTRIES=300
MAX_SCAN=2000


def atomic_json(path,value,limit=65536):
    text=json.dumps(value,allow_nan=False,indent=2)+'\n'
    if len(text.encode())>limit:raise ValueError('Supplementary metadata exceeds 64 KiB budget; original evidence was retained')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,temporary=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as file:
            file.write(text);file.flush();os.fsync(file.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def roots(state):
    state=Path(state)
    result=[state/'profiles',state/'recordings',state/'incidents']
    manifest=state/'session-paths.json'
    if manifest.exists():
        saved=read_json(manifest,limit=65536)
        if not isinstance(saved,dict) or saved.get('schema')!=1 or not isinstance(saved.get('paths'),list) or len(saved['paths'])>100 or any(not isinstance(p,str) for p in saved['paths']):
            raise ValueError('Invalid saved Sessions paths; import paths explicitly')
        result.extend(Path(p) for p in saved['paths'])
    return result


def remember(state,path):
    state=Path(state);manifest=state/'session-paths.json'
    saved=read_json(manifest,limit=65536) if manifest.exists() else dict(schema=1,paths=[])
    paths=saved.get('paths')
    if not isinstance(paths,list) or len(paths)>100 or any(not isinstance(p,str) for p in paths):raise ValueError('Invalid Sessions path index')
    path=str(Path(path).absolute())
    if path not in paths:
        if len(paths)>=100:raise ValueError('Saved path budget reached (100); existing evidence was retained')
        atomic_json(manifest,dict(schema=1,paths=paths+[path]))


def entry(path,fingerprint=True):
    path=Path(path).absolute()
    if path.is_symlink():raise ValueError('Import the regular evidence path rather than a symlink')
    if path.is_dir():
        if not (path/'session.json').exists():return dict(path=str(path),kind='profile_set',name=path.name,identity=None,bytes=None)
        path=path/'session.json'
    identity=file_identity(path)
    if identity[2]>(32*1024*1024 if path.name=='session.json' else 256*1024*1024):raise ValueError('Saved artifact exceeds import size budget')
    kind='profile' if path.name=='session.json' else 'recording'
    digest=read_json(path,with_digest=True)[1] if kind=='profile' and fingerprint else None
    return dict(path=str(path),kind=kind,name=path.parent.name if kind=='profile' else path.name,
                identity=list(identity),bytes=identity[2],digest=digest)


def discover(state,extra=()):
    entries=[];warnings=[];seen=set();scanned=0
    try:locations=roots(state)
    except (OSError,ValueError) as exc:
        locations=[Path(state)/name for name in ('profiles','recordings','incidents')];warnings.append(str(exc))
    locations.extend(Path(p) for p in extra)
    def add(path):
        if len(entries)>=MAX_ENTRIES:return
        try:row=entry(path,fingerprint=False)
        except (OSError,ValueError) as exc:warnings.append(str(exc));return
        if row['path'] not in seen:seen.add(row['path']);entries.append(row)
    for path in dict.fromkeys(locations):
        if len(entries)>=MAX_ENTRIES or scanned>=MAX_SCAN:
            warnings.append('Sessions scan budget reached; import a narrower path. No files were removed.');break
        if not path.exists():continue
        if path.is_symlink():warnings.append('Skipped symlink: '+str(path));continue
        if not path.is_dir() or (path/'session.json').is_file():add(path);continue
        has_profiles=False
        try:
            with os.scandir(path) as children:
                for child in children:
                    scanned+=1
                    if scanned>MAX_SCAN or len(entries)>=MAX_ENTRIES:break
                    candidate=Path(child.path)
                    if child.is_dir(follow_symlinks=False) and (candidate/'session.json').is_file():
                        add(candidate);has_profiles=True
                    elif child.is_file(follow_symlinks=False) and candidate.suffix=='.jsonl':add(candidate)
            if has_profiles and path not in (Path(state)/'profiles',):add(path)
        except OSError as exc:warnings.append(str(exc))
    if scanned>=MAX_SCAN or len(entries)>=MAX_ENTRIES:warnings.append('Scan truncated at budget; import a narrower path. No evidence was removed.')
    return dict(entries=sorted(entries,key=lambda e:e['path']),warnings=warnings[:20],scanned=scanned)


def open_entry(row):
    if row['kind']=='recording':
        replay=Replay(row['path'])
        if row.get('identity') is not None and tuple(row['identity'])!=replay.identity:
            replay.close();raise ValueError('Recording changed since selection; refresh Sessions')
        return replay
    if row['kind']=='profile':return load_profile(row['path'],expected=row.get('identity'),expected_digest=row.get('digest'))
    return profile_runs(row['path'])


def import_path(path,state):
    row=entry(Path(path).expanduser())
    evidence=open_entry(row)
    if isinstance(evidence,Replay):evidence.close()
    remember(state,path)
    return row


def add_outcome(row,text):
    fields=[part.strip() for part in text.split('|')]
    if len(fields)!=4 or any(not field for field in fields):raise ValueError('Expected name | unit | value | measurement source')
    name,unit,raw,provenance=fields
    value=float(raw)
    if not finite(value) or max(len(name),len(unit))>64 or len(provenance)>512:raise ValueError('Outcome value/label exceeds limits')
    if row['kind']!='profile':raise ValueError('Add outcomes to one complete profile')
    record=open_entry(row)
    if record['status']!='complete':raise ValueError('Partial/cancelled profiles remain incomplete; outcomes require a complete capture')
    started,elapsed=record.get('started'),record.get('elapsed_seconds')
    if not finite(started) or not finite(elapsed) or elapsed<=0:raise ValueError('No valid captured window for this outcome')
    metadata=dict(record.get('outcome_metadata',{}))
    metadata[name]=dict(unit=unit,value=value,provenance=provenance,window=[started,started+elapsed])
    path=Path(row['path'])
    atomic_json(path.parent/'outcomes.json',dict(schema=1,profile_identity=list(file_identity(path)),profile_digest=record['_artifact_digest'],outcomes=metadata))
    return load_profile(path)


def presentation_sample(original):
    """Supply absent legacy presentation fields as unknown; keep raw originals."""
    sample=deepcopy(original)
    defaults=dict(hostname='recorded host (not supplied)',cpu_model='recorded CPU',processes_skipped=0,
                  cpu_hardware={'cpus':[],'policies':{}},workloads=[],thermal_throttling=[],root_filesystem={},
                  cgroup_tree={'nodes':[]},resource_topology={},energy={})
    for key,value in defaults.items():sample.setdefault(key,value)
    for key in ('total','used','available','cache','buffers','swap_total','swap_used'):sample['memory'].setdefault(key,None)
    for row in sample['cpu']:
        for key in ('busy_pct','iowait_pct','steal_pct','frequency_mhz'):row.setdefault(key,None)
    fields={'disks':('active_pct','read_bps','write_bps'),'network':('ifindex','rx_bps','tx_bps'),
            'gpus':('busy_pct','memory_used','memory_total','power_w','temperature_c'),
            'processes':('cpu_pct','memory_pct','memory','nice','starttime','ppid','cgroup'),
            'workloads':('inode','device','cpu_pct','memory_current')}
    for kind,names in fields.items():
        for row in sample[kind]:
            for key in names:row.setdefault(key,None)
            if kind=='processes':row.setdefault('state','?');row.setdefault('name','recorded process')
            if kind=='workloads':
                row.setdefault('path',row['id']);row.setdefault('unit',None);row.setdefault('pids',[]);row.setdefault('controllable',False)
    return sample


def profile_lines(record):
    target=record['target'];samples=record.get('samples',[])
    span=samples[-1]['elapsed']-samples[0]['elapsed'] if len(samples)>1 else 0
    requested=record.get('requested_seconds')
    coverage=min(1,span/requested) if requested else None
    lines=['SAVED PROFILE - offline', 'Label: '+record['label'], 'Target: '+str(target),
           'Capture: '+record['status']+' | CPU stacks: '+record['profile']['status'],
           f'Counter coverage: {len(samples)} observations, {span:.2f}s / '+(f'{requested:g}s requested' if requested else 'unknown duration'),
           'Coverage: '+(f'{coverage*100:.0f}%'+(' incomplete' if coverage<.8 else '') if coverage is not None else 'unknown'),
           'Wait tracing: '+record.get('scheduler',{}).get('status','not recorded'),
           'Hardware energy: '+record.get('energy',{}).get('status','not recorded'),
           'Shared hardware energy is not process-attributed. Resource changes do not establish latency changes.','', '-- Resource measurements --']
    lines.extend(f'{name}: {value:.4g}' for name,value in sorted(record['metrics'].items()))
    lines+=['','-- Measured application outcomes --']
    for name,value in record.get('outcomes',{}).items():
        metadata=record.get('outcome_metadata',{}).get(name,{})
        lines.append(f"{name}: {value:g} {metadata.get('unit','(legacy: units embedded in name)')} | source: {metadata.get('provenance','not recorded')}")
        if metadata.get('window'):lines.append('Measured window: '+str(metadata['window']))
    if not record.get('outcomes'):lines.append('No application outcomes supplied; resource metrics alone do not measure speedup.')
    lines+=['',*record.get('warnings',[]),'', 'o Add outcome | b Baseline | c Compare | x Export | Esc back']
    captured=record.get('capture_context',{}).get('findings',[])
    if captured:
        lines+=['','-- Captured interpretation (original rules) --']
        for finding in captured:
            lines.append(str(finding.get('title','Finding'))+f" | rule {finding.get('rule_id')} / version {finding.get('rule_version')}")
    return lines
