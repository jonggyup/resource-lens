"""Hardware/application navigation with explicit accounting and placement semantics."""
import curses
import textwrap
from .hardware import cpulist

TABS = ('Usage', 'Processes', 'Placement', 'Limits', 'History')


def allowed_cpus(group):
    for ancestor in group.get('limits', {}).get('ancestors', []):
        value = ancestor.get('cpuset.cpus.effective')
        if value:
            return set(cpulist(value))
    return None


def io_rate(group, direction):
    values = [d.get(direction+'_bps') for d in group.get('io_devices', [])]
    return sum(values) if values and all(v is not None for v in values) else None


def related_groups(snapshot, relation):
    groups = snapshot.get('workloads', [])
    if relation is None:
        return groups
    kind, target = relation
    if kind == 'cpu':
        return [g for g in groups if (allowed_cpus(g) or set()) & set(target)]
    if kind == 'disk':
        # The identity includes the sysfs inode: don't silently attach a replacement.
        disk = next((d for d in snapshot.get('disks', []) if d.get('identity') == target), None)
        if disk is None:
            return []
        dev = ':'.join(target.split(':')[:2])
        return [g for g in groups if any(d['device'] == dev and
                ((d.get('read_bps') or 0) > 0 or (d.get('write_bps') or 0) > 0)
                for d in g.get('io_devices', []))]
    if kind == 'memory':
        return [g for g in groups if (g.get('memory_current') or 0) > 0]
    return []


