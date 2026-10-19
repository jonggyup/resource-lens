"""Bounded, private investigation sessions. No privilege or kernel-setting changes."""
import curses
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import statistics
import subprocess
import threading
import time

from .memory_sharing import identity


def read(path):
    try:
        with open(path) as f:
            return f.read(262144).strip()
    except OSError:
        return None


def write(path, value):
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write('\n')


def target_sample(pid=None, cgroup=None):
    if cgroup:
        root = Path(cgroup)
        values = {}
        for filename in ('cpu.stat', 'memory.current', 'memory.events', 'io.stat', 'cpu.pressure', 'memory.pressure', 'io.pressure'):
            values[filename] = read(root / filename)
        if values['cpu.stat'] is None:
            raise ValueError('Cgroup disappeared or cpu.stat is unreadable')
        return values
    root = Path('/proc') / str(pid)
    raw = read(root / 'stat')
    if not raw:
        raise ValueError('Process exited or /proc is unreadable')
    fields = raw[raw.rfind(')') + 2:].split()
    result = dict(starttime=int(fields[19]), cpu_ticks=int(fields[11])+int(fields[12]),
                  minor_faults=int(fields[7]), major_faults=int(fields[9]), rss_bytes=int(fields[21])*os.sysconf('SC_PAGE_SIZE'),
                  io=read(root/'io'), status=read(root/'status'), threads={})
    # Exited/short-lived threads cannot be reconstructed from polling. Preserve IDs.
    with os.scandir(root/'task') as tasks:
        for index, entry in enumerate(tasks):
            if index >= 4096:
                result['threads_truncated'] = True
                break
            stat = read(Path(entry.path)/'schedstat')
            try:
                generation = identity(Path(entry.path))
            except (OSError, ValueError):
                continue
            if stat and generation is not None:
                result['threads'][entry.name] = dict(starttime=generation, schedstat=stat)
    return result


def metrics(first, last, elapsed, cgroup=False, schedstats=False):
    if cgroup:
        def fields(sample):
            return {k:int(v) for k,v in (line.split() for line in sample['cpu.stat'].splitlines())}
        a,b=fields(first),fields(last)
        result={'cpu_percent':(b['usage_usec']-a['usage_usec'])/elapsed/10000}
        if 'throttled_usec' in a and 'throttled_usec' in b:
            result['throttled_seconds_per_second']=(b['throttled_usec']-a['throttled_usec'])/1e6/elapsed
    else:
        result={'cpu_percent':(last['cpu_ticks']-first['cpu_ticks'])/os.sysconf('SC_CLK_TCK')/elapsed*100,
                'rss_end_bytes':last['rss_bytes'],
                'major_faults_per_second':(last['major_faults']-first['major_faults'])/elapsed}
        if schedstats:
            wait=0; matched=0
            for tid, end in last['threads'].items():
                start=first['threads'].get(tid)
                if start and start['starttime']==end['starttime']:
                    delta=int(end['schedstat'].split()[1])-int(start['schedstat'].split()[1])
                    if delta>=0:
                        wait+=delta; matched+=1
            if matched:
                result['surviving_threads_runqueue_wait_seconds_per_second']=wait/1e9/elapsed
    return {k:v for k,v in result.items() if v>=0 and math.isfinite(v)}


