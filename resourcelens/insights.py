"""Snapshot-only discovery, explanations and measurement health. Never writes settings."""
import curses
import time
from .focus import candidates
from .readability import wrap_lines

# title, definition, source, limitation, useful next view
METRICS = [
    ('CPU usage', 'Host usage is the busy fraction of all logical CPUs. A process at 100% uses one logical CPU.', '/proc/stat and /proc/PID/stat interval deltas', 'Usage alone does not explain latency or whether a faster CPU would help.', 1),
    ('CPU waiting (PSI)', 'The fraction of time at least one task is waiting for CPU. avg10 is a rolling 10-second average.', '/proc/pressure/cpu; cgroup cpu.pressure', 'PSI measures resource pressure, not request latency or a sum of thread wait times.', 9),
    ('Memory RSS / PSS', 'RSS counts resident mappings. PSS apportions shared pages across their mappings.', '/proc/PID/stat; V reads smaps_rollup or smaps', 'Adding RSS across processes can double-count shared memory. Threads share an address space.', 7),
    ('Memory available', 'Estimated RAM available for new work without swapping, including reclaimable memory.', '/proc/meminfo: MemAvailable', 'NUMA free memory is different: non-free pages may still be reclaimable.', 2),
    ('Disk activity and queue', 'Active time is the fraction of an interval with I/O active. Queue depth is a time-averaged outstanding I/O count.', '/proc/diskstats interval counters', 'Active time alone is not saturation. Parallel devices and cache behavior change its meaning.', 3),
    ('Network throughput', 'Received and transmitted bytes per second for an interface.', '/proc/net/dev interval deltas', 'Layered interfaces can count the same traffic. This is not application throughput or link latency.', 6),
    ('Energy and power', 'Joules are energy consumed over a measured interval. Watts are joules divided by elapsed seconds.', 'Exposed powercap energy_uj and hwmon energy inputs', 'Hardware domains may overlap. Never sum them blindly or attribute them to a selected process.', 9),
    ('CPU quota throttling', 'Time a cgroup was throttled after exhausting its CPU quota.', 'cgroup cpu.stat throttled_usec interval deltas', 'Parent limits also apply. This is not a direct measurement of request delay.', 7),
    ('GPU utilization', 'Device activity reported by the available GPU driver or NVIDIA utility.', 'Driver sysfs counters; optional nvidia-smi', 'Driver definitions differ. Missing telemetry means unavailable, not idle.', 4),
]


def health_rows(sample, replay=False, now=None, stale_after=5):
    """Availability is per metric; freshness is only as precise as the snapshot."""
    sample = sample or {}
    age = max(0, (time.time() if now is None else now) - sample.get('timestamp', 0))
    def row(name, value, source, step, reason=''):
        status = 'recorded' if replay and value is not None else ('stale' if age > stale_after else 'live') if value is not None else 'unavailable'
        # Only report permission denial when the collector explicitly says so.
        if value is None and any(word in reason.lower() for word in ('permission denied', 'operation not permitted')):
            status = 'permission denied'
        return (name, status, source, reason or ('No usable value in this sample.' if value is None else f'Snapshot age: {age:.1f}s' if not replay else 'Historical sample; not current hardware state.'), step)
    cpu = next((c for c in sample.get('cpu', []) if c['id'] == 'cpu'), {})
    rows = [row('Host CPU', cpu.get('busy_pct'), '/proc/stat', 'Open CPU (2); allow two samples for interval rates.'),
            row('Available RAM', sample.get('memory', {}).get('available'), '/proc/meminfo', 'Open Memory (3); check whether /proc/meminfo is readable.')]
    for kind in ('cpu','memory','io'):
        rows.append(row(kind.upper()+' pressure', sample.get('pressure',{}).get(kind,{}).get('some',{}).get('avg10'), '/proc/pressure/'+kind, 'Check kernel PSI support and visibility in this namespace.'))
    for field, key, label, source, step in (
        ('disks','read_bps','Disk','/proc/diskstats','Open Disk (4); allow two samples and check device visibility.'),
        ('network','rx_bps','Network','/proc/net/dev','Open Network (7); check interface visibility and allow two samples.'),
        ('gpus','busy_pct','GPU','driver / optional nvidia-smi','Open GPU (5); check driver telemetry support.')):
        items = sample.get(field, [])
        if not items:rows.append(row(label, None, source, step))
        for item in items:rows.append(row(label+' '+str(item.get('name') or item.get('id')), item.get(key), source, step, str(item.get('error') or '')))
    domains = sample.get('energy',{}).get('domains',[])
    if not domains:rows.append(row('Energy', None, 'powercap / hwmon', 'Open Status (0); hardware or the host may not expose energy counters.'))
    for d in domains:
        rows.append(row('Energy '+d['label'], d.get('watts'), d['id'], 'Open Status (0); inspect counter availability, permissions and gaps.', d.get('reason','')))
    return rows


def search_entries(sample, names):
    entries = [('view', i, name) for i,name in enumerate(names)]
    entries += [('resource',(kind,identity),label) for kind,identity,label in candidates(sample)]
    entries += [('core', c['id'], f"CPU {c['id']} / socket {c['socket']} / core {c['core']}") for c in sample.get('cpu_hardware',{}).get('cpus',[])]
    entries += [('process',(p['pid'],p.get('starttime')),f"PID {p['pid']} {p['name']} {p.get('cgroup') or ''}") for p in sample.get('processes',[])]
    entries += [('group',(g['path'],g.get('inode')),'Cgroup '+g['path']) for g in sample.get('cgroup_tree',{}).get('nodes',[])]
    seen={identity for kind,identity,_ in entries if kind=='group'}
    entries += [('group',(g['id'],g.get('inode')),'Service '+(g.get('unit') or g['id'])) for g in sample.get('workloads',[]) if (g['id'],g.get('inode')) not in seen]
    return entries


