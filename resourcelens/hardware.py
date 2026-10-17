"""CPU topology and explicit, verified sysfs changes. No privilege escalation."""
from pathlib import Path
import re
from .controls import ControlError


def read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ''


def cpulist(value):
    result = set()
    for part in value.replace(',', ' ').split():
        if re.fullmatch(r'\d+(-\d+)?', part):
            ends = list(map(int, part.split('-')))
            if ends[-1] - ends[0] > 65536:
                continue
            result.update(range(ends[0], ends[-1] + 1))
    return sorted(result)


class Hardware:
    def __init__(self, root='/sys/devices/system/cpu'):
        self.root = Path(root)
        self.events = []

    def snapshot(self):
        online = cpulist(read(self.root / 'online'))
        policies = {}
        for path in sorted((self.root / 'cpufreq').glob('policy*')):
            if not re.fullmatch(r'policy\d+', path.name):
                continue
            policies[path.name] = dict(cpus=cpulist(read(path / 'related_cpus')),
                governor=read(path / 'scaling_governor'),
                available=read(path / 'scaling_available_governors').split(),
                driver=read(path / 'scaling_driver'))
        cpus = []
        for cpu in cpulist(read(self.root / 'present')):
            path = self.root / f'cpu{cpu}'
            def topology(name):
                value = read(path / 'topology' / name)
                return int(value) if value.isdigit() else None
            cpus.append(dict(id=cpu, online=cpu in online, socket=topology('physical_package_id'),
                core=topology('core_id'), hotplug=(path / 'online').exists(),
                policy=next((name for name, p in policies.items() if cpu in p['cpus']), None)))
        return dict(cpus=cpus, policies=policies)

    def prepare(self, scope, setting, value):
        before = self.snapshot()
        cpus = [c for c in before['cpus'] if scope[0] == 'all' or c[scope[0]] == scope[1]]
        if not cpus:
            raise ControlError('No present CPUs in this scope.')
        changes = []
        affected = {c['id'] for c in cpus}
        if setting == 'governor':
            if any(not c['policy'] for c in cpus):
                raise ControlError('Frequency governors are unavailable for some selected CPUs.')
            for name in sorted({c['policy'] for c in cpus}):
                policy = before['policies'][name]
                if value not in policy['available'] or not policy['governor']:
                    raise ControlError('Governor unavailable; refresh the menu.')
                affected.update(policy['cpus'])
                if policy['governor'] != value:
                    changes.append((f'cpufreq/{name}/scaling_governor', policy['governor'], value))
        elif setting == 'online' and value in ('0', '1'):
            remaining = {c['id'] for c in before['cpus'] if c['online']} - affected
            if value == '0' and not remaining:
                raise ControlError('Cannot offline all online CPUs.')
            for cpu in cpus:
                old = str(int(cpu['online']))
                if old == value:
                    continue
                if not cpu['hotplug']:
                    raise ControlError(f"CPU {cpu['id']} does not expose hotplug control.")
                changes.append((f"cpu{cpu['id']}/online", old, value))
        else:
            raise ControlError('Unsupported CPU setting.')
        if not changes:
            raise ControlError('Already set; no changes needed.')
        return dict(scope=scope, setting=setting, value=value, before=before,
                    affected=sorted(affected), changes=changes)

    def write(self, relative, value):
        (self.root / relative).write_text(value + '\n')

    def apply(self, plan):
        # Rebuild rather than trust paths supplied in a stale or modified plan.
        fresh = self.prepare(plan['scope'], plan['setting'], plan['value'])
        if fresh != plan:
            raise ControlError('CPU topology or settings changed since preview. Preview again.')
        event = dict(plan=plan, applied=[], status='unconfirmed')
        self.events.append(event)
        try:
            for path, old, value in plan['changes']:
                if read(self.root / path) != old:
                    raise ControlError('Setting changed externally; remaining writes stopped.')
                if plan['setting'] == 'online' and value == '0':
                    if len(cpulist(read(self.root / 'online'))) <= 1:
                        raise ControlError('Cannot offline the last online CPU.')
                # Record before writing: a failing write can still have taken effect.
                event['applied'].append((path, old, value))
                self.write(path, value)
                if read(self.root / path) != value:
                    raise ControlError('Kernel did not confirm requested value.')
            event['status'] = 'confirmed'
        except (OSError, ControlError) as exc:
            raise ControlError(f'{exc}. Batch may be partial; use CPU Undo to restore applied entries.') from exc
        return event

    def undo(self):
        event = next((e for e in reversed(self.events) if e['status'] != 'undone'), None)
        if not event:
            raise ControlError('No hardware changes to undo.')
        plan = event['plan']
        current = self.snapshot()
        # Identity/membership must remain the same, except for online state/governor values.
        def identity(s):
            return ([(c['id'], c['socket'], c['core'], c['policy'], c['hotplug']) for c in s['cpus']],
                    {k: (v['cpus'], v['driver']) for k, v in s['policies'].items()})
        if identity(current) != identity(plan['before']):
            raise ControlError('CPU topology changed; cannot safely undo.')
        for path, old, value in reversed(event['applied']):
            actual = read(self.root / path)
            if actual == old:
                continue
            if actual != value:
                raise ControlError('Setting changed externally; Undo stopped.')
            if path.endswith('/online') and old == '0' and len(cpulist(read(self.root / 'online'))) <= 1:
                raise ControlError('Undo would offline the last CPU.')
            self.write(path, old)
            if read(self.root / path) != old:
                raise ControlError('Undo was not confirmed by the kernel.')
        event['status'] = 'undone'
        return event
