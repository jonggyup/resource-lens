#!/usr/bin/env python3
"""Synthetic MemoryHigh PTY walkthrough; host control/capture calls are forbidden."""
import argparse
from copy import deepcopy
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import termios
import time


ROOT=Path(__file__).resolve().parents[1]
MIB=1024**2


def child_main():
    sys.path.insert(0,str(ROOT))
    import resourcelens.ui as ui
    from resourcelens.controls import SystemdBackend
    from resourcelens.hardware import Hardware
    from resourcelens.memory_controls import MemoryHighSession,INFINITY
    def forbidden(*args,**kwargs):raise AssertionError('Synthetic walkthrough attempted a live operation')
    SystemdBackend.command=forbidden;Hardware.snapshot=forbidden;Hardware.write=forbidden
    ui.ProfileView=forbidden;ui.MemorySharingView=forbidden
    original_draw=ui.draw
    def draw(screen,*args,**kwargs):
        screen.clearok(True);return original_draw(screen,*args,**kwargs)
    ui.draw=draw
    def limit(value):return dict(state='unlimited' if value is None else 'known',value=value,reason=None,raw='max' if value is None else str(value))
    class Backend:
        def __init__(self):
            self.calls=[]
            self.info=dict(path='/demo.service',inode=11,device=1,invocation='fixture-memory',page_size=4096,
                memory_high=None,kernel_high=None,memory_max=512*MIB,kernel_max=512*MIB,
                manager=dict(MemoryHigh=str(INFINITY),MemoryMax=str(512*MIB),MemorySwapMax=str(INFINITY),
                             Id='demo.service',ControlGroup='/demo.service',InvocationID='fixture-memory',ActiveState='active'),
                swap_max=limit(None),ancestor_scan_complete=True,ancestry_visibility='to_configured_root',
                ancestors=[dict(path='/',inode=1,device=1,**{'memory.high':limit(64*MIB),'memory.max':limit(1024*MIB),'memory.swap.max':limit(None)})],
                observations={'memory_current':256*MIB,'memory.swap.current':0,'memory.peak':256*MIB})
        def snapshot(self,target):
            if (target['path'],target['inode'],target.get('device'))!=('/demo.service',11,1):raise AssertionError('Unexpected MemoryHigh target')
            return deepcopy(self.info)
        def set_high(self,target,value):
            self.calls.append(value);self.info.update(memory_high=value,kernel_high=value)
            self.info['manager']['MemoryHigh']=str(INFINITY if value is None else value)
    backend=Backend();original_init=ui.View.__init__
    def init(view,*args,**kwargs):
        original_init(view,*args,**kwargs);view.memory_session=MemoryHighSession(backend)
    ui.View.__init__=init
    class Collector:
        def __init__(self):
            rows=[json.loads(line) for line in (ROOT/'docs/m2-throttling-demo.jsonl').read_text().splitlines()]
            self.template=[row['sample'] for row in rows if row['type']=='sample'][-1]
        def sample(self):
            sample=deepcopy(self.template);sample['timestamp']=time.time();sample['elapsed_s']=.2
            sample['memory'].update(total=1024*MIB,used=512*MIB,available=512*MIB)
            sample['processes'][0]['memory']=64*MIB
            sample['workloads'][0].update(manager='system',controllable=True,memory_current=256*MIB)
            context=sample['target_context'];context['observed_at_wall']=sample['timestamp'];context['observed_at_monotonic']=time.monotonic()
            sample['source_quality']['target_context']['observed_at_wall']=sample['timestamp']
            sample['source_quality']['target_context']['observed_at_monotonic']=context['observed_at_monotonic']
            for index,node in enumerate(context['ancestors']):
                node.update(observed_at_wall=sample['timestamp'],observed_at_monotonic=context['observed_at_monotonic'],interval_s=.2,memory_events_scope='subtree')
                values={'memory.current':str(128*MIB if index==0 else 256*MIB),'memory.swap.current':'0','memory.peak':str(256*MIB),
                        'memory.high':'max' if index==0 or backend.info['memory_high'] is None else str(backend.info['memory_high']),
                        'memory.max':str(512*MIB),'memory.swap.max':'max','memory.low':'0','memory.min':'0',
                        'memory.stat':f'anon {64*MIB}\nfile {64*MIB}\nkernel {16*MIB}\nslab {8*MIB}\n',
                        'memory.pressure':'some avg10=0 avg60=0 avg300=0 total=0\nfull avg10=0 avg60=0 avg300=0 total=0\n'}
                for name,value in values.items():node['files'][name]=dict(value=value,reason=None)
                for name in ('memory.current','memory.swap.current','memory.peak'):node['counters'][name]=int(values[name])
                node['counters']['memory.stat']={key:int(value) for key,value in (line.split() for line in values['memory.stat'].splitlines())}
                node['counters']['memory.pressure']={'some':{'avg10':0},'full':{'avg10':0}}
            return sample
    ui.run(Collector(),.2,ascii=True,control=True)
    print('FIXTURE_RESULT='+json.dumps(dict(writes=backend.calls,final_high=backend.info['kernel_high'],
                                           unchanged_max=backend.info['kernel_max'],observed_usage=backend.info['observations']['memory_current'])))


