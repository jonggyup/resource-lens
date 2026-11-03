"""Scoped pressure and memory-counter findings from recorded source observations."""
from collections import deque
import json

from .evidence import finite
from .model import TargetRef, source_quality


class ResourceHistory:
    def __init__(self,max_frames=120,max_bytes=2*1024*1024):
        self.frames=deque();self.sizes=deque();self.bytes=0
        self.max_frames,self.max_bytes=max_frames,max_bytes
        self.signature=None

    def clear(self):
        self.frames.clear();self.sizes.clear();self.bytes=0;self.signature=None

    def observe(self,sample):
        frame=project(sample)
        signature=(frame['scope'],frame['request'],frame['stamp']) if frame else ('missing',)
        if signature==self.signature:return
        self.signature=signature
        frame=frame or dict(nodes=[],request=None)
        size=len(json.dumps(frame,allow_nan=False).encode())
        self.frames.append(frame);self.sizes.append(size);self.bytes+=size
        while self.frames and (len(self.frames)>self.max_frames or self.bytes>self.max_bytes):
            self.frames.popleft();self.bytes-=self.sizes.popleft()


def project(sample):
    try:
        context=sample['target_context'];quality=source_quality(sample,'target_context')
        target=TargetRef.from_dict(context['target']);group=TargetRef.from_dict(context['selected_group'])
        scope=sample['collection_scope']['id']
        if quality.status not in ('ok','partial') or quality.scope_id!=scope or any(ref.identity.scope_id!=scope for ref in (target,group)):
            return None
        stamp,wall=context['observed_at_monotonic'],context['observed_at_wall']
        if not all(finite(v) for v in (stamp,wall,quality.observed_at_wall)):return None
        nodes=[]
        for source in context['ancestors'][:32]:
            ref=TargetRef.from_dict(source['identity'])
            if ref.kind!='cgroup' or ref.identity.scope_id!=scope or ref.identity.path!=source['path']:return None
            row=dict(identity=ref.to_dict(),path=source['path'],end=source.get('observed_at_monotonic',stamp),
                     interval=source.get('interval_s'),pressure={},memory={},delta={},reasons={},
                     memory_scope=source.get('memory_events_scope','unknown'))
            for resource in ('cpu','memory','io'):
                filename=resource+'.pressure'
                values=(source.get('counters',{}).get(filename) or {}).get('some',{})
                value=values.get('avg10') if isinstance(values,dict) else None
                reason=source.get('files',{}).get(filename,{}).get('reason','not_recorded')
                row['pressure'][resource]=value if not reason and finite(value) and 0<=value<=100 else None
            for filename in ('memory.events.local','memory.events'):
                values=source.get('counters',{}).get(filename) or {}
                deltas=source.get('deltas',{}).get(filename) or {}
                row['memory'][filename]={k:values.get(k) for k in ('high','max','oom','oom_kill')}
                row['delta'][filename]={k:deltas.get(k) for k in ('high','max','oom','oom_kill')}
                row['reasons'][filename]=source.get('files',{}).get(filename,{}).get('reason','not_recorded')
            nodes.append(row)
        if not nodes or TargetRef.from_dict(nodes[0]['identity'])!=group:return None
        return dict(request=tuple(context['request']),target=target.to_dict(),group=group.to_dict(),
                    scope=scope,stamp=stamp,wall=min(wall,quality.observed_at_wall),nodes=nodes)
    except (KeyError,TypeError,ValueError,AttributeError,OverflowError):return None


def pressure_run(history,request,group,node,resource,threshold):
    run=[]
    for frame in history.frames:
        if request[0]=='process' and (frame.get('request')!=tuple(request) or frame.get('group')!=group):
            run=[];continue
        row=next((n for n in frame['nodes'] if n['identity']==node['identity']),None)
        if row is None:run=[];continue
        value,interval,end=row['pressure'][resource],row['interval'],row['end']
        if not finite(value) or value<threshold or not finite(interval) or not 0<interval<=2.5 or not finite(end):
            run=[];continue
        if run and (not 0<end-run[-1]['end']<=2.5 or abs(end-interval-run[-1]['end'])>.05):run=[]
        run.append(dict(value=value,interval=interval,end=end,wall=frame['wall']))
    return run


