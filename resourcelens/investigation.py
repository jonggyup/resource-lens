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
        return values
    cpu = next((c for c in sample['cpu'] if c['id'] == 'cpu'), {})
    memory = sample['memory']
    values = {'Host CPU %': cpu.get('busy_pct'), 'RAM used bytes': memory.get('used')}
    for kind, value in sample.get('pressure', {}).items():
        values[kind.upper() + ' PSI %'] = value.get('some', {}).get('avg10')
    # Do not add layered devices/interfaces into misleading global throughput.
    return values


class Investigation:
    def __init__(self):
        self.samples = deque(maxlen=600)
        self.events = []
        self.selected = 0

    def observe(self, sample):
        self.samples.append({key: value for key, value in sample.items()
                             if key in ('timestamp', 'cpu', 'memory', 'pressure', 'workloads')})

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

    def lines(self):
        lines = ['CHANGE TIMELINE | M mark current sample | Up/Down select event',
                 'Comparison: sampled means, 30 seconds before/after; differences do not establish causality.',
                 'Only available samples are used; no application latency is inferred.', '']
        if not self.events:
            return lines + ['No events yet. Press M to mark a baseline; control attempts are marked automatically.']
        self.selected = max(0, min(self.selected, len(self.events) - 1))
        event = self.events[self.selected]
        pair, counts = self.compare(event)
        lines += [f"Selected: {event['label']} [{event['status']}]", f'Available samples: before {counts[0]}, after {counts[1]}',
                  'METRIC                         BEFORE(n)     AFTER(n)     CHANGE']
        for key in sorted(set(pair[0]) | set(pair[1])):
            before, after = pair[0].get(key), pair[1].get(key)
            value = lambda item: 'N/A' if item is None else f'{item[0]:.2f}({item[1]})'
            change = 'N/A' if before is None or after is None else f'{after[0] - before[0]:+.2f}'
            lines.append(f'{key:<28} {value(before):>12} {value(after):>12} {change:>12}')
        lines += ['', 'EVENTS (time / outcome / action)']
        for index, entry in enumerate(self.events):
            lines.append(('> ' if index == self.selected else '  ') + time.strftime('%H:%M:%S', time.localtime(entry['timestamp'])) +
                         f" [{entry['status']}] {entry['label']}")
        return lines
