"""Topology-based workload CPU selection."""
from .impact import impact_lines, impact_summary
import curses
import textwrap
from .placement import PlacementSession, format_cpus


class PlacementView:
    def placement_init(self):
        self.placement_session = PlacementSession()
        self.placement = None
        self.topology_regions = {}
        self.topology_line_regions = {}

    def open_placement(self):
        if getattr(self, 'replay', None):
            self.modal = dict(stage='result', message='Replay is read-only; resource controls are disabled.')
            return
        if not self.control:
            self.open_control_mode()
            return
        group = self.selected_workload()
        if not group or not group.get('controllable'):
            self.modal = dict(stage='result', message='Select a controllable service/scope on Applications.')
            return
        def inspect():
            return dict(target=dict(group), info=self.placement_session.backend.snapshot(group))
        self.job('placement_open', inspect)

    def placement_ready(self, result):
        info = result['info']
        self.focus = None
        self.placement = dict(target=result['target'], info=info, selected=set(info['effective']),
                              inherit=not info['allowed'], focus=next(iter(info['eligible']), None))
        self.page, self.scroll, self.modal, self.help = 1, 0, None, False

    def placement_toggle(self, ids):
        p = self.placement
        valid = set(ids) & set(p['info']['eligible'])
        if not valid:
            return
        if valid <= p['selected']:
            p['selected'] -= valid
        else:
            p['selected'] |= valid
        p['inherit'] = False

    def placement_preview(self):
        p = self.placement
        self.job('placement_prepare', self.placement_session.prepare, p['target'],
                 None if p['inherit'] else sorted(p['selected']))

    def placement_lines(self):
        plan = self.modal['plan']
        return ['Confirm service CPU placement', 'Unit: ' + plan['target']['unit'],
                'Previous configured CPUs: ' + (format_cpus(plan['before']['allowed']) or 'inherit'),
                'Requested CPUs: ' + (format_cpus(plan['value']) or 'inherit parent'),
                'Expected effective CPUs: ' + format_cpus(plan['effective']),
                'Applies to the service and descendants, including new threads.',
                'CPU capacity is shared; this does not reserve exclusive cores.',
                'Task affinity and child cgroups can narrow placement further.',
                'The CPU set remains after exit. U on Applications undoes it.',
                *impact_summary(self.snapshot, plan, 'placement'),
                '[ Apply ] Enter: apply and verify', '[ Cancel ] Esc: cancel']

    def placement_confirm_key(self, key):
        if key == 27:
            self.modal = None
        elif key in (10, 13):
            width = max(20, min(76, self.screen_size[1] - 8))
            if sum(max(1, len(textwrap.wrap(line, width))) for line in self.placement_lines()) <= self.screen_size[0] - 5:
                self.job('placement_apply', self.placement_session.apply, self.modal['plan'])
        return True

    def placement_key(self, key):
        p = self.placement
        if key in (27, ord('q')):
            self.placement = None
            self.page, self.scroll = 7, 0
        elif key in (10, 13):
            self.placement_preview()
        elif key == ord('a'):
            p['selected'], p['inherit'] = set(p['info']['eligible']), False
        elif key == ord('n'):
            p['selected'], p['inherit'] = set(), False
        elif key == ord('r'):
            p['selected'], p['inherit'] = set(p['info']['eligible']), True
        elif key == ord(' '):
            self.placement_toggle([p['focus']])
        elif key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_LEFT, curses.KEY_RIGHT):
            cpus = p['info']['eligible']
            if cpus:
                positions = {ids[0]: (row, start) for row, hits in self.topology_line_regions.items()
                             for start, _, kind, ids in hits if kind == 'cpu' and ids[0] in cpus}
                origin = positions.get(p['focus'])
                choices = []
                if origin:
                    for cpu, (row, col) in positions.items():
                        dy, dx = row - origin[0], col - origin[1]
                        if key == curses.KEY_UP and dy < 0 or key == curses.KEY_DOWN and dy > 0:
                            choices.append((abs(dx), abs(dy), cpu))
                        elif key == curses.KEY_LEFT and dx < 0 or key == curses.KEY_RIGHT and dx > 0:
                            choices.append((abs(dy), abs(dx), cpu))
                if choices:
                    p['focus'] = min(choices)[2]
                elif not positions:
                    index = cpus.index(p['focus']) if p['focus'] in cpus else 0
                    p['focus'] = cpus[(index + (-1 if key in (curses.KEY_UP, curses.KEY_LEFT) else 1)) % len(cpus)]
                for row, hits in self.topology_line_regions.items():
                    if any(kind == 'cpu' and p['focus'] in ids for _, _, kind, ids in hits):
                        self.scroll = max(0, row - max(1, self.screen_size[0] - 9))
                        break
        elif key == curses.KEY_NPAGE:
            self.scroll += max(1, self.screen_size[0] - 5)
        elif key == curses.KEY_PPAGE:
            self.scroll = max(0, self.scroll - max(1, self.screen_size[0] - 5))
        return True
