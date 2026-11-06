"""Read-only, snapshot-backed navigation of cgroups and their direct processes."""
import curses
from pathlib import PurePosixPath


class CgroupBrowser:
    def __init__(self, snapshot, path='/'):
        self.process_entry = False
        self.sort = 'cpu'
        self.trail = []
        self.path = path
        node = next((n for n in snapshot.get('cgroup_tree', {}).get('nodes', []) if n['path'] == path), None)
        self.inode = node.get('inode') if node else None
        self.selected = None
        self.process = None
        self.detail_scroll = 0
        self.context_detail = False
        self.context_region = None
        self.inspector = None
        self.pending_action = None
        self.regions = []
        self.back = None

    def node(self, snapshot):
        return next((n for n in snapshot.get('cgroup_tree', {}).get('nodes', [])
                     if n['path'] == self.path and n.get('inode') == self.inode), None)

    def ensure_inspector(self, snapshot):
        if self.inspector is not None:
            return self.inspector
        if not (self.process or self.context_detail):
            return None
        from .inspector import Inspector
        if self.process:
            pid, start = self.process
            row = next((p for p in snapshot.get('processes', []) if (p['pid'], p.get('starttime')) == self.process), None)
            request = ('process', pid, start, row.get('cgroup') if row else self.path)
        else:
            node = self.node(snapshot) or {}
            request = ('cgroup', self.path, self.inode, node.get('device'), 'visible')
        self.inspector = Inspector(request, tab=2 if self.context_detail else 0)
        return self.inspector

    def rows(self, snapshot):
        if not self.node(snapshot):
            return []
        groups = [('group', n['path'], n['inode'], n) for n in snapshot['cgroup_tree']['nodes']
                  if n['path'] != '/' and str(PurePosixPath(n['path']).parent) == self.path]
        processes = [('process', p['pid'], p.get('starttime'), p) for p in snapshot.get('processes', [])
                     if p.get('cgroup') == self.path]
        def order(row):
            data=row[3]
            if self.sort == 'name':
                return (str(data.get('name') or row[1]).casefold(),str(row[1]))
            field='cpu_pct' if self.sort == 'cpu' else ('memory_current' if row[0]=='group' else 'memory')
            value=data.get(field)
            return (value is None,-(value or 0),row[0],str(row[1]))
        return sorted(groups+processes,key=order)

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
            self.inspector = None
            self.process = (identity, generation)
            self.detail_scroll = 0
        else:
            self.trail.append((self.path, self.inode, self.selected))
            self.path, self.inode = identity, generation
            self.selected = None

    def up(self, snapshot):
        if self.process:
            self.process = None
            return True
        previous = self.path
        parent = str(PurePosixPath(previous).parent)
        node = next((n for n in snapshot.get('cgroup_tree', {}).get('nodes', []) if n['path'] == parent), None)
        if previous == '/':
            return False
        if self.trail and self.trail[-1][0] == parent:
            self.path, self.inode, self.selected = self.trail.pop()
        elif node:
            self.path, self.inode, self.selected = parent, node['inode'], None
            self.trail = []
        return True

    def key(self, key, snapshot, height):
        if key in (ord('q'), curses.KEY_F10):
            return False
        if self.inspector is not None:
            result = self.inspector.key(key, height)
            self.detail_scroll = self.inspector.scroll
            if result == 'handled':
                return True
            if result == 'close':
                self.inspector = None
            elif result is not None:
                self.pending_action = result
                return True
        if self.process_entry and key in (27,curses.KEY_LEFT,curses.KEY_BACKSPACE,127,8):
            return False
        if not self.process and key == ord('D'):
            self.context_detail = not self.context_detail
            self.detail_scroll = 0
            return True
        if self.context_detail and key in (27, curses.KEY_LEFT, curses.KEY_BACKSPACE, 127, 8):
            self.context_detail = False
            return True
        if key in (ord('c'),ord('m'),ord('n')):
            self.sort={ord('c'):'cpu',ord('m'):'memory',ord('n'):'name'}[key]
            return True
        if key == 27:
            if self.process:
                self.process = None
                return True
            if self.trail:
                self.path, self.inode, self.selected = self.trail.pop()
                return True
            return False
        if key in (curses.KEY_LEFT, curses.KEY_BACKSPACE, 127, 8):
            return self.up(snapshot)
        elif self.process or self.context_detail:
            step = max(1, height - 9)
            move = {curses.KEY_DOWN: 1, ord('j'): 1, curses.KEY_UP: -1, ord('k'): -1,
                    curses.KEY_NPAGE: step, curses.KEY_PPAGE: -step}.get(key)
            if move is not None:
                self.detail_scroll = max(0, self.detail_scroll + move)
            elif key == curses.KEY_HOME:
                self.detail_scroll = 0
            elif key == curses.KEY_END:
                self.detail_scroll = 10**9
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
        if self.inspector is not None:
            if state & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED) and y == 2 and 1 <= x < 14:
                return self.key(27, snapshot, height)
            result = self.inspector.mouse(x, y, state, height)
            self.detail_scroll = self.inspector.scroll
            if result and result != 'handled':
                self.pending_action = result
            return
        if state & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED):
            if self.context_region and y == self.context_region[2] and self.context_region[0] <= x < self.context_region[1]:
                self.context_detail = not self.context_detail
                self.detail_scroll = 0
                return
            if self.back and y == self.back[2] and self.back[0] <= x < self.back[1]:
                if self.context_detail:
                    self.context_detail = False
                    return
                return False if self.process_entry else self.up(snapshot)
            for start,end,row,identity in self.regions:
                if row == y and start <= x < end:
                    self.selected = identity
                    self.enter(snapshot)
                    return
        for button,key in ((curses.BUTTON4_PRESSED,curses.KEY_UP),(getattr(curses,'BUTTON5_PRESSED',0),curses.KEY_DOWN)):
            if state & button:
                for _ in range(3):self.key(key,snapshot,height)

    def draw(self, screen, snapshot, width, height, view=None):
        from .ui import put, pct, size
        self.regions, self.back = [], (1, 14, 2)
        self.context_region = None if self.process else (1, 18, 4)
        node = self.node(snapshot)
        if (self.process_entry or self.context_detail or self.process and node is not None):
            inspector = self.ensure_inspector(snapshot)
            if inspector:
                if view is None:
                    from types import SimpleNamespace
                    from .diagnostics import DiagnosticHistory
                    history = DiagnosticHistory(); history.observe(snapshot)
                    view = SimpleNamespace(snapshot=snapshot, replay=True, paused=False, diagnostic_history=history,
                        page=7, browser=self, application_mode='cgroups', application_process=None,
                        active_inspector=lambda: self.inspector)
                inspector.scroll = self.detail_scroll
                inspector.draw(screen, view, width, height)
                self.detail_scroll = inspector.scroll
                return
        put(screen,2,1,'[ < Back ]' if self.process_entry or self.process else '[ < Parent ]',curses.A_BOLD)
        put(screen,3,1,'PROCESS DETAILS' if self.process_entry else 'CGROUP BROWSER: '+self.path,curses.A_BOLD)
        put(screen,4,1,'Arrows: scroll | V: memory | P: investigate' if self.process else '[ D Limits ] | Enter: open | Left: parent')
        tree = snapshot.get('cgroup_tree')
        if tree is None and not self.process_entry:
            put(screen,6,1,'This recording has no cgroup hierarchy data. Live collection or a new recording is required.')
            return
        node = self.node(snapshot)
        if node is None and not self.process_entry:
            put(screen,6,1,'Cgroup disappeared, was replaced, or is outside collection coverage. Go to parent to reselect.')
            return
        if not self.process_entry:
            put(screen,5,1,f"Group CPU {pct(node.get('cpu_pct'))} | memory {size(node.get('memory_current'))} (includes descendants)")
        rows = self.rows(snapshot)
        selected = self.selection(rows)
        capacity = max(1,height-9)
        first = max(0,(selected or 0)-capacity+1)
        name_width = max(8, width - 23)
        put(screen,6,1,f"{'TYPE / NAME':<{name_width}} {'CPU':>7} {'MEMORY':>10}",curses.A_BOLD)
        for offset,(kind,identity,generation,data) in enumerate(rows[first:first+capacity]):
            name = PurePosixPath(identity).name+'/' if kind == 'group' else f"{identity} {data['name']}"
            label = ('CG ' if kind == 'group' else 'PID ')+name
            line = f"{label[:name_width]:<{name_width}} {pct(data.get('cpu_pct')):>7} {size(data.get('memory_current' if kind=='group' else 'memory')):>10}"
            y=7+offset
            put(screen,y,1,line,curses.A_REVERSE if (kind,identity,generation)==self.selected else 0)
            self.regions.append((1,width-1,y,(kind,identity,generation)))
        if not rows:put(screen,7,1,'No visible child cgroups or direct processes.')
        coverage = 'PARTIAL: node limit or unreadable directories' if tree.get('truncated') or tree.get('errors') else 'Visible cgroup directories'
        put(screen,height-2,1,coverage+' | PIDs belong directly here; child counters overlap parent counters.')
