"""One runtime MemoryHigh control, with fresh identity/configuration checks."""
from copy import deepcopy
import os
import re
import time
import uuid

from .controls import ControlError,SystemdBackend
from .sources import read_text
from .targets import parse_limit
from .workloads import WorkloadCollector


MAX_BYTES=2**63-1
INFINITY=2**64-1
TARGET_FIELDS=('unit','path','inode','device','manager','controllable','owner_uid')


def memory_value(text,page_size):
    if type(page_size) is not int or page_size<=0:raise ControlError('Cannot determine the system page size')
    raw=str(text).strip().lower()
    if raw in ('max','infinity','unlimited'):return None,None
    match=re.fullmatch(r'([1-9][0-9]*)([kmgt]?)(?:i?b)?',raw)
    if not match:raise ControlError('Use positive whole bytes, K/M/G/T (base 1024), or max; percentages/default are not supported')
    requested=int(match[1])*1024**('kmgt'.index(match[2])+1 if match[2] else 0)
    value=((requested+page_size-1)//page_size)*page_size
    if value>MAX_BYTES:raise ControlError('MemoryHigh exceeds the supported byte range')
    return value,requested


def manager_limit(raw):
    if raw in ('infinity','max',str(INFINITY)):return None
    if not isinstance(raw,str) or not raw.isdigit() or int(raw)>MAX_BYTES:
        raise ControlError('MemoryHigh/MemoryMax is unavailable or cannot be safely restored')
    return int(raw)


def kernel_matches(configured,observed,page_size):
    if configured is None or observed is None:return configured is observed
    # Historical kernels/systemd versions can expose a page-rounded old setting.
    # New requests are page-aligned; restoration verifies the captured raw value.
    return observed in (configured//page_size*page_size,(configured+page_size-1)//page_size*page_size)


class MemoryBackend(SystemdBackend):
    def __init__(self,*args,page_size=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.page_size=os.sysconf('SC_PAGE_SIZE') if page_size is None else page_size

    def show(self,target):
        output=self.command(target,'show','--no-pager',
            '--property=Id,ControlGroup,InvocationID,ActiveState,MemoryHigh,MemoryMax,MemorySwapMax,StartupMemoryHigh,StartupMemoryMax,EffectiveMemoryHigh,EffectiveMemoryMax',
            '--',target['unit'])
        values=dict(line.split('=',1) for line in output.splitlines() if '=' in line)
        if values.get('Id')!=target['unit'] or values.get('ControlGroup')!=target['path'] or values.get('ActiveState')!='active' or not values.get('InvocationID'):
            raise ControlError('Select an active matching service/scope with an identifiable invocation')
        return values

    @staticmethod
    def read_boundary(directory,name,required=False):
        result=read_text(directory/name,limit=4096)
        value=parse_limit(name,result)
        if required and value['state'] not in ('known','unlimited'):
            raise ControlError(name+' is not readable/valid ('+(value.get('reason') or 'unknown')+')')
        return dict(value,raw=result.text)

    def snapshot(self,target):
        if not target.get('controllable'):raise ControlError('Select a controllable service/scope through its owning manager')
        directory=WorkloadCollector(self.root).directory(target['path'])
        if directory is None:raise ControlError('The selected cgroup disappeared or resolves outside the configured root')
        identity=directory.stat()
        if identity.st_ino!=target.get('inode') or target.get('device') is not None and identity.st_dev!=target['device']:
            raise ControlError('The cgroup was replaced; select the service again')
        if 'memory' not in (read_text(self.root/'cgroup.controllers',limit=4096).text or '').split():
            raise ControlError('The cgroup v2 memory controller is unavailable')
        values=self.show(target)
        high=manager_limit(values.get('MemoryHigh'));maximum=manager_limit(values.get('MemoryMax'))
        own_high=self.read_boundary(directory,'memory.high',True)
        own_max=self.read_boundary(directory,'memory.max',True)
        current=read_text(directory/'memory.current',limit=4096)
        if current.reason or not isinstance(current.text,str) or not current.text.isdigit():
            raise ControlError('Current group memory usage is unavailable; a meaningful MemoryHigh preview cannot be prepared')
        observations=dict(memory_current=int(current.text))
        for filename in ('memory.swap.current','memory.peak'):
            result=read_text(directory/filename,limit=4096)
            observations[filename]=int(result.text) if not result.reason and isinstance(result.text,str) and result.text.isdigit() else None
        ancestors=[];cursor=directory.parent;started=time.monotonic();complete=True
        root=self.root.resolve()
        while cursor.is_relative_to(root) and cursor!=directory:
            if len(ancestors)>=32 or time.monotonic()-started>.15:complete=False;break
            info=cursor.stat()
            row=dict(path='/'+str(cursor.relative_to(root)) if cursor!=root else '/',inode=info.st_ino,device=info.st_dev)
            for filename in ('memory.high','memory.max','memory.swap.max'):row[filename]=self.read_boundary(cursor,filename)
            after=cursor.stat()
            if (info.st_dev,info.st_ino)!=(after.st_dev,after.st_ino):raise ControlError('An ancestor cgroup changed during the memory preview')
            ancestors.append(row)
            if cursor==root:break
            cursor=cursor.parent
        final=directory.stat();checked=self.show(target)
        if (final.st_dev,final.st_ino)!=(identity.st_dev,identity.st_ino) or checked!=values:
            raise ControlError('The unit or configuration changed during memory inspection; preview again')
        return dict(path=target['path'],inode=identity.st_ino,device=identity.st_dev,invocation=values['InvocationID'],
                    page_size=self.page_size,memory_high=high,memory_max=maximum,
                    kernel_high=own_high['value'],kernel_max=own_max['value'],
                    manager=values,swap_max=self.read_boundary(directory,'memory.swap.max'),
                    ancestors=ancestors,ancestor_scan_complete=complete,ancestry_visibility='to_configured_root',
                    observations=observations)

    def set_high(self,target,value):
        if value is not None and (type(value) is not int or not 0<=value<=MAX_BYTES):raise ControlError('Invalid MemoryHigh byte value')
        self.command(target,'--runtime','set-property','--',target['unit'],
                     'MemoryHigh='+('infinity' if value is None else str(value)))


class MemoryHighSession:
    def __init__(self,backend=None):
        self.backend=backend or MemoryBackend()
        self.events=[]

    @staticmethod
    def identity(info):
        return tuple(info[key] for key in ('path','inode','device','invocation'))

    @staticmethod
    def guards(info):
        return {key:info[key] for key in ('path','inode','device','invocation','page_size','memory_high','memory_max',
                                        'kernel_high','kernel_max','manager','swap_max','ancestors','ancestor_scan_complete','ancestry_visibility')}

    @staticmethod
    def agreement(info):
        return all(kernel_matches(info[key],info[native],info['page_size']) for key,native in (('memory_high','kernel_high'),('memory_max','kernel_max')))

    def prepare(self,target,value):
        target={key:target.get(key) for key in TARGET_FIELDS}
        before=self.backend.snapshot(target)
        if not self.agreement(before):raise ControlError('systemd and kernel memory settings disagree; refusing untracked configuration')
        value,requested=memory_value(value,before['page_size'])
        if before['memory_high']==value and before['kernel_high']==value:raise ControlError('MemoryHigh already has this value; no change requested')
        return dict(target=target,property='memory_high',value=value,requested_bytes=requested,before=before,
                    below_usage=value is not None and value<before['observations']['memory_current'])

    def verify(self,target,before,value,restoring=False):
        deadline=time.monotonic()+1
        while True:
            current=self.backend.snapshot(target)
            if self.identity(current)!=self.identity(before):raise ControlError('The unit restarted or cgroup changed during MemoryHigh verification')
            stable={key:value for key,value in self.guards(current).items() if key not in ('memory_high','kernel_high','manager')}
            expected={key:value for key,value in self.guards(before).items() if key not in ('memory_high','kernel_high','manager')}
            other={k:v for k,v in current['manager'].items() if k not in ('MemoryHigh','EffectiveMemoryHigh')}
            prior={k:v for k,v in before['manager'].items() if k not in ('MemoryHigh','EffectiveMemoryHigh')}
            if stable!=expected or other!=prior:raise ControlError('Other memory settings or ancestor constraints changed during verification; inspect before restoration')
            wanted_kernel=before['kernel_high'] if restoring else value
            if current['memory_high']==value and current['kernel_high']==wanted_kernel:return current
            if time.monotonic()>=deadline:raise ControlError('MemoryHigh was not confirmed in both systemd and memory.high; the change remains unconfirmed')
            time.sleep(.05)

    def apply(self,plan):
        if plan.get('property')!='memory_high' or plan.get('restoring'):raise ControlError('Only a prepared MemoryHigh apply is supported by this session')
        target,before,value=plan['target'],plan['before'],plan['value']
        normalized,_=memory_value('max' if value is None else value,before['page_size'])
        if normalized!=value:raise ControlError('Invalid page-aligned MemoryHigh preview; prepare again')
        current=self.backend.snapshot(target)
        if self.guards(current)!=self.guards(before):raise ControlError('Unit, memory settings or ancestor constraints changed after preview; prepare a fresh preview')
        if value is not None and value<current['observations']['memory_current'] and not plan['below_usage']:
            raise ControlError('Current usage crossed the requested MemoryHigh after preview; review the reclaim warning in a fresh preview')
        event=dict(timestamp=time.time(),receipt_id=uuid.uuid4().hex,target=deepcopy(target),property='memory_high',before=deepcopy(before),
                   requested=value,plan=deepcopy(plan),status='unconfirmed')
        self.events.append(event)
        try:
            self.backend.set_high(target,value)
            event['after']=self.verify(target,before,value)
            event['status']='confirmed'
        except Exception as exc:
            event['error']=str(exc);raise
        return event

    def latest(self,receipt_id=None):
        event=next((e for e in reversed(self.events) if e['status'] in ('confirmed','unconfirmed')),None)
        if event is None:raise ControlError('No MemoryHigh change from this session remains to restore')
        if receipt_id is not None and receipt_id!=event['receipt_id']:raise ControlError('A newer MemoryHigh receipt is active; review the latest change first')
        return event

    def check_restore(self,event,current):
        before=event['before']
        if self.identity(current)!=self.identity(before):raise ControlError('MemoryHigh restore refused: the unit restarted or cgroup was replaced')
        untouched=lambda info:{key:value for key,value in self.guards(info).items() if key not in ('memory_high','kernel_high','manager')}
        other=lambda info:{key:value for key,value in info['manager'].items() if key not in ('MemoryHigh','EffectiveMemoryHigh')}
        if untouched(current)!=untouched(before) or other(current)!=other(before):
            raise ControlError('MemoryHigh restore refused: other memory settings or ancestors changed externally')
        previous=(current['memory_high'],current['kernel_high'])==(before['memory_high'],before['kernel_high'])
        recoverable_kernel=current['kernel_high']==event['requested'] or (
            event['status']=='unconfirmed' and current['kernel_high']==before['kernel_high'])
        if not previous and (current['memory_high']!=event['requested'] or not recoverable_kernel):
            raise ControlError('MemoryHigh restore refused: current values differ from this session receipt; inspect external changes')
        return previous

    def prepare_restore(self,receipt_id):
        event=self.latest(receipt_id);current=self.backend.snapshot(event['target'])
        self.check_restore(event,current)
        value=event['before']['memory_high']
        return dict(target=deepcopy(event['target']),property='memory_high',before=current,value=value,
                    requested_bytes=value,expected_kernel_high=event['before']['kernel_high'],
                    below_usage=value is not None and value<current['observations']['memory_current'],
                    restoring=True,receipt_id=receipt_id)

    def undo(self,plan=None):
        event=self.latest(plan.get('receipt_id') if plan else None)
        target,before=event['target'],event['before']
        current=self.backend.snapshot(target)
        previous=self.check_restore(event,current)
        if plan is not None:
            if (plan.get('property')!='memory_high' or plan.get('restoring') is not True or plan.get('target')!=target or
                    plan.get('value')!=before['memory_high'] or plan.get('expected_kernel_high')!=before['kernel_high'] or
                    self.guards(plan['before'])!=self.guards(current)):
                raise ControlError('MemoryHigh restoration preview is stale or invalid; review a fresh preview')
            if before['memory_high'] is not None and before['memory_high']<current['observations']['memory_current'] and not plan['below_usage']:
                raise ControlError('Usage crossed the restoration boundary; prepare a fresh restore preview')
        if previous:
            event.update(status='undone',restored=current,undone_at=time.time());return event
        try:
            self.backend.set_high(target,before['memory_high'])
            event['restored']=self.verify(target,before,before['memory_high'],restoring=True)
        except Exception as exc:
            event.update(status='unconfirmed',error=str(exc));raise
        event.update(status='undone',undone_at=time.time())
        return event
