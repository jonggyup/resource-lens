#!/usr/bin/env python3
"""Read-only PTY walkthrough using synthetic recorded CPU-quota evidence."""

import argparse
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


def verify(recording):
    root = Path(__file__).resolve().parents[1]
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 100, 0, 0))
    args = ['resource-lens', '--replay', str(recording), '--ascii', '--no-config', '--interval', '0.2']
    code = ('import sys\nimport resourcelens.__main__ as cli\n'
            'def forbidden(*args, **kwargs): raise AssertionError("Replay constructed a live collector")\n'
            'cli.Collector=forbidden\n' + f'sys.argv={args!r}\nraise SystemExit(cli.main())\n')
    child = subprocess.Popen([sys.executable, '-c', code], stdin=slave, stdout=slave,
                             stderr=slave, cwd=root, env={**os.environ, 'TERM': 'xterm-256color'})
    timings = {}
    def read():
        try:return os.read(master, 65536)
        except OSError as exc:
            if exc.errno == errno.EIO:return b''
            raise
    def until(needle, keys=None, name=None):
        output = bytearray()
        start = time.monotonic()
        if keys is not None:os.write(master, keys)
        while time.monotonic()-start < 4:
            if select.select([master], [], [], .02)[0]:output.extend(read())
            if needle in output:
                if name:timings[name] = round((time.monotonic()-start)*1000, 3)
                return
            if child.poll() is not None:break
        raise RuntimeError('Expected terminal output was not observed: '+needle.decode()+'; tail '+repr(bytes(output[-1200:])))
    try:
        until(b'RESOURCE LENS')
        until(b'APPLICATIONS / PROCESSES', b')8', 'open_applications')
        until(b'Group quota throttling observed', b'sworker\n', 'search_and_inspect')
        until(b'Recorded value:', b'e', 'open_configuration_evidence')
        until(b'throttled_usec:', b'n', 'open_counter_evidence')
        until(b'Cgroup /demo.service/jobs', b'g', 'open_exact_group')
        until(b'throttled_usec:', b'\x1b', 'restore_evidence_view')
        until(b'live target', b'P', 'reject_live_capture_in_replay')
        until(b'APPLICATIONS / PROCESSES', b'\x1bq', 'return_to_applications')
        started = time.monotonic();os.write(master,b'q')
        while child.poll() is None and time.monotonic()-started < 3:
            if select.select([master], [], [], .01)[0]:read()
        if child.poll() != 0:raise RuntimeError('Replay did not exit cleanly')
        return dict(method=__doc__, evidence='Synthetic counters; not live enforcement or independent usability validation.',
                    terminal=dict(columns=100, rows=24), live_collector='forbidden by constructor guard',
                    key_to_matching_output_ms=timings, exit_code=child.returncode)
    finally:
        if child.poll() is None:child.kill()
        child.wait()
        os.close(master);os.close(slave)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', type=Path, default=Path(__file__).resolve().parents[1]/'docs/m2-throttling-demo.jsonl')
    args=parser.parse_args()
    print(json.dumps(verify(args.record.resolve()),indent=2,allow_nan=False))


if __name__=='__main__':main()