def capture(directory, *, pid=None, cgroup=None, duration=10, label='', stop=None, perf=True, expected_start=None, expected_inode=None, outcomes=None, scheduler=False):
    outcomes=outcomes or {}
    if any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in outcomes.values()):
        raise ValueError('Workload outcomes must be finite numbers')
    if (pid is None)==(cgroup is None):
        raise ValueError('Choose exactly one PID or cgroup')
    if not math.isfinite(duration) or not 0.2<=duration<=300:
        raise ValueError('Duration must be 0.2–300 seconds')
    if pid is not None and pid<=0:
        raise ValueError('PID must be positive')
    if cgroup:
        root=Path('/sys/fs/cgroup').resolve()
        cgroup=str(Path(cgroup).resolve())
        if not Path(cgroup).is_relative_to(root):
            raise ValueError('Cgroup must be inside /sys/fs/cgroup')
        generation=Path(cgroup).stat().st_ino
        if expected_inode is not None and generation!=expected_inode:
            raise ValueError('Cgroup identity changed; select it again')
    else:
        try:
            generation=identity(Path('/proc')/str(pid))
        except (OSError,ValueError) as exc:
            raise ValueError('Process exited or is unreadable') from exc
        if generation is None or (expected_start is not None and generation!=expected_start):
            raise ValueError('Process exited or PID was reused')
    directory=Path(directory).absolute()
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    stop=stop or threading.Event()
    info=dict(schema=1, label=label, target={'pid':pid,'cgroup':cgroup,'identity':generation},
              started=time.time(), requested_seconds=duration, status='collecting', samples=[], metrics={}, outcomes=outcomes,
              environment={'kernel':platform.release(),'machine':platform.machine(),'cpu_count':os.cpu_count(),
                           'perf_event_paranoid':read('/proc/sys/kernel/perf_event_paranoid'),
                           'sched_schedstats':read('/proc/sys/kernel/sched_schedstats'),
                           'online_cpus':read('/sys/devices/system/cpu/online')},
              warnings=['Resource changes do not establish causality or predict application latency.',
                        'Polling runqueue counters cover surviving threads only; trace-based intervals are reported separately.',
                        'Frame-pointer stacks may be incomplete; debug symbols and frame pointers improve attribution.',
                        'Counter and perf windows are approximately aligned; perf startup/decoding add overhead.'])
    if pid:
        info['environment']['affinity']=sorted(os.sched_getaffinity(pid))
        info['environment']['executable']=os.readlink(f'/proc/{pid}/exe') if Path(f'/proc/{pid}/exe').exists() else None
    else:
        info['environment']['limits']={name:read(Path(cgroup)/name) for name in ('cpu.max','cpu.weight','cpuset.cpus.effective','memory.max','io.max')}
    info['environment']['governors']={p.parent.name:read(p) for p in Path('/sys/devices/system/cpu/cpufreq').glob('policy*/scaling_governor')}
    process=None; log=None; trace=None; start=time.monotonic()
    info['profile']={'status':'disabled' if not perf else 'unavailable'}
    info['scheduler']={'status':'pending' if scheduler else 'disabled'}
    write(directory/'session.json',info)
    try:
        if scheduler:
            from .scheduler_trace import SchedulerTrace
            trace=SchedulerTrace(directory,pid,cgroup,duration)
        if perf:
            executable=shutil.which('perf')
            if executable:
                command=[executable,'record','-e','cpu-clock','-F','49','--call-graph','fp','-o',str(directory/'perf.data')]
                command += ['-a','-G',os.path.relpath(cgroup,'/sys/fs/cgroup')] if cgroup else ['-p',str(pid),'--no-inherit']
                command += ['--','sleep',str(duration)]
                info['profile']['command']=command
                log=open(os.open(directory/'perf.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w')
                process=subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
            else:
                info['profile']['reason']='perf is not installed; install the Linux perf package for this kernel.'
        while True:
            current=Path(cgroup).stat().st_ino if cgroup else identity(Path('/proc')/str(pid))
            if current!=generation:
                raise ValueError('Target exited or identity changed; partial session only')
            sample=target_sample(pid,cgroup)
            if pid and (sample['starttime']!=generation or identity(Path('/proc')/str(pid))!=generation):
                raise ValueError('PID identity changed during sampling')
            info['samples'].append({'elapsed':time.monotonic()-start,'data':sample})
            if stop.is_set() or time.monotonic()-start>=duration:
                break
            if trace and trace.over_limit():
                raise ValueError('Scheduler trace reached 64 MiB capture limit')
            if (directory/'perf.data').exists() and (directory/'perf.data').stat().st_size>64*1024*1024:
                raise ValueError('Profile reached 64 MiB capture limit')
            stop.wait(min(.25,max(0,duration-(time.monotonic()-start))))
        info['status']='cancelled' if stop.is_set() else 'complete'
    except KeyboardInterrupt:
        info['status']='cancelled'
        raise
    except (OSError,ValueError) as exc:
        info['status']='partial'; info['warnings'].append(str(exc))
    finally:
        if trace:
            try:
                info['scheduler']=trace.finish(cancelled=info['status']=='cancelled')
            except (OSError,ValueError) as exc:
                info['scheduler']={'status':'failed','reason':str(exc)}
        if log and not process:
            log.close()
        if process:
            if process.poll() is None:
                try:os.killpg(process.pid,signal.SIGINT)
                except ProcessLookupError:pass
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait()
            log.close()
            info['profile']['returncode']=process.returncode
            data=directory/'perf.data'
            if data.exists():data.chmod(0o600)
            info['profile']['status']='failed'
            if process.returncode in (0,-signal.SIGINT,128+signal.SIGINT) and data.exists() and data.stat().st_size>0:
                try:
                    with open(os.open(directory/'perf-report.txt',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as report:
                        decoded=subprocess.run([executable,'report','--stdio','--percent-limit','2','-i',str(data)],
                                               stdout=report,stderr=subprocess.STDOUT,timeout=5,
                                               env={**os.environ,'LC_ALL':'C','PERF_PAGER':'cat'})
                    if decoded.returncode==0:info['profile']['status']='recorded'
                except (OSError,subprocess.TimeoutExpired) as exc:
                    info['warnings'].append('Profile decoding failed: '+str(exc))
            info['profile']['diagnostic']=read(directory/'perf.log')
        info['elapsed_seconds']=time.monotonic()-start
        if len(info['samples'])>1:
            a,b=info['samples'][0],info['samples'][-1]
            info['metrics']=metrics(a['data'],b['data'],b['elapsed']-a['elapsed'],bool(cgroup),info['environment']['sched_schedstats']=='1')
        if info['scheduler']['status']=='complete':
            for kind,values in info['scheduler'].get('distributions',{}).items():
                if values['count']:
                    for stat in ('p50_ms','p95_ms','p99_ms','max_ms'):
                        info['metrics']['scheduler.'+kind+'.'+stat]=values[stat]
        if info['environment']['sched_schedstats']!='1':
            info['warnings'].append('Polling-based scheduler accounting unavailable/disabled; see the separate scheduler trace status.')
        write(directory/'session.final.json',info)
        os.replace(directory/'session.final.json',directory/'session.json')
    return info


def compare(baseline, candidate):
    def load(path):
        path=Path(path)
        files=[path/'session.json'] if (path/'session.json').exists() else sorted(path.glob('*/session.json'))
        if not files or len(files)>1000:raise ValueError('Expected 1–1000 saved sessions per comparison side')
        results=[]
        for file in files:
            with open(file) as f:r=json.load(f)
            if r.get('schema')!=1:raise ValueError('Unsupported session schema')
            results.append(r)
        return results
    a,b=load(baseline),load(candidate)
    warnings=['Descriptive comparison only; match workload, warmup, build and load before attributing a regression.',
              'CPU percentages and stack samples do not predict application latency.']
    if min(len(a),len(b))<3:warnings.append('Fewer than three repeats on at least one side; variability is poorly estimated.')
    if any(r['status']!='complete' for r in a+b):
        raise ValueError('Comparison requires complete sessions; exclude partial/cancelled captures')
    if len({bool(r['target'].get('cgroup')) for r in a+b})!=1:
        raise ValueError('Cannot compare a process with a cgroup scope')
    if len({r['label'] for r in a+b})!=1:warnings.append('Workload labels differ; verify equivalent workloads.')
    configurations=[]
    for r in a+b:
        if r['environment'] not in configurations:configurations.append(r['environment'])
    changes={k:{'baseline':a[0]['environment'].get(k),'candidate':b[0]['environment'].get(k)} for k in a[0]['environment'].keys()|b[0]['environment'].keys() if a[0]['environment'].get(k)!=b[0]['environment'].get(k)}
    if any(r['environment']!=side[0]['environment'] for side in (a,b) for r in side):
        warnings.append('Configuration varies within a repeat set; inspect configurations before interpreting means.')
    def summary(values):
        return dict(mean=statistics.mean(values),stdev=statistics.stdev(values) if len(values)>1 else None,
                    min=min(values),max=max(values),count=len(values))
    rows={}
    sets=[dict(r['metrics'],**{'outcome.'+k:v for k,v in r.get('outcomes',{}).items()}) for r in a+b]
    common=set.intersection(*(set(r) for r in sets))
    if any(set(r)!=common for r in sets):warnings.append('Some metrics are unavailable in some sessions; only common metrics compared.')
    for key in sorted(common):
        x=summary([r[key] for r in sets[:len(a)]]);y=summary([r[key] for r in sets[len(a):]])
        rows[key]={'baseline':x,'candidate':y,'delta':y['mean']-x['mean'],
                   'change_percent':100*(y['mean']-x['mean'])/x['mean'] if x['mean'] else None}
    if not any(k.startswith('outcome.') for k in rows):warnings.append('No matched application latency or throughput outcome was supplied.')
    scheduler_status={'baseline':[r.get('scheduler',{}).get('status','disabled') for r in a],
                      'candidate':[r.get('scheduler',{}).get('status','disabled') for r in b]}
    if any(status!='disabled' for side in scheduler_status.values() for status in side) and any(status!='complete' for side in scheduler_status.values() for status in side):
        warnings.append('Scheduler tracing is not complete for every run; consult scheduler status before comparing waits.')
    return dict(scheduler=scheduler_status,metrics=rows,environment_changes=changes,configurations=configurations,warnings=warnings,
                profiles={'baseline':[r['profile']['status'] for r in a],'candidate':[r['profile']['status'] for r in b]})


class ProfileView:
    def __init__(self, target):
        from .usability import state_dir
        import uuid
        self.directory=state_dir()/'profiles'/uuid.uuid4().hex
        self.stop=threading.Event();self.result=None;self.error=None;self.scroll=0
        self.target=target
        def worker():
            try:self.result=capture(self.directory,**target,stop=self.stop)
            except Exception as exc:self.error=str(exc)
        threading.Thread(target=worker,daemon=True).start()

    def key(self,key,height):
        if key in (27,ord('q'),curses.KEY_F10):self.stop.set();return False
        if key in (curses.KEY_DOWN,ord('j')):self.scroll+=1
        elif key in (curses.KEY_UP,ord('k')):self.scroll=max(0,self.scroll-1)
        return True

    def draw(self,screen,width,height):
        from .ui import put
        import textwrap
        target='PID '+str(self.target['pid']) if self.target.get('pid') else 'Cgroup '+str(self.target.get('cgroup'))
        lines=['INVESTIGATION / '+target+' / 10 seconds','Esc cancels collection and returns immediately.',str(self.directory)]
        if self.error:lines.append(self.error)
        elif not self.result:lines.append('Collecting in background; normal telemetry continues.')
        else:
            r=self.result
            lines += ['Session: '+r['status']+' | CPU profile: '+r['profile']['status']]
            from .ui import size
            labels={'cpu_percent':'CPU (% of one CPU)', 'rss_end_bytes':'Resident memory at end',
                    'major_faults_per_second':'Major faults / second',
                    'surviving_threads_runqueue_wait_seconds_per_second':'Runqueue wait (thread-seconds / second)',
                    'throttled_seconds_per_second':'Throttled seconds / second'}
            lines += [labels.get(k,k)+': '+(size(v) if k=='rss_end_bytes' else f'{v:.3f}') for k,v in r['metrics'].items() if not k.startswith('scheduler.')]
            if r.get('scheduler',{}).get('status','disabled')!='disabled':
                from .scheduler_trace import summary_lines
                lines += summary_lines(r['scheduler'])
            lines += r['warnings']
            if r['profile'].get('reason'):lines.append(r['profile']['reason'])
            if r['profile'].get('diagnostic'):lines.extend(r['profile']['diagnostic'].splitlines())
            if r['profile']['status']=='recorded':
                lines += (read(self.directory/'perf-report.txt') or '').splitlines()[:100]
            lines += ['Stacks: perf report -i '+str(self.directory/'perf.data'), 'Compare: resource-lens --compare BASELINE_DIR CANDIDATE_DIR']
        wrapped=[piece for line in lines for piece in (textwrap.wrap(line,max(10,width-3)) or [''])]
        self.scroll=min(self.scroll,max(0,len(wrapped)-max(1,height-5)))
        for i,line in enumerate(wrapped[self.scroll:self.scroll+max(1,height-5)]):put(screen,i+2,1,line)
