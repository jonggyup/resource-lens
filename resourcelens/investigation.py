"""Observed pressure, session markers and descriptive before/after comparisons."""
from collections import deque
import math
import time


def indicators(snapshot, group=None):
    pressure = group.get('pressure', {}) if group else snapshot.get('pressure', {})
    result = []
    for kind in ('cpu', 'memory', 'io'):
        value = pressure.get(kind, {})
        value = value.get('avg10') if group else value.get('some', {}).get('avg10')
        result.append(f'{kind.upper()} waiting: ' + ('unknown' if value is None else f'{value:.1f}% PSI (10s)'))
    if group:
        throttled = group.get('throttled_usec_per_s')
        result.append('CPU quota throttling: ' + ('unknown' if throttled is None else f'{throttled / 1000:.1f} ms/s'))
    else:
        thermal = snapshot.get('thermal_throttling', [])
        known = [v for entry in thermal for v in (entry.get('core_events'), entry.get('package_events')) if v is not None]
        result.append('Thermal throttling: ' + ('unknown (no interval counters)' if not known else
                      'events observed this interval' if any(v > 0 for v in known) else 'no events in exposed counters'))
    return result


def metrics(sample, target=None):
    if target:
        group = next((g for g in sample.get('workloads', [])
                      if g['id'] == target['id'] and g.get('inode') == target.get('inode')), None)
        if group is None:
            return {}
        values = {'Service CPU %': group.get('cpu_pct'), 'Service RAM bytes': group.get('memory_current'),
                  'Quota throttle ms/s': (group['throttled_usec_per_s'] / 1000 if group.get('throttled_usec_per_s') is not None else None)}
        for direction in ('read', 'write'):
            rates = [d.get(direction + '_bps') for d in group.get('io_devices', [])]
            values[f'Service {direction} B/s'] = sum(rates) if rates and all(v is not None for v in rates) else None
        for kind, value in group.get('pressure', {}).items():
            values[kind.upper() + ' PSI %'] = value.get('avg10')
        for domain in sample.get('energy', {}).get('domains', []):
            values['Energy (hardware) '+domain['id']+' W'] = domain.get('watts')
        return values
    cpu = next((c for c in sample['cpu'] if c['id'] == 'cpu'), {})
    memory = sample['memory']
    values = {'Host CPU %': cpu.get('busy_pct'), 'RAM used bytes': memory.get('used')}
    for kind, value in sample.get('pressure', {}).items():
        values[kind.upper() + ' PSI %'] = value.get('some', {}).get('avg10')
    # Keep devices and energy domains separate; their counters may overlap.
    for field, directions in (('disks', ('read', 'write')), ('network', ('rx', 'tx'))):
        for item in sample.get(field, []):
            identity = item.get('identity') or (str(item.get('name'))+':'+str(item.get('ifindex')))
            for direction in directions:
                values[f"{field}:{identity} {direction} B/s"] = item.get(direction + '_bps')
    for domain in sample.get('energy', {}).get('domains', []):
        values['Energy (hardware) '+domain['id']+' W'] = domain.get('watts')
    return values


class Investigation:
    def __init__(self):
        self.samples = deque(maxlen=600)
        self.events = []
        self.selected = 0

    def observe(self, sample):
        self.samples.append({key: value for key, value in sample.items()
                             if key in ('timestamp', 'cpu', 'memory', 'pressure', 'workloads', 'disks', 'network', 'energy')})

    def mark(self, label, timestamp=None, target=None, status='marker'):
        event = dict(timestamp=time.time() if timestamp is None else timestamp,
                     label=label, target=target, status=status)
        self.events.append(event)
        self.events = self.events[-1000:]
        self.selected = len(self.events) - 1
        return event

    def compare(self, event, seconds=30):
        stamp, target = event['timestamp'], event.get('target')
        windows = ([s for s in self.samples if stamp - seconds <= s['timestamp'] < stamp],
                   [s for s in self.samples if stamp < s['timestamp'] <= stamp + seconds])
        result = []
        for samples in windows:
            values = {}
            for sample in samples:
                for key, value in metrics(sample, target).items():
                    if value is not None and isinstance(value, (int, float)) and math.isfinite(value):
                        values.setdefault(key, []).append(value)
            result.append({key: (sum(items) / len(items), len(items)) for key, items in values.items()})
        return result, [len(window) for window in windows]

    def lines(self, width=80):
        lines = ['CHANGE TIMELINE', 'M: mark baseline | Up/Down: event | PgUp/PgDn: scroll', '',
                 '-- How to read the comparison --',
                 'Values are averages from the 30 seconds before and after the event.',
                 'These are sample averages, not time-weighted values or proof of cause.',
                 'Missing samples are excluded. Request latency is not measured here.', '']
        if not self.events:
            return lines + ['No events yet. Press M to mark a baseline; control attempts are marked automatically.']
        self.selected = max(0, min(self.selected, len(self.events) - 1))
        event = self.events[self.selected]
        pair, counts = self.compare(event)
        lines += [f"Selected: {event['label']} [{event['status']}]", f'Available samples: before {counts[0]}, after {counts[1]}',
                  'Bars share a scale within each metric; counts show available samples.']
        bar_width = max(6, min(24, width - 32))
        def display(key, value):
            if value is None:return 'N/A'
            if key.endswith(('bytes','B/s')):
                from .ui import size
                return size(value) + ('/s' if key.endswith('B/s') else '')
            return f'{value:.2f}'
        for key in sorted(set(pair[0]) | set(pair[1])):
            before, after = pair[0].get(key), pair[1].get(key)
            scale = max((abs(v[0]) for v in (before,after) if v is not None), default=0) or 1
            lines += ['', key]
            for label,value in (('Before',before),('After ',after)):
                graph = '?' * bar_width if value is None else '#' * round(abs(value[0])/scale*bar_width)
                lines.append(f"  {label} [{graph:<{bar_width}}] {display(key, value[0] if value else None)} (n={value[1] if value else 0})")
            if before is None or after is None:
                lines.append('  Change: unavailable; one window has no usable values.')
            else:
                delta=after[0]-before[0]
                relative=f'{100*delta/abs(before[0]):+.1f}%' if before[0] else 'baseline is zero; relative change undefined'
                lines.append('  Change: '+('+' if delta>=0 else '-')+display(key,abs(delta))+'; '+relative)
        if not pair[0] and not pair[1]:lines.append('No usable measurements for this target in either window.')
        lines += ['', '-- Events: time / result / action --']
        for index, entry in enumerate(self.events):
            lines.append(('> ' if index == self.selected else '  ') + time.strftime('%H:%M:%S', time.localtime(entry['timestamp'])) +
                         f" [{entry['status']}] {entry['label']}")
        return lines
