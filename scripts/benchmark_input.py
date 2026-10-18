"""Local PTY key-to-curses-flush measurements (not physical display/SSH latency)."""
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


def trial():
    master, slave = pty.openpty()
    reader, writer = os.pipe()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 35, 120, 0, 0))
    code = '''import os,json,time
import resourcelens.ui as ui
from resourcelens.collect import Collector
original=ui.draw
fd=int(os.environ['RL_BENCH_FD'])
def draw(screen,view,interval,error=None):
 original(screen,view,interval,error)
 os.write(fd,(json.dumps(dict(ns=time.perf_counter_ns(),page=view.page,filter=view.filter,editing=view.editing_filter,ready=view.snapshot is not None))+'\\n').encode())
ui.draw=draw
ui.run(Collector(nvidia=False),60)
'''
    env={**os.environ,'TERM':'xterm-256color','RL_BENCH_FD':str(writer)}
    env.pop('ESCDELAY',None)
    child=subprocess.Popen([sys.executable,'-c',code],stdin=slave,stdout=slave,stderr=slave,
                           env=env,pass_fds=(writer,),cwd=Path(__file__).resolve().parents[1])
    os.close(writer)
    pending=bytearray()
    frames=0
    def until(predicate):
        nonlocal frames
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            ready,_,_=select.select([master,reader],[],[],.05)
            if master in ready:
                os.read(master,65536)
            if reader in ready:
                data=os.read(reader,65536)
                if not data: raise RuntimeError('benchmark child exited')
                pending.extend(data)
                while b'\n' in pending:
                    line,_,tail=pending.partition(b'\n');pending[:]=tail
                    frame=json.loads(line);frames+=1
                    if predicate(frame):return frame
        raise RuntimeError('Frame timeout')
    try:
        until(lambda f:f['ready'])
        result={};regular=[]
        for key,page in [(b'0',9),(b'6',5),(b'3',2),(b'7',6),(b'1',0)]:
            start=time.perf_counter_ns();os.write(master,key)
            frame=until(lambda f:f['page']==page)
            regular.append(round((frame['ns']-start)/1e6,3))
        result['navigation_ms']=regular
        os.write(master,b'/');until(lambda f:f['editing'])
        start=time.perf_counter_ns();before=frames;os.write(master,b'x'*40)
        frame=until(lambda f:f['filter']=='x'*40)
        result['40_character_burst_ms']=round((frame['ns']-start)/1e6,3)
        result['burst_frames']=frames-before
        start=time.perf_counter_ns();os.write(master,b'\x1b')
        frame=until(lambda f:not f['editing'])
        result['escape_to_frame_ms']=round((frame['ns']-start)/1e6,3)
        start=time.perf_counter_ns();os.write(master,b'\x1b')
        while child.poll() is None:
            if select.select([master],[],[],.001)[0]:os.read(master,65536)
            if time.perf_counter_ns()-start>3e9:raise RuntimeError('Exit timeout')
        result['escape_to_exit_ms']=round((time.perf_counter_ns()-start)/1e6,3)
        assert child.returncode==0
        return result
    finally:
        if child.poll() is None:child.kill()
        child.wait()
        for fd in (master,slave,reader):os.close(fd)


if __name__=='__main__':
    print(json.dumps({'method':__doc__,'trials':[trial() for _ in range(3)]},indent=2))
