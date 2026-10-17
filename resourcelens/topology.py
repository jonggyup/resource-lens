"""Responsive logical topology tiles; positions do not imply silicon coordinates."""
from collections import defaultdict


def topology_map(data, metrics, width, selected=None, eligible=None, focus=None):
    lines, regions = [], defaultdict(list)
    sockets = defaultdict(list)
    for cpu in data.get('cpus', []):
        sockets[cpu['socket']].append(cpu)
    tile_width = min(28, max(24, width))
    columns = max(1, (width + 1) // (tile_width + 1))
    def hit(row, x, end, kind, ids):
        regions[row].append((x, end, kind, tuple(ids)))
    for socket, cpus in sorted(sockets.items(), key=lambda item: -1 if item[0] is None else item[0]):
        online = sum(c['online'] for c in cpus)
        hit(len(lines), 0, width, 'socket', [c['id'] for c in cpus])
        lines.append(f"[ SOCKET {socket if socket is not None else '?'} ]  {online}/{len(cpus)} logical CPUs online")
        nodes = defaultdict(list)
        for cpu in cpus:
            nodes[tuple(cpu.get('nodes', []))].append(cpu)
        for node, members in sorted(nodes.items()):
            lines.append('  NUMA ' + (','.join(map(str, node)) if node else 'unknown') + ' | boxes = physical cores; rows = SMT threads')
            cores = defaultdict(list)
            for cpu in members:
                # Use exported sibling lists to avoid merging cores with reused IDs across dies.
                sibling_key = tuple(cpu.get('siblings', [])) or (cpu.get('die'), cpu['core'])
                if cpu['core'] is None and not cpu.get('siblings'):
                    sibling_key = ('unknown', cpu['id'])
                cores[str(sibling_key)].append(cpu)
            groups = sorted(cores.values(), key=lambda group: min(c['id'] for c in group))
            for start in range(0, len(groups), columns):
                batch = groups[start:start + columns]
                height = max(len(g) for g in batch) + 4
                rows = ['' for _ in range(height)]
                base = len(lines)
                for col, group in enumerate(batch):
                    x = col * (tile_width + 1)
                    cpu = group[0]
                    label = f" Core {cpu['core'] if cpu['core'] is not None else '?'}"
                    if cpu.get('die') is not None:
                        label += f" / Die {cpu['die']}"
                    box = ['+' + label.ljust(tile_width - 2, '-')[:tile_width - 2] + '+']
                    hit(base, x, x + tile_width, 'core', [c['id'] for c in group])
                    for offset, cpu in enumerate(sorted(group, key=lambda c: c['id'])):
                        cpu_id = cpu['id']
                        value = metrics.get(f'cpu{cpu_id}', {}).get('busy_pct')
                        if not cpu['online']:
                            state = 'OFFLINE'
                        elif value is None:
                            state = 'usage N/A'
                        else:
                            state = '[' + '#' * round(max(0, min(100, value)) / 20) + '.' * (5 - round(max(0, min(100, value)) / 20)) + f'] {value:3.0f}%'
                        mark = ' '
                        if selected is not None:
                            mark = 'x' if cpu_id in selected else ('-' if cpu_id in eligible else '!')
                        label = f"{'>' if focus == cpu_id else ' '}{mark} CPU{cpu_id:<3} {state}"
                        box.append('|' + label.ljust(tile_width - 2)[:tile_width - 2] + '|')
                        hit(base + offset + 1, x, x + tile_width, 'cpu', [cpu_id])
                    while len(box) < height - 3:
                        box.append('|' + ' ' * (tile_width - 2) + '|')
                    policies = {data.get('policies', {}).get(c.get('policy'), {}).get('governor') or 'N/A' for c in group}
                    label = ' gov: ' + '/'.join(sorted(policies))
                    clocks = [metrics.get(f"cpu{c['id']}", {}).get('frequency_mhz') for c in sorted(group, key=lambda c: c['id'])]
                    frequency = ' MHz: ' + '/'.join('N/A' if value is None else f'{value:.0f}' for value in clocks)
                    box += ['|' + label.ljust(tile_width - 2)[:tile_width - 2] + '|',
                            '|' + frequency.ljust(tile_width - 2)[:tile_width - 2] + '|',
                            '+' + '-' * (tile_width - 2) + '+']
                    for i, text in enumerate(box):
                        rows[i] += (' ' if col else '') + text
                lines.extend(rows)
            lines.append('')
    if not sockets:
        lines.append('CPU topology is unavailable from sysfs.')
    return lines, dict(regions)