class InsightsView:
    def open_insights(self, mode):
        sample = self.snapshot or {}
        if mode == 'search':
            items = search_entries(sample,self.names)
        elif mode == 'metrics':items = [('metric',i,row[0]) for i,row in enumerate(METRICS)]
        else:
            rows = health_rows(sample,bool(self.replay),stale_after=getattr(self,'health_stale_after',5))
            items = [('health',row,row[0]+' ['+row[1]+']') for row in rows]
        self.modal = dict(stage='insights',mode=mode,items=items,query='',selected=0,scroll=0)

    def insight_items(self):
        query=self.modal['query'].casefold().split()
        return [item for item in self.modal['items'] if all(word in item[2].casefold() for word in query)]

    def insight_lines(self):
        m=self.modal
        if m.get('detail'):
            lines=wrap_lines(m['detail'],max(20,min(76,self.screen_size[1]-8)))
            count=max(1,self.screen_size[0]-10)
            m['scroll']=min(m['scroll'],max(0,len(lines)-count))
            return ['METRIC GUIDE' if m['mode']=='metrics' else 'MEASUREMENT HEALTH',
                    *lines[m['scroll']:m['scroll']+count], '', 'Arrows: scroll | Enter: related view | Esc: list']
        items=self.insight_items()
        m['selected']=min(m['selected'],max(0,len(items)-1))
        count=max(1,self.screen_size[0]-12)
        start=m['selected']//count*count;m['offset']=start
        title={'search':'FIND ANYTHING','metrics':'EXPLAIN A METRIC','health':'MEASUREMENT HEALTH'}[m['mode']]
        width=max(8,min(74,self.screen_size[1]-10))
        return [title,'Search: '+m['query']+'_',f'{len(items)} matches | arrows select | Enter or click opens',
                *[('> ' if i==m['selected'] else '- ')+item[2][:width] for i,item in enumerate(items) if start<=i<start+count],
                *([] if items else ['No matches. Backspace edits the search.']),
                'Snapshot-based results. Esc closes.']

    def insight_key(self,key):
        m=self.modal
        if key==27:
            if m.get('detail'):m.pop('detail');m['scroll']=0
            else:self.modal=None
            return True
        if m.get('detail'):
            if key in (10,13):
                self.page=m['related'];self.scroll=0;self.focus=None;self.browser=None;self.help=False;self.modal=None
            elif key in (curses.KEY_DOWN,curses.KEY_NPAGE):m['scroll']+=1 if key==curses.KEY_DOWN else max(1,self.screen_size[0]-10)
            elif key in (curses.KEY_UP,curses.KEY_PPAGE):m['scroll']=max(0,m['scroll']-(1 if key==curses.KEY_UP else max(1,self.screen_size[0]-10)))
            return True
        items=self.insight_items()
        if key in (curses.KEY_DOWN,curses.KEY_UP):
            if items:m['selected']=(m['selected']+(1 if key==curses.KEY_DOWN else -1))%len(items)
        elif key in (10,13) and items:
            kind,identity,label=items[min(m['selected'],len(items)-1)]
            if kind=='metric':
                title,meaning,source,limit,page=METRICS[identity]
                m.update(detail=[title,'','Meaning: '+meaning,'','Source: '+source,'','Limits: '+limit],related=page)
            elif kind=='health':
                name,status,source,reason,step=identity
                m.update(detail=[name,'State: '+status,'Source: '+source,'',reason,'','Next step: '+step,
                                 'Freshness is snapshot-level, not a per-sensor timestamp.'],related=9)
            else:self.open_search_result(kind,identity)
        elif key in (curses.KEY_BACKSPACE,127,8):m['query']=m['query'][:-1];m['selected']=0
        elif 32<=key<127 and len(m['query'])<80:m['query']+=chr(key);m['selected']=0
        return True

    def open_search_result(self,kind,identity):
        from .cgroup_browser import CgroupBrowser
        sample=self.snapshot or {}
        self.browser=None;self.focus=None;self.help=False;self.scroll=0
        if kind=='view':self.page=identity
        elif kind=='resource':
            self.page={'cpu':1,'memory':2,'disk':3,'gpu':4,'network':6}[identity[0]];self.focus=identity
        elif kind=='core':
            cpu=next((c for c in sample.get('cpu_hardware',{}).get('cpus',[]) if c['id']==identity),None)
            if cpu:self.page=1;self.focus=('cpu',str(cpu['socket']));self.hardware_selection=[identity]
            else:return self.search_gone()
        elif kind=='process':
            p=next((p for p in sample.get('processes',[]) if (p['pid'],p.get('starttime'))==identity),None)
            if not p:return self.search_gone()
            self.page=7;self.application_mode='processes'
            self.browser=CgroupBrowser(sample,p.get('cgroup') or '/');self.browser.process=identity;self.browser.process_entry=True
        elif kind=='group':
            path,inode=identity
            node=next((n for n in sample.get('cgroup_tree',{}).get('nodes',[]) if (n['path'],n.get('inode'))==identity),None)
            group=next((g for g in sample.get('workloads',[]) if (g['id'],g.get('inode'))==identity),None)
            if not node and not group:return self.search_gone()
            self.page=7;self.application_mode='cgroups';self.relation=None;self.workload_filter=''
            if node:self.browser=CgroupBrowser(sample,path)
            else:self.workload_id=path;self.workload_identity=identity;self.workload_detail=True
        self.modal=None

    def search_gone(self):
        self.modal=dict(stage='result',message='This result exited, changed identity or is no longer visible. Press s to search again after closing this message.')
