"""Process-first Applications view with identity-stable selection."""
import curses
from .cgroup_browser import CgroupBrowser


class ApplicationProcesses:
    def application_process_init(self):
        self.application_mode = 'processes'
        self.application_process = None
        self.application_process_manual = False
        self.application_process_regions = []
        self.application_mode_region = None

    def application_process_rows(self):
        rows = [p for p in self.snapshot.get('processes', []) if self.filter.casefold() in
                f"{p['pid']} {p['name']} {p.get('cgroup') or ''}".casefold()] if self.snapshot else []
        field = {'cpu':'cpu_pct','memory':'memory','name':'name','pid':'pid'}[self.sort]
        def order(p):
            value = p.get(field)
            if self.sort in ('cpu','memory'):
                return (value is None, -(value or 0), p['pid'])
            return (value is None, value if value is not None else '', p['pid'])
        return sorted(rows,key=order)

    def selected_application_process(self, rows):
        if (self.application_process is None or not self.application_process_manual) and rows:
            self.application_process = (rows[0]['pid'],rows[0].get('starttime'))
        return next((i for i,p in enumerate(rows) if (p['pid'],p.get('starttime')) == self.application_process),None)

    def application_process_key(self, key, height):
        if self.page != 7 or self.application_mode != 'processes':
            return False
        if key in (curses.KEY_F4,ord('/')):
            self.filter_context,self.editing_filter,self.scroll = 'processes',True,0
            return True
        if key in map(ord,'cmnp'):
            self.sort = {ord('c'):'cpu',ord('m'):'memory',ord('n'):'name',ord('p'):'pid'}[key]
            return True
        rows = self.application_process_rows()
        index = self.selected_application_process(rows)
        if key in (10,13,curses.KEY_RIGHT,ord('v')):
            if index is not None:
                self.application_process_manual = True
                p = rows[index]
                self.browser = CgroupBrowser(self.snapshot,p.get('cgroup') or '/')
                self.browser.process = (p['pid'],p.get('starttime'))
                self.browser.process_entry = True
            return True
        moves = {curses.KEY_UP:-1,ord('k'):-1,curses.KEY_DOWN:1,ord('j'):1,
                 curses.KEY_PPAGE:-max(1,height-8),curses.KEY_NPAGE:max(1,height-8)}
        if key in moves or key in (curses.KEY_HOME,curses.KEY_END):
            if rows:
                index = max(0,min(len(rows)-1,(index if index is not None else -1)+moves.get(key,0)))
                if key == curses.KEY_HOME:index=0
                if key == curses.KEY_END:index=len(rows)-1
                self.application_process_manual = True
                self.application_process = (rows[index]['pid'],rows[index].get('starttime'))
            return True
        if key in map(ord,'wlbuUBDo[]xid'):
            # These are group controls/metrics. Never apply them to a hidden group.
            self.modal = dict(stage='result',message='Switch to cgroups with g and explicitly select a group for group metrics or controls.')
            return True
        return False

    def draw_application_processes(self, screen, width, height):
        from .ui import put,pct,size
        self.application_process_regions=[]
        rows=self.application_process_rows()
        selected=self.selected_application_process(rows)
        capacity=max(1,height-8)
        first=max(0,(selected or 0)-capacity+1)
        put(screen,3,1,f'APPLICATIONS / PROCESSES | {len(rows)} visible | sort: {self.sort} | filter: {self.filter or "none"}',curses.A_BOLD)
        put(screen,4,1,'Enter: details | P: investigate | V: memory | /: filter | ?: all shortcuts')
        name_width=max(8,min(24,width-29))
        put(screen,5,1,f"{'PID':<7} {'NAME':<{name_width}} {'CPU':>7} {'RAM (RSS)':>10}  CGROUP",curses.A_BOLD)
        for offset,p in enumerate(rows[first:first+capacity]):
            y=6+offset
            line=f"{p['pid']:<7} {p['name'][:name_width]:<{name_width}} {pct(p.get('cpu_pct')):>7} {size(p.get('memory')):>10}  {p.get('cgroup') or 'unknown'}"
            identity=(p['pid'],p.get('starttime'))
            put(screen,y,1,line,curses.A_REVERSE if identity==self.application_process else 0)
            self.application_process_regions.append((1,width-1,y,identity))
        if not rows:put(screen,6,1,'No matching visible processes. / edits the search.')
        elif selected is None:put(screen,4,1,'Selection exited, changed identity or was filtered out; use arrows to select again.')
        return 'Processes | CPU 100% = one logical CPU | g: cgroup view'