def explain(history,sample,request,now=None,stale_after=5,psi_threshold=20,duration=3):
    from .diagnostics import context_for,request_current,finding
    context=context_for(sample,request)
    if context is None or not request_current(sample,request):return []
    frame=project(dict(sample,target_context=context))
    reference=sample['timestamp'] if now is None else now
    if not frame or not -.1<=reference-frame['wall']<=stale_after:return []
    results=[]
    # Keep one readable ancestor in addition to the exact selected group. Parent
    # activity is shared evidence, never a measurement of this process's delay.
    for index,node in enumerate(frame['nodes'][:2]):
        local=index==0
        relevance='observed' if local else 'possible'
        for resource in ('cpu','memory','io'):
            if not finite(node['pressure'][resource]) or node['pressure'][resource]<psi_threshold:continue
            run=pressure_run(history,request,frame['group'],node,resource,psi_threshold)
            seconds=sum(v['interval'] for v in run)
            if len(run)<3 or seconds<duration or run[-1]['end']!=node['end']:continue
            evidence=tuple(dict(path=node['path'],file=resource+'.pressure',counter='some.avg10',
                                value=v['value'],interval_s=v['interval'],interval_start=v['end']-v['interval'],
                                interval_end=v['end'],scope=frame['scope'],source_target=node['identity']) for v in run)
            results.append(finding(('local_' if local else 'ancestor_')+resource+'_pressure',frame['target'],
                ('Group ' if local else 'Ancestor ')+resource.upper()+' pressure persists',
                f"{node['path']}: PSI some.avg10 stayed at or above {psi_threshold:g}% across {len(run)} recorded intervals.",
                relevance,evidence=evidence,window=dict(start=evidence[0]['interval_start'],end=evidence[-1]['interval_end'],
                    intervals=len(run),covered_seconds=seconds,mean_psi_pct=sum(v['value']*v['interval'] for v in run)/seconds,
                    threshold=psi_threshold,unit='PSI some.avg10 percent'),
                limitations=('PSI is a smoothed group-level observation, not per-process or request latency.',
                             'Ancestor pressure includes sibling workloads and does not assign their delays to this target.',
                             'Pressure alone does not identify the cause or establish a configured limit is active.'),
                actions=('show_evidence','show_constraints','waits','health'),origin=node['identity']))
        filename='memory.events.local' if not node['reasons']['memory.events.local'] else 'memory.events'
        scope='local' if filename.endswith('.local') else node['memory_scope']
        if node['reasons'][filename] or scope not in ('local','subtree'):continue
        interval,end=node['interval'],node['end']
        if not finite(interval) or interval<=0 or not finite(end):continue
        for counter in ('oom_kill','oom','high','max'):
            total=node['memory'][filename][counter];delta=node['delta'][filename][counter]
            if type(total) is not int or type(delta) is not int or not 0<delta<=total:continue
            results.append(finding(('local_' if local else 'ancestor_')+'memory_'+counter,frame['target'],
                ('Group ' if local else 'Ancestor ')+f'memory {counter} events increased',
                f"{node['path']}: {counter} increased by {delta} in {interval:.2f}s ({scope} accounting).",
                relevance,evidence=(dict(path=node['path'],file=filename,counter=counter,before=total-delta,after=total,
                    delta=delta,interval_s=interval,interval_start=end-interval,interval_end=end,scope=frame['scope'],accounting_scope=scope,source_target=node['identity']),),
                window=dict(start=end-interval,end=end,covered_seconds=interval,intervals=1,events_delta=delta,unit='events'),
                limitations=('This is a recorded counter increase, not proof that pressure is still ongoing.',
                             'Group/subtree counters do not identify a responsible PID or request; parent/child counts must not be added.'),
                actions=('show_evidence','show_constraints','memory','health'),origin=node['identity']))
    return results[:12]
