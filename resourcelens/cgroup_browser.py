"""Read-only, snapshot-backed navigation of cgroups and their direct processes."""
import curses
from pathlib import PurePosixPath


class CgroupBrowser:
    def __init__(self, snapshot, path='/'):
        self.path = path
        node = next((n for n in snapshot.get('cgroup_tree', {}).get('nodes', []) if n['path'] == path), None)
        self.inode = node.get('inode') if node else None
        self.selected = None
        self.process = None
        self.regions = []
        self.back = None

    def node(self, snapshot):
        return next((n for n in snapshot.get('cgroup_tree', {}).get('nodes', [])
                     if n['path'] == self.path and n.get('inode') == self.inode), None)

    def rows(self, snapshot):
        if not self.node(snapshot):
            return []
        groups = [('group', n['path'], n['inode'], n) for n in snapshot['cgroup_tree']['nodes']
                  if n['path'] != '/' and str(PurePosixPath(n['path']).parent) == self.path]
        processes = [('process', p['pid'], p.get('starttime'), p) for p in snapshot.get('processes', [])
                     if p.get('cgroup') == self.path]
        return sorted(groups, key=lambda r:r[1]) + sorted(processes, key=lambda r:r[1])

    def selection(self, rows):
        if self.selected is None and rows:
            self.selected = rows[0][:3]
        return next((i for i,r in enumerate(rows) if r[:3] == self.selected), None)

    def enter(self, snapshot):
        rows = self.rows(snapshot)
        index = self.selection(rows)
        if index is None:
            return
        kind, identity, generation, data = rows[index]
        if kind == 'process':
            self.process = (identity, generation)
        else:
            self.path, self.inode = identity, generation
            self.selected = None

    def up(self, snapshot):
        if self.process:
            self.process = None
            return
        previous = self.path
        parent = str(PurePosixPath(previous).parent)
        node = next((n for n in snapshot.get('cgroup_tree', {}).get('nodes', []) if n['path'] == parent), None)
        if node:
            self.path, self.inode, self.selected = parent, node['inode'], None

    def key(self, key, snapshot, height):
        if key in (27, ord('q'), curses.KEY_F10):
            if self.process:
                self.process = None
                return True
            return False
        if key in (curses.KEY_LEFT, curses.KEY_BACKSPACE, 127, 8):
            self.up(snapshot)
        elif not self.process:
            rows = self.rows(snapshot)
            index = self.selection(rows)
            if key in (10, 13, curses.KEY_RIGHT):
                self.enter(snapshot)
            elif rows:
                step = max(1, height-9)
                move = {curses.KEY_DOWN:1, ord('j'):1, curses.KEY_UP:-1, ord('k'):-1,
                        curses.KEY_NPAGE:step, curses.KEY_PPAGE:-step}.get(key)
                if move is not None:
                    index = max(0, min(len(rows)-1, (index if index is not None else -1)+move))
                    self.selected = rows[index][:3]
                elif key in (curses.KEY_HOME, curses.KEY_END):
                    self.selected = rows[0 if key == curses.KEY_HOME else -1][:3]
        return True

    def mouse(self, x, y, state, snapshot, height):
        if state & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED):
            if self.back and y == self.back[2] and self.back[0] <= x < self.back[1]:
                self.up(snapshot)
            for start,end,row,identity in self.regions:
                if row == y and start <= x < end:
                    self.selected = identity
                    self.enter(snapshot)
                    return
        for button,key in ((curses.BUTTON4_PRESSED,curses.KEY_UP),(getattr(curses,'BUTTON5_PRESSED',0),curses.KEY_DOWN)):
            if state & button:
                for _ in range(3):self.key(key,snapshot,height)

    def draw(self, screen, snapshot, width, height):
        from .ui import put, pct, size
        self.regions, self.back = [], (1, 14, 2)
        put(screen,2,1,'[ < Parent ]',curses.A_BOLD)
        put(screen,3,1,'CGROUP BROWSER: '+self.path,curses.A_BOLD)
        put(screen,4,1,'Enter/Right or click: open | Left/Backspace: parent | Esc: Applications')
        tree = snapshot.get('cgroup_tree')
        if tree is None:
            put(screen,6,1,'This recording has no cgroup hierarchy data. Live collection or a new recording is required.')
            return
        node = self.node(snapshot)
        if node is None:
            put(screen,6,1,'Cgroup disappeared, was replaced, or is outside collection coverage. Go to parent to reselect.')
            return
        put(screen,5,1,f"Group CPU {pct(node.get('cpu_pct'))} | memory {size(node.get('memory_current'))} (includes descendants)")
        if self.process:
            pid,starttime = self.process
            p = next((p for p in snapshot.get('processes',[]) if (p['pid'],p.get('starttime')) == (pid,starttime) and p.get('cgroup') == self.path),None)
            if not p:
                put(screen,7,1,'Process exited, moved, or PID was reused. Left/Esc returns to this group.')
                return
            lines = [f"PID {pid}: {p['name']}", f"Parent PID: {p.get('ppid')} | state: {p.get('state')}",
                     f"CPU: {pct(p.get('cpu_pct'))} | RSS: {size(p.get('memory'))}",
                     f"Start ticks: {starttime}", 'Exact cgroup: '+self.path,
                     'Process details are read-only. Group controls remain in Applications.']
            for i,line in enumerate(lines):put(screen,7+i,1,line)
            return
        rows = self.rows(snapshot)
        selected = self.selection(rows)
        capacity = max(1,height-9)
        first = max(0,(selected or 0)-capacity+1)
        put(screen,6,1,'TYPE / NAME                                      CPU         MEMORY',curses.A_BOLD)
        for offset,(kind,identity,generation,data) in enumerate(rows[first:first+capacity]):
            name = PurePosixPath(identity).name+'/' if kind == 'group' else f"{identity} {data['name']}"
            label = ('CG ' if kind == 'group' else 'PID ')+name
            line = f"{label[:max(16,width-28)]:<{max(16,width-28)}} {pct(data.get('cpu_pct')):>7} {size(data.get('memory_current' if kind=='group' else 'memory')):>10}"
            y=7+offset
            put(screen,y,1,line,curses.A_REVERSE if (kind,identity,generation)==self.selected else 0)
            self.regions.append((1,width-1,y,(kind,identity,generation)))
        if not rows:put(screen,7,1,'No visible child cgroups or direct processes.')
        coverage = 'PARTIAL: node limit or unreadable directories' if tree.get('truncated') or tree.get('errors') else 'Visible cgroup directories'
        put(screen,height-2,1,coverage+' | PIDs belong directly here; child counters overlap parent counters.')
