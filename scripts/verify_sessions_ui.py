#!/usr/bin/env python3
"""Synthetic offline Sessions walkthrough; live collection/capture/control calls are forbidden."""
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
import tempfile
import termios
import time


ROOT=Path(__file__).resolve().parents[1]


def fixtures(state):
    sys.path.insert(0,str(ROOT))
    from resourcelens.incidents import IncidentCapture
    rows=[json.loads(line) for line in (ROOT/'docs/m2-throttling-demo.jsonl').read_text().splitlines()]
    samples=[row['sample'] for row in rows if row['type']=='sample']
    capture=IncidentCapture(state/'incidents',duration=3,post=0)
    for i,source in enumerate(samples):
        sample=deepcopy(source);sample['pressure']={}
        for node in sample['target_context']['ancestors']:
            node['files']['io.pressure']=dict(value='some avg10=30 avg60=0 avg300=0 total=100',reason=None)
            node['counters']['io.pressure']={'some':{'avg10':30,'total':i*100}}
        capture.observe(sample)
    capture.close()
    originals={}
    for name,value,status in (('a-baseline',25,'complete'),('b-candidate',50,'complete'),('c-partial',80,'partial')):
        directory=state/'profiles'/name;directory.mkdir(parents=True)
        record=dict(schema=1,label='synthetic-build',target=dict(pid=123,cgroup=None,identity=99),started=100,
                    requested_seconds=5,elapsed_seconds=5,status=status,samples=[{'elapsed':0},{'elapsed':5}],
                    metrics={'cpu_percent':value,'rss_end_bytes':128},outcomes={},environment={'kernel':'fixture','machine':'fixture'},
                    profile={'status':'disabled'},scheduler={'status':'disabled'},energy={'status':'disabled'},warnings=[])
        path=directory/'session.json';path.write_text(json.dumps(record));originals[path]=path.read_bytes()
    return originals


def verify():
    with tempfile.TemporaryDirectory() as directory:
        state=Path(directory)/'resource-lens';originals=fixtures(state)
        master,slave=pty.openpty()
        fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',40,110,0,0))
        code=('import sys\nimport resourcelens.__main__ as cli\nimport resourcelens.ui as ui\n'
              'import resourcelens.profiling as profiles\nimport resourcelens.controls as controls\n'
              'import resourcelens.hardware as hardware\n'
              'def forbidden(*args,**kwargs): raise AssertionError("Offline viewer attempted a live operation")\n'
              'cli.Collector=forbidden\nprofiles.capture=forbidden\ncontrols.SystemdBackend.snapshot=forbidden\n'
              'controls.SystemdBackend.command=forbidden\nhardware.Hardware.snapshot=forbidden\nhardware.Hardware.write=forbidden\n'
              'original=ui.draw\n'
              'def draw(screen,*args,**kwargs):\n screen.clearok(True)\n return original(screen,*args,**kwargs)\n'
              'ui.draw=draw\n'
              "sys.argv=['resource-lens','--sessions','--no-config','--ascii']\nraise SystemExit(cli.main())\n")
        child=subprocess.Popen([sys.executable,'-c',code],stdin=slave,stdout=slave,stderr=slave,cwd=ROOT,
                               env={**os.environ,'TERM':'xterm-256color','XDG_STATE_HOME':directory})
        output=bytearray();timings={};recent=bytearray()
        def read():
            try:return os.read(master,65536)
            except OSError as exc:
                if exc.errno==errno.EIO:return b''
                raise
        def until(needle,keys=None,name=None):
            nonlocal recent
            started=time.monotonic()
            if keys is not None:recent=bytearray();os.write(master,keys)
            frame=recent
            if needle in frame:return
            while time.monotonic()-started<5:
                if select.select([master],[],[],.02)[0]:
                    data=read();output.extend(data);frame.extend(data)
                if needle in frame:
                    if name:timings[name]=round((time.monotonic()-started)*1000,3)
                    return
                if child.poll() is not None:break
            raise RuntimeError('Missing terminal text '+repr(needle)+'; tail '+repr(bytes(frame[-1800:])))
        try:
            until(b'RESOURCE LENS / SESSIONS')
            until(b'baseline')
            until(b'SAVED RECORDING / OFFLINE',b'\n','open_incident_offline')
            until(b'INSPECT / PID 123',b'sworker\n','inspect_saved_target')
            until(b'Captured findings (original rules)',b'J','read_captured_findings')
            until(b'local_io_pressure / version 1')
            until(b'RESOURCE LENS / SESSIONS',b'\x1bL','return_to_sessions')
            until(b'SAVED PROFILE - offline',b'j\n','open_baseline')
            until(b'MEASURED APPLICATION OUTCOME',b'o')
            until(b'Outcome saved separately',b'p99 | ms | 12 | fixture load generator\n','save_measured_outcome')
            until(b'Baseline:',b'b','select_baseline')
            until(b'SESSIONS',b'\x1b')
            until(b'SAVED PROFILE - offline',b'j\n','open_candidate')
            until(b'MEASURED APPLICATION OUTCOME',b'o')
            until(b'Outcome saved separately',b'p99 | ms | 10 | fixture load generator\n')
            until(b'SAVED RUN COMPARISON',b'c','compare_without_cli')
            until(b'Saved redacted summary:',b'x','export_redacted_summary')
            fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',14,60,0,0))
            until(b'SAVED RUN COMPARISON',b'k','small_terminal_comparison')
            os.write(master,b'\x1bq');child.wait(timeout=4)
            while select.select([master],[],[],0)[0]:
                data=read()
                if not data:break
                output.extend(data)
            if child.returncode or b'Traceback' in output:raise RuntimeError(bytes(output[-2400:]).decode(errors='replace'))
            report=json.loads(next((state/'reports').glob('*.json')).read_text())
            text=json.dumps(report)
            if directory in text or 'fixture load generator' in text:raise RuntimeError('Export retained an identifier')
            metrics=report['summary']['comparison']['metrics']
            if metrics['cpu_percent']['change_percent']!=100:raise RuntimeError('Unexpected comparison result')
            if any(path.read_bytes()!=content for path,content in originals.items()):raise RuntimeError('Original profile was modified')
            return dict(scenario='M4 saved incident, profiles, measured outcomes, comparison and export',
                        evidence='Synthetic fixtures only; no live enforcement, latency measurement or independent-user claim.',
                        live_operations='forbidden',rendering='Full redraw instrumentation; timings are local checks, not benchmarks.',
                        terminal_sizes=[[110,40],[60,14]],key_to_matching_output_ms=timings,
                        raw_originals='unchanged',comparison_cpu_change_percent=100,redaction='identifiers absent; units retained',exit_code=child.returncode)
        finally:
            if child.poll() is None:child.kill()
            child.wait();os.close(master);os.close(slave)


if __name__=='__main__':print(json.dumps(verify(),indent=2,allow_nan=False))
