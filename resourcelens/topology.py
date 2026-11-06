"""Responsive logical topology tiles; positions do not imply silicon coordinates."""
from collections import defaultdict
import math
import re


def scoped_temperatures(data,temperatures):
    """Bind legacy core labels only when their physical-core match is unique."""
    result=[];cores=defaultdict(set);sockets=set()
    for cpu in data.get('cpus',[]):
        sockets.add(cpu['socket'])
        cores[cpu['core']].add((cpu['socket'],cpu.get('die'),cpu['core'],tuple(cpu.get('siblings',[]))))
    for sensor in temperatures:
        value=sensor.get('temperature_c')
        if type(value) not in (int,float) or not math.isfinite(value):continue
        name=sensor.get('name') or ''
        if not isinstance(name,str):continue
        core=sensor.get('core')
        if type(core) is not int:core=None
        match=re.fullmatch(r'Core (\d+)',name)
        if core is None and match:core=int(match[1])
        socket=sensor.get('socket')
        if type(socket) is not int:socket=None
        package=re.fullmatch(r'(?:Package|Physical) id (\d+)',name)
        if socket is None and package:socket=int(package[1])
        if core is not None:
            candidates={c for c in cores.get(core,()) if socket is None or c[0]==socket}
            if len(candidates)!=1:continue
            socket,die,core,_=next(iter(candidates))
            result.append(dict(sensor,socket=socket,die=die,core=core))
        else:
            if socket is None and len(sockets)==1:socket=next(iter(sockets))
            if socket is not None:result.append(dict(sensor,socket=socket,core=None))
    return result


def topology_map(data, metrics, width, selected=None, eligible=None, focus=None,temperatures=(),energy=None):
    lines, regions = [], defaultdict(list)
    temperatures=scoped_temperatures(data,temperatures)
    from .energy import scoped_domains,package_power
    energy_domains=scoped_domains((energy or {}).get('domains',[]))
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
        lines.append(f"[ SOCKET {socket if socket is not None else '?'} ]  {online}/{len(cpus)} threads online")
        package=[t for t in temperatures if t['socket']==socket and t['core'] is None]
        if package:
            sensor=next((t for t in package if re.fullmatch(r'(?:Package|Physical) id \d+',t['name'])),package[0])
            label='Package' if re.fullmatch(r'(?:Package|Physical) id \d+',sensor['name']) else sensor['name']
            lines[-1]+=f" | {label}: {sensor['temperature_c']:.1f} C"
        watts=package_power(energy_domains,socket)
        if watts is not None:lines[-1]+=f' | Power: {watts:.2f} W'
        nodes = defaultdict(list)
        for cpu in cpus:
            nodes[tuple(cpu.get('nodes', []))].append(cpu)
        for node, members in sorted(nodes.items()):
            lines.append('  NUMA ' + (','.join(map(str, node)) if node else 'unknown') + ' | core boxes contain CPU threads')
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
                height = max(len(g) for g in batch) + 5
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
                    while len(box) < height - 4:
                        box.append('|' + ' ' * (tile_width - 2) + '|')
                    policies = {data.get('policies', {}).get(c.get('policy'), {}).get('governor') or 'N/A' for c in group}
                    label = ' Governor: ' + '/'.join(sorted(policies))
                    clocks = [metrics.get(f"cpu{c['id']}", {}).get('frequency_mhz') for c in sorted(group, key=lambda c: c['id'])]
                    frequency = ' MHz: ' + '/'.join('N/A' if value is None else f'{value:.0f}' for value in clocks)
                    readings=[t['temperature_c'] for t in temperatures if (t['socket'],t.get('die'),t['core'])==(cpu['socket'],cpu.get('die'),cpu['core'])]
                    temperature=' Temp: '+(f'{readings[0]:.1f} C' if len(readings)==1 else 'N/A')
                    box += ['|' + label.ljust(tile_width - 2)[:tile_width - 2] + '|',
                            '|' + frequency.ljust(tile_width - 2)[:tile_width - 2] + '|',
                            '|' + temperature.ljust(tile_width - 2)[:tile_width - 2] + '|',
                            '+' + '-' * (tile_width - 2) + '+']
                    for i, text in enumerate(box):
                        rows[i] += (' ' if col else '') + text
                lines.extend(rows)
            lines.append('')
    if not sockets:
        lines.append('CPU topology is unavailable from sysfs.')
    return lines, dict(regions)
