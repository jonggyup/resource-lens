"""Opt-in, kernel-filtered scheduler evidence; never changes global tracing settings."""
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

from .memory_sharing import identity

MAX_THREADS = 64
MAX_BYTES = 64 * 1024 * 1024
HEADER = re.compile(r'^\s*(\d+\.\d+):\s+sched:(sched_switch|sched_wakeup):\s+(.*)$')
SWITCH = re.compile(r'prev_pid=(\d+)\s+prev_prio=\d+\s+prev_state=(\S+).*\bnext_pid=(\d+)\b')
WAKE = re.compile(r'\bpid=(\d+)\b')
FRAME = re.compile(r'^\s*[0-9a-f]+\s+(.+)$')


def private_file(path):
    return open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w')


def stop_process(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def host_pid_namespace():
    # Linux include/linux/proc_ns.h: PROC_PID_INIT_INO. Fail closed
    # when namespace IDs cannot be matched to host tracepoint IDs.
    return Path('/proc/self/ns/pid').stat().st_ino == 0xEFFFFFFC


def thread_set(pid=None, cgroup=None):
    """Frozen scope, not dynamic cgroup attribution. Refuse oversized/partial scope."""
    tids = set()
    if pid:
        with os.scandir(f'/proc/{pid}/task') as entries:
            for entry in entries:
                if entry.name.isdigit():
                    tids.add(int(entry.name))
                if len(tids) > MAX_THREADS:
                    raise ValueError('Scheduler tracing supports at most 64 initial threads; choose a smaller target')
    else:
        pending = [Path(cgroup)]
        visited = 0
        while pending:
            directory = pending.pop()
            visited += 1
            if visited > 256:
                raise ValueError('Cgroup exceeds 256-directory scheduler scope limit')
            with (directory/'cgroup.threads').open() as stream:
                for line in stream:
                    tids.add(int(line))
                    if len(tids) > MAX_THREADS:
                        raise ValueError('Scheduler tracing supports at most 64 initial threads; choose a smaller target')
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                        if len(pending) + visited > 256:
                            raise ValueError('Cgroup exceeds 256-directory scheduler scope limit')
    if not tids:
        raise ValueError('No threads in scheduler target')
    return {tid: identity(Path('/proc')/str(tid)) for tid in sorted(tids)}


def distribution(values):
    if not values:
        return {'count': 0}
    values = sorted(values)
    def percentile(p):
        return values[max(0, math.ceil(p*len(values))-1)]
    return dict(count=len(values), total_ms=sum(values), mean_ms=sum(values)/len(values),
                p50_ms=percentile(.5), p95_ms=percentile(.95), p99_ms=percentile(.99), max_ms=values[-1])


def analyze(lines, tids, max_events=200000):
    """Pair only complete observed intervals. Lose all derived results on trace gaps."""
    tids = set(tids)
    pending = {}
    values = {'off_cpu': [], 'sleep_to_wakeup': [], 'runqueue': []}
    stacks = {}
    result = dict(events=0, unpaired_switch_ins=0, unfinished_intervals=0,
                  missing_wakeups=0, duplicate_wakeups=0, malformed_events=0,
                  discontinuities=0, loss_records=0, truncated=False)
    last_time = -1
    first_time = None
    current = None

    def event(row):
        nonlocal last_time, first_time
        timestamp, kind, payload, frames = row
        result['events'] += 1
        if first_time is None:
            first_time = timestamp
        if timestamp < last_time:
            result['discontinuities'] += 1
            pending.clear()
        last_time = timestamp
        if kind == 'sched_wakeup':
            match = WAKE.search(payload)
            if not match:
                result['malformed_events'] += 1
                return
            tid = int(match[1])
            state = pending.get(tid)
            if tid in tids:
                if state is None:
                    # Capture may start while this task is already sleeping.
                    pending[tid] = dict(out=None, ready=timestamp, state=None, frames=[])
                elif state['ready'] is None:
                    state['ready'] = timestamp
                else:
                    result['duplicate_wakeups'] += 1
            return
        match = SWITCH.search(payload)
        if not match:
            result['malformed_events'] += 1
            return
        prev, state, nxt = int(match[1]), match[2], int(match[3])
        if prev in tids:
            if prev in pending:
                result['discontinuities'] += 1
            pending[prev] = dict(out=timestamp, ready=timestamp if state in ('R','R+') else None,
                                 state=state, frames=frames)
        if nxt not in tids:
            return
        interval = pending.pop(nxt, None)
        if interval is None:
            result['unpaired_switch_ins'] += 1
            return
        out, ready = interval['out'], interval['ready']
        if out is not None:
            duration = (timestamp-out)*1000
            values['off_cpu'].append(duration)
            # Exit/dead/stopped task states are not labelled sleeping.
            sleeping = interval['state'] in ('S','D','I')
            if sleeping and ready is not None:
                values['sleep_to_wakeup'].append((ready-out)*1000)
            elif sleeping:
                result['missing_wakeups'] += 1
            key = (interval['state'], tuple(interval['frames']))
            if key not in stacks and len(stacks) >= 1024:
                key = ('other', ('[additional stack groups omitted]',))
            entry = stacks.setdefault(key, dict(state=key[0], frames=list(key[1]), intervals=0, off_cpu_ms=0))
            entry['intervals'] += 1
            entry['off_cpu_ms'] += duration
        if ready is not None:
            values['runqueue'].append((timestamp-ready)*1000)

    for line in lines:
        if 'PERF_RECORD_LOST' in line or re.search(r'\bLOST\s+\d+', line):
            result['loss_records'] += 1
            continue
        match = HEADER.match(line)
        if match:
            if current:
                event(current)
            if result['events'] >= max_events:
                result['truncated'] = True
                current = None
                break
            current = [float(match[1]), match[2], match[3], []]
        elif 'sched:sched_' in line:
            result['malformed_events'] += 1
        elif current:
            frame = FRAME.match(line)
            if frame and len(current[3]) < 32:
                current[3].append(frame[1])
    if current:
        event(current)
    result['observed_seconds'] = max(0,last_time-first_time) if first_time is not None else 0
    result['unfinished_intervals'] = len(pending)
    valid = not any(result[k] for k in ('loss_records','malformed_events','discontinuities','truncated'))
    result['status'] = 'complete' if valid and result['events'] else ('empty' if valid else 'partial')
    result['distributions'] = {k: distribution(v) for k,v in values.items()} if valid else {}
    result['top_stacks'] = sorted(stacks.values(), key=lambda s: -s['off_cpu_ms'])[:10] if valid else []
    return result


class SchedulerTrace:
    def __init__(self, directory, pid, cgroup, duration):
        self.directory = Path(directory)
        self.process = None
        self.log = None
        self.tids = {}
        self.result = dict(status='unavailable', warnings=[
            'Scope is the initial thread set, not newly created threads or dynamic cgroup membership.',
            'Use host PID namespace and host /proc; tracepoint task IDs are host IDs.',
            'Off-CPU time includes voluntary sleeps; scheduler state and stacks do not prove a bottleneck.',
            'Stacks belong to the switched-out task, not the next task or the wakeup source.'])
        self.perf = shutil.which('perf')
        try:
            if not self.perf:
                raise ValueError('perf is not installed')
            if not host_pid_namespace():
                raise ValueError('Scheduler tracing requires the initial host PID namespace')
            self.tids = thread_set(pid, cgroup)
            self.result['threads'] = {str(tid): start for tid,start in self.tids.items()}
            switches = ' || '.join(f'prev_pid == {tid} || next_pid == {tid}' for tid in self.tids)
            wakes = ' || '.join(f'pid == {tid}' for tid in self.tids)
            command = [self.perf, 'record', '-a', '--clockid', 'mono', '--call-graph', 'fp',
                       '-e', 'sched:sched_switch', '--filter', switches,
                       '-e', 'sched:sched_wakeup', '--filter', wakes,
                       '-o', str(self.directory/'sched.data'), '--', 'sleep', str(duration)]
            self.result['command'] = command
            self.log = private_file(self.directory/'sched.log')
            self.process = subprocess.Popen(command, stdout=self.log, stderr=self.log, start_new_session=True,
                                            env={**os.environ, 'LC_ALL': 'C'})
            self.result['status'] = 'collecting'
        except (OSError, ValueError) as exc:
            self.result['reason'] = str(exc)
            if self.log:
                self.log.close()

    def over_limit(self):
        path = self.directory/'sched.data'
        try:
            return path.stat().st_size > MAX_BYTES
        except FileNotFoundError:
            return False

    def finish(self, cancelled=False):
        if not self.process:
            return self.result
        stop_process(self.process)
        self.log.close()
        self.result['returncode'] = self.process.returncode
        with (self.directory/'sched.log').open(errors='replace') as stream:
            self.result['diagnostic'] = stream.read(16384)
        data = self.directory/'sched.data'
        if data.exists():
            data.chmod(0o600)
        if cancelled:
            self.result['status'] = 'cancelled'
            return self.result
        self.result['status'] = 'failed'
        if self.process.returncode not in (0, -signal.SIGINT, 128+signal.SIGINT) or not data.exists():
            return self.result
        if self.over_limit():
            self.result.update(status='partial', reason='Scheduler trace exceeded 64 MiB soft limit')
            return self.result
        valid = set()
        for tid,start in self.tids.items():
            try:
                if identity(Path('/proc')/str(tid)) == start:
                    valid.add(tid)
            except (OSError, ValueError):
                pass
        self.result['excluded_threads'] = sorted(set(self.tids)-valid)
        if len(valid) != len(self.tids):
            self.result['warnings'].append('Exited/reused thread identities excluded; short-lived work is not represented.')
        output = self.directory/'sched-events.txt'
        errors = self.directory/'sched-decode.log'
        process = None
        try:
            with private_file(output) as stream, private_file(errors) as log:
                command = [self.perf, 'script', '--ns', '--show-lost-events', '-i', str(data),
                           '-F', 'trace:time,event,trace,ip,sym,dso']
                process = subprocess.Popen(command, stdout=stream, stderr=log, start_new_session=True,
                                            env={**os.environ, 'LC_ALL': 'C'})
                deadline = time.monotonic()+5
                while process.poll() is None:
                    if time.monotonic() > deadline or output.stat().st_size > MAX_BYTES:
                        raise ValueError('Scheduler decoding exceeded time/size limit')
                    time.sleep(.02)
                if process.returncode:
                    raise ValueError('perf script failed; see sched-decode.log')
            if output.stat().st_size > MAX_BYTES:
                raise ValueError('Scheduler decoding exceeded size limit')
            with output.open(errors='replace') as stream:
                evidence = analyze(stream, valid)
            # perf may report a loss only in diagnostics, depending on its version.
            with errors.open(errors='replace') as stream:
                diagnostic = self.result['diagnostic'] + stream.read(16384)
            if re.search(r'\blost\b|PERF_RECORD_LOST', diagnostic, re.I):
                evidence.update(status='partial', distributions={}, top_stacks=[])
                self.result['warnings'].append('Perf diagnostics mention loss; derived distributions suppressed. Inspect logs.')
            self.result.update(evidence)
            if self.result['excluded_threads'] and self.result['status']=='complete':
                self.result['status']='partial'
            if not valid:
                self.result.update(status='partial', distributions={}, top_stacks=[], reason='No original thread identities survived')
        except (OSError, ValueError) as exc:
            self.result.update(status='partial', reason=str(exc))
        finally:
            if process and process.poll() is None:
                stop_process(process)
        return self.result


def summary_lines(result):
    lines = ['SCHEDULER TRACE: '+result['status']]
    if result.get('events'):
        lines.append('Percentiles cover complete observed intervals, not request latency or the entire capture window.')
    if result.get('reason'):
        lines.append(result['reason'])
    lines += [f"Initial threads {len(result.get('threads',{}))} | events {result.get('events',0)} | span {result.get('observed_seconds',0):.3f}s | excluded threads {len(result.get('excluded_threads',[]))}"]
    labels = {'off_cpu':'Off CPU', 'sleep_to_wakeup':'Sleep to wakeup', 'runqueue':'Ready to running'}
    for key, value in result.get('distributions',{}).items():
        if not value['count']:
            lines.append(labels[key]+': no complete intervals')
        else:
            lines.append(f"{labels[key]}: {value['count']} intervals | p50 {value['p50_ms']:.3f} ms | p95 {value['p95_ms']:.3f} ms | p99 {value['p99_ms']:.3f} ms | max {value['max_ms']:.3f} ms")
    lines.append(f"Unpaired entries {result.get('unpaired_switch_ins',0)} | unfinished {result.get('unfinished_intervals',0)} | missing wakeups {result.get('missing_wakeups',0)} | loss records {result.get('loss_records',0)}")
    lines += result.get('warnings',[])
    for stack in result.get('top_stacks',[])[:5]:
        lines.append(f"State {stack['state']} | {stack['off_cpu_ms']:.3f} ms off CPU | {stack['intervals']} intervals")
        lines += ['  '+frame for frame in stack['frames'][:8]] or ['  [stack unavailable]']
    if result['status'] in ('failed','unavailable') and result.get('diagnostic'):
        lines += result['diagnostic'].splitlines()[:12]
    return lines
