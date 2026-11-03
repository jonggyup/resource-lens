#!/usr/bin/env python3
"""Synthetic PTY control walkthrough. All policy mutations use an in-memory backend."""
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


ROOT = Path(__file__).resolve().parents[1]


def child_main():
    sys.path.insert(0,str(ROOT))
    from resourcelens.controls import ControlSession, SystemdBackend
    from resourcelens.hardware import Hardware
    from resourcelens.ui import View, run
    import resourcelens.ui as ui

    original_draw = ui.draw
    def draw(screen,*args,**kwargs):
        # Emit full frames so matching text cannot be split by curses diff paints.
        screen.clearok(True)
        return original_draw(screen,*args,**kwargs)
    ui.draw = draw

    def forbidden(*args, **kwargs):
        raise AssertionError('Synthetic walkthrough attempted host control access')
    SystemdBackend.command = forbidden
    Hardware.snapshot = forbidden
    Hardware.write = forbidden

    class Backend:
        def __init__(self):
            self.info = dict(path='/demo.service', inode=11, invocation='fixture-invocation',
                             weight=100, quota=None, kernel_weight=100, kernel_quota=None, kernel_max='max 100000')
            self.calls = []

        def snapshot(self,target):
            return deepcopy(self.info)

        def set(self,target,property,value):
            self.calls.append((property,value))
            self.info[property] = value
            if property == 'quota':
                self.info['kernel_quota'] = value
                self.info['kernel_max'] = f'{int(value*1000)} 100000' if value is not None else 'max 100000'
            else:
                self.info['kernel_weight'] = 100 if value is None else value

    backend = Backend()
    original_init = View.__init__
    def init(view,*args,**kwargs):
        original_init(view,*args,**kwargs)
        view.session = ControlSession(backend)
    View.__init__ = init

    class ScenarioCollector:
        def __init__(self):
            rows = [json.loads(line) for line in (ROOT/'docs/m2-throttling-demo.jsonl').read_text().splitlines()]
            self.template = [r['sample'] for r in rows if r['type']=='sample'][-1]

        def sample(self):
            sample = deepcopy(self.template)
            sample['timestamp'], sample['elapsed_s'] = time.time(), .2
            group = sample['workloads'][0]
            group.update(manager='system',controllable=True)
            context = sample['target_context']
            context['observed_at_wall'] = sample['timestamp']
            context['observed_at_monotonic'] = time.monotonic()
            for node in context['ancestors']:
                node['observed_at_wall'] = sample['timestamp']
                node['observed_at_monotonic'] = context['observed_at_monotonic']
            return sample

    run(ScenarioCollector(),.2,ascii=True,control=True)
    print('FIXTURE_RESULT='+json.dumps(dict(calls=backend.calls,final_quota=backend.info['quota'])))


def verify():
    master, slave = pty.openpty()
    fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',40,110,0,0))
    child = subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child'],
                             stdin=slave,stdout=slave,stderr=slave,cwd=ROOT,
                             env={**os.environ,'TERM':'xterm-256color'})
    timings, output = {}, bytearray()
    def read():
        try:return os.read(master,65536)
        except OSError as exc:
            if exc.errno == errno.EIO:return b''
            raise
    def until(needle,keys=None,name=None):
        new=bytearray();start=time.monotonic()
        if keys is not None:os.write(master,keys)
        while time.monotonic()-start<4:
            if select.select([master],[],[],.02)[0]:
                data=read();new.extend(data);output.extend(data)
            if needle in new:
                if name:timings[name]=round((time.monotonic()-start)*1000,3)
                return
            if child.poll() is not None:break
        raise RuntimeError('Missing terminal text '+repr(needle)+'; '+repr(bytes(new[-1600:])))
    try:
        until(b'RESOURCE LENS')
        until(b'APPLICATIONS / PROCESSES',b'8')
        until(b'INSPECT / PID 123',b'sworker\n','inspect_process')
        until(b'REVIEW SERVICE CONTROLS',b'r','review_service_scope')
        until(b'Set CPU quota for demo.service',b'l','open_quota_editor')
        until(b'Confirm temporary CPU setting',b'25\n','prepare_without_write')
        until(b'CONTROL RESULT',b'\n','apply_and_verify')
        until(b'CHANGES',b'J','open_changes')
        until(b'REVIEW RESTORATION',b'u','review_restore')
        until(b'CONTROL RESULT',b'\n','restore_and_verify')
        until(b'Latest configuration event',b'\x1b','return_to_inspector')
        until(b'CHANGES',b'J','reopen_shared_history')
        fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',14,60,0,0))
        # Trigger a redraw with a printable key, then exercise paging/return.
        until(b'CHANGES',b'np','small_terminal_history')
        os.write(master,b'\x1b\x1bqq')
        child.wait(timeout=4)
        while select.select([master],[],[],0)[0]:
            data=read()
            if not data:break
            output.extend(data)
        if child.returncode or b'Traceback' in output:
            raise RuntimeError(bytes(output[-2400:]).decode(errors='replace'))
        result = json.loads(bytes(output).split(b'FIXTURE_RESULT=')[-1].strip())
        if result != dict(calls=[['quota',25],['quota',None]],final_quota=None):
            raise RuntimeError('Unexpected fixture mutations: '+repr(result))
        return dict(scenario='M3 process-to-service control and restoration',
                    evidence='Synthetic telemetry and in-memory settings; no live enforcement claim.',
                    rendering='Full redraw instrumentation; timings are local checks, not benchmarks.',
                    host_control_access='forbidden',terminal_sizes=[[110,40],[60,14]],
                    key_to_matching_output_ms=timings,fixture_result=result,exit_code=child.returncode)
    finally:
        if child.poll() is None:child.kill()
        child.wait();os.close(master);os.close(slave)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.child:child_main()
    else:print(json.dumps(verify(),indent=2,allow_nan=False))