class Workspaces:
    def workspace_init(self):
        self.app_tab = 0
        self.relation = None
        self.relation_label = ''
        self.hardware_selection = None
        self.app_regions = []
        self.app_tab_regions = []
        self.app_history_identity = {}

    def workspace_key(self, key):
        if self.page == 7 and key == ord('D'):
            self.workload_detail = not self.workload_detail
            self.scroll = 0
        elif self.page == 7 and key in (ord('['),ord(']')):
            self.app_tab = (self.app_tab + (1 if key == ord(']') else -1)) % len(TABS)
            if self.screen_size[1] < 100 or self.screen_size[0] < 20:
                self.workload_detail = True
            self.scroll = 0
        elif self.page == 7 and key == ord('x'):
            self.relation, self.relation_label = None, ''
            self.scroll = 0
        elif key == ord('a') and self.page in (1,2,3,4,6):
            self.open_related()
        else:
            return False
        return True

    def open_related(self):
        sample = self.snapshot
        if not sample:
            return
        if self.page == 1:
            ids = self.hardware_selection
            if not ids:
                ids = [c['id'] for c in sample.get('cpu_hardware', {}).get('cpus', [])
                       if not self.focus or str(c['socket']) == self.focus[1]]
            self.hardware_selection = None
            self.relation = ('cpu', tuple(ids))
            self.relation_label = 'Allowed on CPUs '+','.join(map(str,ids))+'; not measured execution'
        elif self.page == 3:
            if not self.focus or self.focus[0] != 'disk':
                self.open_focus()
                return
            disk = next((d for d in sample['disks'] if d['name']==self.focus[1]),None)
            if not disk or not disk.get('identity'):
                self.modal = dict(stage='result',message='Disk identity unavailable in this sample; no attribution inferred.')
                return
            self.relation = ('disk',disk['identity'])
            self.relation_label = 'Active I/O on '+disk['name']+'; matching accounting device only'
        elif self.page == 2:
            self.relation = ('memory',None)
            self.relation_label = 'Accounted memory > 0; NUMA-node attribution unavailable'
        else:
            self.modal = dict(stage='result',message='Per-application GPU/network attribution is unavailable. Host utilization is not attributed to applications.')
            return
        self.page, self.scroll, self.focus = 7, 0, None
        self.workload_id, self.workload_identity = None, None
        self.workload_notice, self.workload_detail = '', False

    def application_lines(self, width):
        from .ui import pct, size, bar, ratio
        from .investigation import indicators
        group = self.selected_workload()
        if not group:
            return ['No selected application.', self.workload_notice or 'Select a visible workload using arrows or click.',
                    self.relation_label, 'x clears the hardware relationship filter.']
        lines = [group.get('unit') or group['id'],
                 'Grouping: '+group.get('kind','cgroup')+' | manager: '+str(group.get('manager') or 'unknown'),
                 'Identity: '+group['id']+' | inode '+str(group.get('inode')),
                 self.workload_notice or 'A workload group is not necessarily one desktop application.',
                 ' | '.join(('['+name+']') if i==self.app_tab else name for i,name in enumerate(TABS)),
                 '[ / ] tabs | D detail focus | Esc list focus', '']
        if self.app_tab == 0:
            lines += [f"CPU {pct(group.get('cpu_pct'))} (100% = one logical CPU)",
                      'Accounted memory '+size(group.get('memory_current')),
                      'Share of host RAM '+bar(ratio(group.get('memory_current'),self.snapshot['memory'].get('total')),min(26,max(8,width-22))),
                      f"Block I/O: read {size(io_rate(group,'read'))}/s | write {size(io_rate(group,'write'))}/s",
                      *indicators(self.snapshot,group), '',
                      'o: allowed CPU overlay | d: first mapped accounting disk',
                      'GPU / network per-application usage: unavailable',
                      'Nested cgroup counters can overlap; do not sum rows.','', 'Accounting devices:']
            cpu_values = self.history['workload:'+group['id']]
            maximum = max(100,max((v for v in cpu_values if v is not None),default=100))
            lines[7:7] = [f'CPU history 0-{maximum:.0f}%:',self.trend(cpu_values,max(8,width-4),maximum)]
            for dev in group.get('io_devices', []):
                name = next((d['name'] for d in self.snapshot['disks'] if ':'.join(d.get('identity','').split(':')[:2])==dev['device']), 'not mapped to a visible whole disk')
                lines.append(f"{dev['device']} ({name}) R {size(dev.get('read_bps'))}/s W {size(dev.get('write_bps'))}/s")
        elif self.app_tab == 1:
            from .usability import tree_rows
            pids = set(group.get('pids', []))
            members = [p for p in self.snapshot['processes'] if p['pid'] in pids]
            lines += ['Visible members; nested units are grouped separately.']
            for process, depth in tree_rows(members):
                lines.append('  '*min(depth,6)+f"{process['pid']} {process['name']} CPU {pct(process.get('cpu_pct'))} RSS {size(process.get('memory'))}")
        elif self.app_tab == 2:
            ids = allowed_cpus(group)
            lines += ['Allowed CPUs: '+(','.join(map(str,sorted(ids))) if ids is not None else 'unknown'),
                      'Allowed placement is not per-core execution attribution.',
                      'Thread affinity and child cgroups may narrow this set.',
                      'o opens the CPU topology overlay; p edits placement with --control.',
                      'NUMA memory residency: unavailable; no node use inferred.']
        elif self.app_tab == 3:
            limits = group.get('limits', {})
            lines += ['Tightest observed CPU quota: '+pct(limits.get('quota_pct')),
                      'Quota ancestry: '+('complete' if limits.get('quota_complete') else 'incomplete; tighter limits may be unreadable'),
                      'Tightest observed memory maximum: '+size(limits.get('memory_max'))]
            lines += ['w weight | l quota | p placement | b disk bandwidth',
                      'u Undo CPU | U Undo placement | B Undo disk',
                      'Controls require live --control mode and authorization.',
                      'Local and ancestor limits (nearest first):']
            for ancestor in group.get('limits',{}).get('ancestors',[]):
                lines += [ancestor['path']]
                for key in ('cpu.max','cpu.weight','cpuset.cpus.effective','memory.high','memory.max','io.max'):
                    value = ancestor.get(key)
                    lines.append('  '+key+': '+(value if value is not None else 'unavailable/inactive'))
        else:
            key = 'workload:'+group['id']
            values = self.history[key]
            maximum = max(100,max((v for v in values if v is not None),default=100))
            lines += [f'CPU history (0-{maximum:.0f}%):',self.trend(values,max(8,width-4),maximum),
                      'History resets when the cgroup inode changes.', 'M baseline | N named marker | 9 Timeline',
                      'Pin with o first to target a comparison at this application.']
            for event in self.investigation.events[-50:]:
                target = event.get('target') or {}
                if target.get('id')==group['id'] and target.get('inode')==group.get('inode'):
                    lines.append('['+event['status']+'] '+event['label'])
        return [part for line in lines for part in (textwrap.wrap(str(line),max(20,width)) or [''])]

    def draw_applications(self, screen, width, height):
        from .ui import put, pct, size
        self.app_regions, self.app_tab_regions = [], []
        rows = self.workload_rows()
        left = min(44,max(30,width//3))
        capacity = max(1,height-9)
        selected = next((i for i,g in enumerate(rows) if g['id']==self.workload_id),0)
        first = max(0,min(selected-capacity+1,len(rows)-capacity))
        put(screen,3,1,'APPLICATIONS / '+str(len(rows))+' groups',curses.A_BOLD,left-2)
        put(screen,4,1,'/ search | c/m/n/i sort',limit=left-2)
        put(screen,5,1,self.relation_label or 'Explicit cgroup/systemd grouping',limit=left-2)
        put(screen,6,1,'NAME                 CPU / RAM',limit=left-2)
        for i,group in enumerate(rows[first:first+capacity]):
            y=7+i
            name=group.get('unit') or group['id']
            attr=curses.A_REVERSE if group['id']==self.workload_id else 0
            put(screen,y,1,f"{name[:18]:18} {pct(group.get('cpu_pct'))} {size(group.get('memory_current'))}",attr,left-2)
            self.app_regions.append((1,left-1,y,group['id'],group.get('inode')))
        for y in range(3,height-2):put(screen,y,left,'|')
        x=left+2
        for i,name in enumerate(TABS):
            label=' '+name+' '
            if x+len(label)>width-1:break
            put(screen,3,x,label,curses.A_REVERSE if i==self.app_tab else curses.A_BOLD)
            self.app_tab_regions.append((x,x+len(label),3,i))
            x+=len(label)
        lines=self.application_lines(width-left-4)
        count=max(1,height-7)
        if self.workload_detail:
            self.scroll=min(self.scroll,max(0,len(lines)-count))
        else:
            self.scroll=0
        for i,line in enumerate(lines[self.scroll:self.scroll+count]):
            put(screen,5+i,left+2,line,limit=width-left-4)
        return 'Applications | '+('detail scroll' if self.workload_detail else 'arrows select')+' | [ / ] tabs | x clear relationship'
