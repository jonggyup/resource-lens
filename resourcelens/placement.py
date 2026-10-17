"""Service CPU placement through systemd AllowedCPUs and cgroup v2 cpusets."""
import re
import time
from .controls import ControlError, SystemdBackend, ControlSession
from .hardware import Hardware, cpulist, read


def format_cpus(values):
    runs = []
    for value in sorted(set(values)):
        if runs and value == runs[-1][-1] + 1:
            runs[-1].append(value)
        else:
            runs.append([value])
    return ','.join(str(run[0]) if len(run) == 1 else f'{run[0]}-{run[-1]}' for run in runs)


def parse_cpus(text):
    if not text:
        return []
    if not re.fullmatch(r'\d+(?:-\d+)?(?:[ ,]+\d+(?:-\d+)?)*', text):
        raise ControlError('Unrecognized CPU list from systemd/kernel.')
    for item in text.replace(',', ' ').split():
        bounds = list(map(int, item.split('-')))
        if bounds[-1] < bounds[0] or bounds[-1] > 65535:
            raise ControlError('Invalid CPU range.')
    return cpulist(text)


class PlacementBackend(SystemdBackend):
    def __init__(self, *args, hardware=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hardware = hardware or Hardware()

    def snapshot(self, target):
        base = super().snapshot(target)
        if 'cpuset' not in read(self.root / 'cgroup.controllers').split():
            raise ControlError('cgroup v2 cpuset controller is unavailable.')
        out = self.command(target, 'show', '--property=AllowedCPUs', '--value', '--', target['unit']).strip()
        allowed = parse_cpus(out)
        directory = self.root / target['path'].lstrip('/')
        def effective(path):
            while True:
                file = path / 'cpuset.cpus.effective'
                if file.exists():
                    value = parse_cpus(file.read_text().strip())
                    if not value:
                        raise ControlError('Effective CPU set is empty; refresh after topology settles.')
                    return value
                if path == self.root:
                    raise ControlError('Cannot read ancestor effective CPUs.')
                path = path.parent
        file = directory / 'cpuset.cpus'
        kernel = parse_cpus(file.read_text().strip()) if file.exists() else None
        online = sorted(c['id'] for c in self.hardware.snapshot()['cpus'] if c['online'])
        parent = effective(directory.parent)
        return dict(path=base['path'], inode=base['inode'], invocation=base['invocation'],
                    allowed=allowed, kernel=kernel, effective=effective(directory),
                    eligible=sorted(set(parent) & set(online)), online=online)

    def set(self, target, cpus):
        self.command(target, '--runtime', 'set-property', '--', target['unit'], 'AllowedCPUs=' + format_cpus(cpus))


class PlacementSession:
    def __init__(self, backend=None):
        self.backend = backend or PlacementBackend()
        self.events = []

    def prepare(self, target, cpus):
        before = self.backend.snapshot(target)
        if not before['eligible']:
            raise ControlError('No online CPUs are available under the parent restrictions.')
        value = sorted(set(cpus)) if cpus is not None else []
        if cpus is not None and (not value or any(type(c) is not int for c in value)):
            raise ControlError('Select at least one logical CPU, or use Inherit.')
        if not set(value) <= set(before['eligible']):
            raise ControlError('Selection includes offline CPUs or CPUs excluded by an ancestor.')
        if (before['kernel'] or []) != before['allowed']:
            raise ControlError('systemd and kernel CPU placement disagree; refusing to overwrite it.')
        if value == before['allowed']:
            raise ControlError('Placement is already set; no change needed.')
        return dict(target=dict(target), before=before, value=value,
                    effective=value or before['eligible'])

    def verify(self, target, before, value):
        deadline = time.monotonic() + 1
        while True:
            info = self.backend.snapshot(target)
            if ControlSession.identity(info) != ControlSession.identity(before):
                raise ControlError('Unit restarted or was replaced during placement.')
            expected = value if value else info['eligible']
            if info['allowed'] == value and (info['kernel'] or []) == value and info['effective'] == expected and expected:
                return info
            if time.monotonic() >= deadline:
                raise ControlError('Placement is not confirmed in both systemd and kernel. Some systemd versions fail to clear AllowedCPUs; inspect the effective cpuset.')
            time.sleep(.05)

    def apply(self, plan):
        current = self.backend.snapshot(plan['target'])
        if current != plan['before']:
            raise ControlError('Unit, topology or CPU restrictions changed after preview. Reopen placement.')
        event = dict(target=plan['target'], before=plan['before'], requested=plan['value'],
                     status='unconfirmed', timestamp=time.time())
        self.events.append(event)
        self.backend.set(plan['target'], plan['value'])
        event['after'] = self.verify(plan['target'], plan['before'], plan['value'])
        event['status'] = 'confirmed'
        return event

    def undo(self):
        event = next((e for e in reversed(self.events) if e['status'] != 'undone'), None)
        if not event:
            raise ControlError('No placement changes to undo.')
        before, target = event['before'], event['target']
        current = self.backend.snapshot(target)
        if ControlSession.identity(current) != ControlSession.identity(before):
            raise ControlError('Unit restarted or was replaced; Undo refused.')
        if current['allowed'] == before['allowed'] and (current['kernel'] or []) == (before['kernel'] or []):
            event['status'] = 'undone'
            return event
        if current['allowed'] != event['requested'] or (current['kernel'] or []) not in (event['requested'], before['kernel'] or []):
            raise ControlError('Placement changed externally; Undo refused.')
        if before['eligible'] != current['eligible']:
            raise ControlError('Online CPUs or ancestor restrictions changed; Undo refused.')
        self.backend.set(target, before['allowed'])
        event['after'] = self.verify(target, before, before['allowed'])
        event['status'] = 'undone'
        return event
