"""Recorded cgroup memory accounting and boundaries, distinct from process RSS/PSS."""
import math


BYTE_FILES=('memory.current','memory.swap.current','memory.peak')
STAT_BYTES=('anon','file','kernel','slab','shmem','file_mapped','sock','pagetables','swapcached')


def number(value):
    return type(value) is int and value>=0


def scalar(node,name):
    row=node.get('files',{}).get(name,{})
    value=node.get('counters',{}).get(name)
    if row.get('reason'):return dict(state='unavailable',value=None,reason=row['reason'])
    if number(value):return dict(state='known',value=value,reason=None)
    raw=row.get('value')
    if isinstance(raw,str) and raw.isdigit():return dict(state='known',value=int(raw),reason=None)
    return dict(state='unavailable',value=None,reason='parse_error' if raw is not None else 'not_recorded')


def boundary(node,name):
    row=node.get('files',{}).get(name,{})
    if row.get('reason'):return dict(state='unavailable',value=None,reason=row['reason'])
    raw=row.get('value')
    if raw=='max':return dict(state='unlimited',value=None,reason=None)
    if isinstance(raw,str) and raw.isdigit():return dict(state='known',value=int(raw),reason=None)
    return dict(state='unavailable',value=None,reason='parse_error' if raw is not None else 'not_recorded')


def display(row):
    if row['state']=='known':
        from .ui import size
        return size(row['value'])+f" ({row['value']} bytes)"
    if row['state']=='unlimited':return 'max (explicitly unlimited at this group)'
    return 'unknown ('+(row.get('reason') or 'not_recorded').replace('_',' ')+')'


def memory_lines(context,now=None):
    nodes=(context or {}).get('ancestors',[])
    if not nodes:return ['CGROUP MEMORY','Exact memory accounting was not recorded. V separately inspects live process RSS/PSS.']
    node=nodes[0]
    lines=['CGROUP MEMORY / '+node['path'],
           'Includes this group and descendants; these values are not process RSS or PSS.']
    wall=node.get('observed_at_wall',context.get('observed_at_wall'))
    if type(wall) in (int,float) and math.isfinite(wall):
        lines.append(f'Source observed at: {wall:.3f} wall-clock seconds')
        if now is not None and type(now) in (int,float) and math.isfinite(now):
            age=now-wall
            lines.append('Observation age: '+(f'{age:.2f}s'+(' (stale)' if age>5 else '') if age>=0 else 'unknown (clock moved backwards)'))
    for name in BYTE_FILES:lines.append(name+': '+display(scalar(node,name)))
    lines.append('memory.peak is the cgroup-reported peak, not a maximum for this observation window.')
    for name in ('memory.high','memory.max','memory.swap.max','memory.low','memory.min'):
        lines.append(name+': '+display(boundary(node,name)))
    lines += ['MemoryHigh is a reclaim/throttling boundary; usage can exceed it.',
              'MemoryMax is a separate hard boundary with possible OOM effects.',
              '', '-- memory.stat: recorded categories --']
    stat=node.get('counters',{}).get('memory.stat') or {}
    reason=node.get('files',{}).get('memory.stat',{}).get('reason')
    for name in STAT_BYTES:
        value=stat.get(name) if isinstance(stat,dict) and not reason else None
        lines.append(name+': '+display(dict(state='known' if number(value) else 'unavailable',value=value,reason=reason or 'not_recorded')))
    lines.append('Categories overlap (for example slab within kernel, shmem within file); do not add them as a total.')
    lines += ['', '-- Memory pressure and events --']
    pressure=node.get('counters',{}).get('memory.pressure') or {}
    reason=node.get('files',{}).get('memory.pressure',{}).get('reason')
    for kind in ('some','full'):
        value=(pressure.get(kind) or {}).get('avg10') if isinstance(pressure,dict) else None
        known=not reason and type(value) in (int,float) and math.isfinite(value) and 0<=value<=100
        lines.append('memory.pressure '+kind+'.avg10: '+(f'{value:.2f}% PSI' if known else 'unknown ('+(reason or 'not_recorded')+')'))
    for filename,scope in (('memory.events.local','local'),('memory.events',node.get('memory_events_scope','unknown'))):
        values=node.get('counters',{}).get(filename) or {}
        deltas=node.get('deltas',{}).get(filename) or {}
        reason=node.get('files',{}).get(filename,{}).get('reason')
        interval=node.get('interval_s')
        lines.append(filename+' ['+scope+' accounting]')
        for name in ('high','max','oom','oom_kill'):
            total=values.get(name) if isinstance(values,dict) and not reason else None
            delta=deltas.get(name) if isinstance(deltas,dict) and not reason else None
            valid=number(total) and number(delta) and delta<=total and type(interval) in (int,float) and math.isfinite(interval) and interval>0
            lines.append('  '+name+': '+(str(total)+' cumulative' if number(total) else 'unknown')+
                         (f'; +{delta} in {interval:.3f}s' if valid else '; interval unavailable (warmup, reset or missing data)'))
    lines += ['PSI and group counters do not measure request latency or identify a responsible PID.',
              '', '-- Readable ancestor boundaries (shared; never additive) --']
    for ancestor in nodes[1:]:
        lines.append(ancestor['path'])
        for name in ('memory.high','memory.max','memory.swap.max'):lines.append('  '+name+': '+display(boundary(ancestor,name)))
    lines.append('Ancestry coverage: '+context.get('ancestry_visibility','unknown')+'. Hidden limits remain unknown.')
    return lines
