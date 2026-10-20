"""Read-only hardware energy domains. Never attribute shared-domain joules to a PID."""
import os
from pathlib import Path
import time


def text(path):
    try:
        with open(path) as stream:
            return stream.read(4096).strip()
    except OSError:
        return None


def number(path):
    value = text(path)
    try:
        result = int(value)
        return result if 0 <= result < 2**64 else None
    except (TypeError, ValueError):
        return None


class EnergyMeter:
    def __init__(self, root='/sys', clock=time.monotonic):
        self.root = Path(root).resolve()
        self.clock = clock
        self.previous = {}
        self.paths = []
        self.warnings = []
        seen = set()
        # Traverse real powercap directories once; class entries are aliases.
        pending = [self.root/'devices/virtual/powercap', self.root/'class/powercap']
        visited = set()
        while pending and len(visited) < 256:
            directory = pending.pop().resolve()
            if directory in visited or not directory.is_relative_to(self.root):
                continue
            visited.add(directory)
            try:
                with os.scandir(directory) as entries:
                    for index, entry in enumerate(entries):
                        if index >= 512:
                            self.warnings.append('Powercap directory scan truncated')
                            break
                        path = Path(entry.path)
                        if entry.name == 'energy_uj':
                            self._add(path, 'powercap', seen)
                        elif entry.is_dir() and entry.name not in ('power','device','subsystem'):
                            if len(pending) < 512:
                                pending.append(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                self.warnings.append('Powercap discovery: '+str(exc))
        if pending:
            self.warnings.append('Powercap discovery reached directory limit')
        try:
            with os.scandir(self.root/'class/hwmon') as chips:
                for index, chip in enumerate(chips):
                    if index >= 128:
                        self.warnings.append('Hwmon discovery truncated')
                        break
                    directory = Path(chip.path)
                    with os.scandir(directory) as entries:
                        for count, entry in enumerate(entries):
                            if count >= 512:
                                self.warnings.append('Hwmon sensor scan truncated')
                                break
                            if entry.name.startswith('energy') and entry.name.endswith('_input'):
                                self._add(Path(entry.path), 'hwmon', seen)
        except FileNotFoundError:
            pass
        except OSError as exc:
            self.warnings.append('Hwmon discovery: '+str(exc))

    def _add(self, path, source, seen):
        path = path.resolve()
        if path in seen or not path.is_relative_to(self.root):
            return
        seen.add(path)
        if len(self.paths) >= 256:
            self.warnings.append('Energy domain limit reached')
            return
        directory = path.parent
        label = text(directory/'name') or directory.name
        if source == 'hwmon':
            label += ': '+(text(directory/(path.stem.replace('_input','')+'_label')) or path.stem)
        self.paths.append(dict(path=path, id=str(path.relative_to(self.root)), label=label, source=source,
                               parent=str(directory.parent.relative_to(self.root))))

    def sample(self):
        domains = []
        for sensor in self.paths:
            path = sensor['path']
            stamp = self.clock()
            value = number(path)
            try:
                stat = path.stat()
                identity = (stat.st_dev,stat.st_ino)
            except OSError:
                identity = None
            domain = {k:v for k,v in sensor.items() if k!='path'}
            domain.update(counter_uj=value, monotonic=stamp, joules=None, watts=None, interval_seconds=None,
                          max_energy_range_uj=number(path.parent/'max_energy_range_uj'), status='unavailable')
            previous = self.previous.get(sensor['id'])
            if value is not None and identity is not None:
                domain['status'] = 'warming_up'
                if previous:
                    old,when,generation = previous
                    elapsed = stamp-when
                    if identity != generation or value < old:
                        domain.update(status='gap',reason='Counter reset, rollover or sensor replacement; interval omitted')
                    elif not 0 < elapsed <= 2:
                        domain.update(status='gap',reason='Sampling gap exceeds two seconds or clock did not advance')
                    elif domain['max_energy_range_uj'] is not None and value > domain['max_energy_range_uj']:
                        domain.update(status='gap',reason='Counter outside advertised range')
                    else:
                        joules = (value-old)/1e6
                        domain.update(status='measured',joules=joules,watts=joules/elapsed,interval_seconds=elapsed)
                self.previous[sensor['id']] = (value,stamp,identity)
            else:
                domain['reason'] = 'Energy counter is missing, unreadable or invalid'
                self.previous.pop(sensor['id'],None)
            domains.append(domain)
        return dict(status='available' if any(d['counter_uj'] is not None for d in domains) else 'unavailable',
                    domains=domains, warnings=list(dict.fromkeys(self.warnings)),
                    scope='Hardware domains across all workloads; not energy attributed to the selected process or cgroup')


def summarize(samples, work_units=None, work_unit='unit'):
    totals = {}
    for index,sample in enumerate(samples):
        for domain in sample['domains']:
            row = totals.setdefault(domain['id'],dict(id=domain['id'],label=domain['label'],source=domain['source'],
                         parent=domain['parent'],observed_joules=0.,observed_seconds=0.,intervals=0,gaps=0))
            if domain['status']=='measured':
                row['observed_joules'] += domain['joules']
                row['observed_seconds'] += domain['interval_seconds']
                row['intervals'] += 1
            elif index > 0:
                row['gaps'] += 1
    for row in totals.values():
        row['status'] = 'complete' if row['intervals'] and not row['gaps'] else ('partial' if row['intervals'] else 'unavailable')
        row['average_watts'] = row['observed_joules']/row['observed_seconds'] if row['observed_seconds'] else None
        row['joules_per_work_unit'] = row['observed_joules']/work_units if work_units and row['status']=='complete' else None
    return dict(status='complete' if totals and all(r['status']=='complete' for r in totals.values()) else
                ('partial' if any(r['intervals'] for r in totals.values()) else 'unavailable'),
                domains=list(totals.values()), work_units=work_units,work_unit=work_unit,
                scope='Hardware domains across all workloads; no process/cgroup attribution or whole-machine total',
                warnings=list(dict.fromkeys(w for s in samples for w in s['warnings'])))


def lines(result):
    output=['ENERGY: '+result['status'],result['scope']]
    if not result['domains']:
        output.append('No supported energy counters exposed. Unknown energy is not zero.')
    for domain in result['domains']:
        output.append(domain['label']+' ['+domain['source']+']: '+domain['status'])
        if domain.get('intervals'):
            output.append(f"  {domain['observed_joules']:.3f} J over {domain['observed_seconds']:.3f}s | {domain['average_watts']:.3f} W average | gaps {domain['gaps']}")
        if domain.get('joules_per_work_unit') is not None:
            output.append(f"  {domain['joules_per_work_unit']:.6f} J / {result['work_unit']}")
    if result.get('work_units') is None:
        output.append('Joules/work unavailable: provide completed work with --work-units and --work-unit.')
    return output+result.get('warnings',[])+['Domains can overlap; do not sum package, subdomain or duplicate-source readings.']
