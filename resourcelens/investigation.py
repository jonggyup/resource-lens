"""Observed pressure, session markers and descriptive before/after comparisons."""
from collections import deque
import math
import time


def indicators(snapshot, group=None, resources=('cpu', 'memory', 'io')):
    pressure = group.get('pressure', {}) if group else snapshot.get('pressure', {})
    result = []
    for kind in resources:
        value = pressure.get(kind, {})
        value = value.get('avg10') if group else value.get('some', {}).get('avg10')
        label = {'cpu': 'CPU', 'memory': 'Memory', 'io': 'I/O'}[kind]
        result.append(f'{label} waiting (10s): ' + ('N/A' if value is None else f'{value:.1f}%'))
    if group and 'cpu' in resources:
        throttled = group.get('throttled_usec_per_s')
        result.append('CPU quota throttling: ' + ('N/A' if throttled is None else f'{throttled / 1000:.1f} ms/s'))
    elif not group and 'cpu' in resources:
        thermal = snapshot.get('thermal_throttling', [])
        known = [v for entry in thermal for v in (entry.get('core_events'), entry.get('package_events')) if v is not None]
        result.append('Thermal throttling: ' + ('N/A (no interval counters)' if not known else
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
        self.window_seconds=30

    def observe(self, sample):
        self.samples.append({key: value for key, value in sample.items()
                             if key in ('timestamp', 'elapsed_s', 'collection_scope', 'cpu', 'memory', 'pressure', 'workloads', 'disks', 'network', 'energy')})

    def mark(self, label, timestamp=None, target=None, status='marker'):
        event = dict(timestamp=time.time() if timestamp is None else timestamp,
                     label=label, target=target, status=status)
        self.events.append(event)
        self.events = self.events[-1000:]
        self.selected = len(self.events) - 1
        return event

    def compare(self, event, seconds=30):
        """Legacy sample-mean API; UI/export use comparison() with time coverage."""
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

    def comparison(self, event, seconds=None):
        from .comparison import compare_windows
        return compare_windows(self.samples, event, metrics, self.window_seconds if seconds is None else seconds, self.events)

    def observation_lines(self, event):
        from .comparison import comparison_lines
        return comparison_lines(self.comparison(event))

    def lines(self, width=80):
        lines = ['CHANGE TIMELINE', 'M: mark | Up/Down: event | PgUp/PgDn: scroll | [ / ]: window',
                 f'Requested window: {self.window_seconds}s on each side', '',
                 '-- How to read the comparison --',
                 'Baseline ends at apply start; post-change starts after verified completion.',
                 'Transition time is excluded; gaps and unknown intervals reduce coverage.',
                 'Missing samples are excluded. Request latency is not measured here.', '']
        if not self.events:
            return lines + ['No events yet. Press M to mark a baseline; control attempts are marked automatically.']
        self.selected = max(0, min(self.selected, len(self.events) - 1))
        event = self.events[self.selected]
        lines += [f"Selected: {event['label']} [{event['status']}]", *self.observation_lines(event)]
        lines += ['', '-- Events: time / result / action --']
        for index, entry in enumerate(self.events):
            lines.append(('> ' if index == self.selected else '  ') + time.strftime('%H:%M:%S', time.localtime(entry['timestamp'])) +
                         f" [{entry['status']}] {entry['label']}")
        return lines