def verify():
    master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',40,110,0,0))
    child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child'],stdin=slave,stdout=slave,stderr=slave,cwd=ROOT,
                           env={**os.environ,'TERM':'xterm-256color'})
    output=bytearray();recent=bytearray();timings={}
    def read():
        try:return os.read(master,65536)
        except OSError as exc:
            if exc.errno==errno.EIO:return b''
            raise
    def until(needle,keys=None,name=None):
        nonlocal recent
        started=time.monotonic()
        if keys is not None:recent=bytearray();os.write(master,keys)
        if needle in recent:return
        while time.monotonic()-started<5:
            if select.select([master],[],[],.02)[0]:
                data=read();output.extend(data);recent.extend(data)
            if needle in recent:
                if name:timings[name]=round((time.monotonic()-started)*1000,3)
                return
            if child.poll() is not None:break
        raise RuntimeError('Missing terminal text '+repr(needle)+'; '+repr(bytes(recent[-1800:])))
    try:
        until(b'RESOURCE LENS');until(b'APPLICATIONS / PROCESSES',b'8')
        until(b'INSPECT / PID 123',b'sworker\n','inspect_process')
        until(b'CGROUP MEMORY',b'm','read_exact_group_memory')
        until(b'REVIEW SERVICE CONTROLS',b'r','review_service_scope')
        until(b'SET SERVICE MEMORYHIGH',b'h','open_memory_editor')
        until(b'REVIEW MEMORYHIGH',b'128M\n','fresh_preview_without_write')
        until(b'WARNING:')
        until(b'Scroll to end to enable Enter.',b'\n','refuse_unreviewed_apply')
        until(b'[ Apply ]',b'\x1b[6~','review_full_preview')
        until(b'CONTROL RESULT',b'\n','apply_and_verify')
        until(b'REVIEW MEMORYHIGH RESTORATION',b'u','fresh_restoration_preview')
        until(b'[ Apply ]',b'\x1b[6~','review_restoration')
        until(b'CONTROL RESULT',b'\n','restore_and_verify')
        until(b'CHANGES',b'J','shared_changes_history')
        fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',14,60,0,0))
        until(b'CHANGES',b'np','small_terminal_history')
        os.write(master,b'\x1b\x1bqq');child.wait(timeout=4)
        while select.select([master],[],[],0)[0]:
            data=read()
            if not data:break
            output.extend(data)
        if child.returncode or b'Traceback' in output:raise RuntimeError(bytes(output[-2400:]).decode(errors='replace'))
        result=json.loads(bytes(output).split(b'FIXTURE_RESULT=')[-1].strip())
        if result!=dict(writes=[128*MIB,None],final_high=None,unchanged_max=512*MIB,observed_usage=256*MIB):
            raise RuntimeError('Unexpected fixture settings: '+repr(result))
        return dict(scenario='M5 PR14 MemoryHigh observation/review/apply/restore',
                    evidence='Synthetic counters and in-memory settings; no live enforcement or latency claim.',
                    host_operations='forbidden',rendering='Full redraw instrumentation; timings are local checks, not benchmarks.',
                    terminal_sizes=[[110,40],[60,14]],key_to_matching_output_ms=timings,fixture_result=result,exit_code=child.returncode)
    finally:
        if child.poll() is None:child.kill()
        child.wait();os.close(master);os.close(slave)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--child',action='store_true',help=argparse.SUPPRESS)
    if parser.parse_args().child:child_main()
    else:print(json.dumps(verify(),indent=2,allow_nan=False))
