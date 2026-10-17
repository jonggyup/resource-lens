"""Mouse and keyboard CPU menus, shared by the curses view."""
import curses
from .hardware import Hardware


class HardwareView:
    def hardware_init(self):
        self.hardware = Hardware()
        self.cpu_regions = {}
        self.modal_regions = {}

    def hardware_menu(self, scope=('all', None)):
        if getattr(self, 'replay', None):
            self.modal = dict(stage='result', message='Replay is read-only; resource controls are disabled.')
            return
        if not self.control:
            self.modal = dict(stage='result', message='Restart with --control to enable CPU settings.')
            return
        data = self.hardware.snapshot()
        cpus = [c for c in data['cpus'] if scope[0] == 'all' or c[scope[0]] == scope[1]]
        options = []
        if scope[0] == 'id' and cpus and cpus[0]['socket'] is not None:
            options.append((f"Socket {cpus[0]['socket']} settings", ('scope', ('socket', cpus[0]['socket']))))
        if scope[0] != 'all':
            options.append(('All CPUs / all sockets', ('scope', ('all', None))))
        else:
            for socket in sorted({c['socket'] for c in cpus if c['socket'] is not None}):
                options.append((f'Socket {socket} settings', ('scope', ('socket', socket))))
            for cpu in cpus:
                options.append((f"CPU {cpu['id']} settings", ('scope', ('id', cpu['id']))))
        policies = [data['policies'][c['policy']] for c in cpus if c['policy']]
        governors = set.intersection(*(set(p['available']) for p in policies)) if policies and len(policies) == len(cpus) else set()
        scope_options = options if scope[0] == 'all' else []
        if scope[0] == 'all':
            options = []
        for governor in sorted(governors):
            options.append((f'Governor: {governor}', ('prepare', 'governor', governor)))
        options.extend([('Bring selected CPUs online', ('prepare', 'online', '1')),
                        ('Take selected CPUs offline', ('prepare', 'online', '0')),
                        ('Undo last hardware change', ('undo',)), ('Cancel', ('cancel',))])
        options.extend(scope_options)
        self.modal = dict(stage='hardware_menu', scope=scope, options=options, selected=0,
                          note='' if governors else 'No common frequency governors exposed for this scope.')

    def hardware_choose(self):
        modal = self.modal
        action = modal['options'][modal['selected']][1]
        if action[0] == 'scope':
            self.hardware_menu(action[1])
        elif action[0] == 'prepare':
            self.job('hardware_prepare', self.hardware.prepare, modal['scope'], *action[1:])
        elif action[0] == 'undo':
            self.job('hardware_undo', self.hardware.undo)
        else:
            self.modal = None

    def hardware_lines(self):
        m = self.modal
        if m['stage'] == 'hardware_menu':
            # Page choices so all CPUs remain reachable even on large servers.
            count = max(1, self.screen_size[0] - 12)
            start = m['selected'] // count * count
            m['offset'] = start
            title = 'All CPUs / all sockets' if m['scope'][0] == 'all' else f"{m['scope'][0].upper()} {m['scope'][1]}"
            return [f'CPU settings: {title}', m['note'] or 'Select a governor or online/offline action.',
                    'Up/Down + Enter, or click a choice; Esc cancels.'] + [
                    ('> ' if i == m['selected'] else '- ') + label[:max(16, min(74, self.screen_size[1] - 10))]
                    for i, (label, _) in enumerate(m['options']) if start <= i < start + count]
        plan = m['plan']
        return ['Confirm machine-wide CPU setting', f"Scope: {plan['scope']}",
                f"{plan['setting']}: {', '.join(sorted({old for _, old, _ in plan['changes']}))} -> {plan['value']}",
                'Affected logical CPUs: ' + ','.join(map(str, plan['affected'])),
                'Governor policies may include CPUs outside the selected scope.',
                'Offline removes logical CPUs from scheduling; it is not physical removal.',
                'Changes remain after exit. Batches can partially succeed.',
                'u on CPU page restores this session\'s hardware changes.',
                '[ Apply ] Enter: apply and verify', '[ Cancel ] Esc: cancel']

    def hardware_key(self, key):
        if key == 27:
            self.modal = None
        elif self.modal['stage'] == 'hardware_menu':
            if key in (curses.KEY_UP, curses.KEY_DOWN):
                self.modal['selected'] = (self.modal['selected'] + (1 if key == curses.KEY_DOWN else -1)) % len(self.modal['options'])
            elif key in (10, 13):
                self.hardware_choose()
        elif key in (10, 13) and self.hardware_preview_fits():
            self.job('hardware_apply', self.hardware.apply, self.modal['plan'])
        return True

    def hardware_preview_fits(self):
        import textwrap
        width = max(20, min(76, self.screen_size[1] - 8))
        return sum(max(1, len(textwrap.wrap(line, width))) for line in self.hardware_lines()) <= self.screen_size[0] - 5
