"""Verified runtime systemd block bandwidth limits with identity-aware Undo."""
import os
from pathlib import Path
import re
import stat
import time
from .controls import ControlError, SystemdBackend
from .collect import read
from .workloads import WorkloadCollector

PROPERTIES = {'read': 'IOReadBandwidthMax', 'write': 'IOWriteBandwidthMax'}


def bandwidth(value):
    text = str(value).strip().lower()
    if text in ('max', 'infinity', 'default'):
        return None
    match = re.fullmatch(r'([1-9][0-9]*)([kmg]?)', text)
    if not match:
        raise ControlError('Use bytes/sec, K/M/G (base 1024), or max')
    result = int(match[1]) * {'': 1, 'k': 1024, 'm': 1024**2, 'g': 1024**3}[match[2]]
    if result > 10**15:
        raise ControlError('Bandwidth exceeds supported limit')
    return result


class DiskBackend(SystemdBackend):
    def device(self, name):
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_:+.-]+', name) or name in ('.','..'):
            raise ControlError('Choose a block-device name, not a path')
        path = Path('/dev') / name
        try:
            info = path.stat()
        except OSError as exc:
            raise ControlError('Block device is no longer available') from exc
        if not stat.S_ISBLK(info.st_mode):
            raise ControlError('Not a block device')
        device = f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'
        sys = Path('/sys/dev/block') / device
        return dict(name=name, path=str(path.resolve()), device=device, inode=sys.stat().st_ino,
                    backing=sorted(p.name for p in (sys/'slaves').glob('*')),
                    holders=sorted(p.name for p in (sys/'holders').glob('*')))

    def settings(self, target, direction):
        output = self.command(target, 'show', '--value', '--property='+PROPERTIES[direction], '--', target['unit'])
        fields = output.split()
        if len(fields) % 2:
            raise ControlError('Cannot parse existing systemd I/O settings')
        values = {}
        for path, raw in zip(fields[::2], fields[1::2]):
            if not path.startswith('/dev/') or not raw.isdigit():
                raise ControlError('Unsupported existing device mapping; refusing replacement')
            info = os.stat(path)
            if not stat.S_ISBLK(info.st_mode):
                raise ControlError('Existing limit does not resolve to a block device')
            if int(raw) != 2**64-1:
                values[str(Path(path).resolve())] = int(raw)
        return values

    def snapshot(self, target):
        directory = WorkloadCollector(self.root).directory(target['path'])
        if directory is None or directory.stat().st_ino != target['inode']:
            raise ControlError('Cgroup disappeared or was replaced')
        if 'io' not in read(self.root/'cgroup.controllers').split():
            raise ControlError('cgroup v2 I/O controller is unavailable')
        output = self.command(target, 'show', '--property=Id,ControlGroup,InvocationID,ActiveState', '--', target['unit'])
        info = dict(line.split('=',1) for line in output.splitlines() if '=' in line)
        if info.get('Id') != target['unit'] or info.get('ControlGroup') != target['path'] or info.get('ActiveState') != 'active' or not info.get('InvocationID'):
            raise ControlError('Select an active matching systemd unit')
        kernel = {}
        for line in read(directory/'io.max').splitlines():
            fields = line.split()
            for field in fields[1:]:
                key, value = field.split('=',1)
                if value != 'max':
                    kernel.setdefault(fields[0], {})[key] = int(value)
        return dict(path=target['path'], inode=target['inode'], invocation=info['InvocationID'],
                    read=self.settings(target,'read'), write=self.settings(target,'write'), kernel=kernel)

    def set(self, target, direction, values, removed):
        pairs = [f'{path} {value}' for path,value in sorted(values.items())]
        pairs += [f'{path} infinity' for path in removed if path not in values]
        if not pairs:
            return
        self.command(target, '--runtime', 'set-property', '--', target['unit'], PROPERTIES[direction]+'='+' '.join(pairs))

    def matches(self, state):
        for direction, key in (('read','rbps'),('write','wbps')):
            expected = {}
            for path,value in state[direction].items():
                info = os.stat(path)
                expected[f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'] = value
            actual = {dev: values[key] for dev,values in state['kernel'].items() if key in values}
            if actual != expected:
                return False
        return True


class DiskSession:
    def __init__(self, backend=None):
        self.backend = backend or DiskBackend()
        self.events = []

    def prepare(self, target, device, direction, value):
        if direction not in PROPERTIES:
            raise ControlError('Direction must be read or write')
        if not target.get('controllable'):
            raise ControlError('Select a controllable service/scope')
        target = {k: target.get(k) for k in ('unit','path','inode','manager','controllable','owner_uid')}
        dev = self.backend.device(device)
        before = self.backend.snapshot(target)
        if not self.backend.matches(before):
            raise ControlError('systemd/kernel I/O settings disagree; inspect existing configuration')
        value = bandwidth(value)
        if before[direction].get(dev['path']) == value:
            raise ControlError('No change requested')
        return dict(target=target, device=dev, direction=direction, value=value, before=before)

    def verify(self, target, expected):
        deadline = time.monotonic()+1
        while True:
            current = self.backend.snapshot(target)
            if all(current[k] == expected[k] for k in ('path','inode','invocation','read','write')) and self.backend.matches(current):
                return current
            if time.monotonic() >= deadline:
                raise ControlError('I/O setting not verified in both systemd and kernel; inspect before retrying')
            time.sleep(.05)

    def apply(self, plan):
        target, before, dev = plan['target'], plan['before'], plan['device']
        if self.backend.device(dev['name']) != dev or self.backend.snapshot(target) != before:
            raise ControlError('Device, unit or settings changed after preview')
        direction = plan['direction']
        values = dict(before[direction])
        if plan['value'] is None:
            values.pop(dev['path'], None)
        else:
            values[dev['path']] = plan['value']
        event = dict(timestamp=time.time(), target=target, plan=plan, before=before, status='unconfirmed')
        self.events.append(event)
        self.backend.set(target, direction, values, [dev['path']])
        expected = dict(before, **{direction:values})
        event['after'] = self.verify(target, expected)
        event['status'] = 'confirmed'
        return event

    def undo(self):
        event = next((e for e in reversed(self.events) if e['status'] != 'undone'), None)
        if not event:
            raise ControlError('No disk changes to undo')
        plan, before = event['plan'], event['before']
        if self.backend.device(plan['device']['name']) != plan['device']:
            raise ControlError('Device changed; refusing Undo')
        current = self.backend.snapshot(event['target'])
        if current == before:
            event['status'] = 'undone'
            return event
        expected = event.get('after')
        if expected is None:
            values = dict(before[plan['direction']])
            if plan['value'] is None:
                values.pop(plan['device']['path'], None)
            else:
                values[plan['device']['path']] = plan['value']
            wanted = dict(before, **{plan['direction']:values})
            if not all(current[k] == wanted[k] for k in ('path','inode','invocation','read','write')) or not self.backend.matches(current):
                raise ControlError('Unconfirmed or externally changed settings; inspect before Undo')
        elif current != expected:
            raise ControlError('Externally changed settings; inspect before Undo')
        self.backend.set(event['target'], plan['direction'], before[plan['direction']], [plan['device']['path']])
        event['restored'] = self.verify(event['target'], before)
        event['status'] = 'undone'
        return event
