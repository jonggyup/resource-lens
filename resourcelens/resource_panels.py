"""Responsive device panels shared by details and expanded overview."""
import textwrap
from .placement import format_cpus
from .investigation import indicators


def panels(cards, width):
    """Wrap every field and align card rows without dropping devices or text."""
    from .ui import clean
    columns = max(1, width // 44)
    card_width = max(12, (width - columns + 1) // columns)
    lines = []
    for start in range(0, len(cards), columns):
        batch = []
        for title, content in cards[start:start + columns]:
            body = []
            for line in [title, *content]:
                body.extend(textwrap.wrap(clean(line), card_width - 4, replace_whitespace=False) or [''])
            batch.append(body)
        height = max(map(len, batch))
        for row in range(height + 2):
            segments = []
            for body in batch:
                if row in (0, height + 1):
                    segments.append('+' + '-' * (card_width - 2) + '+')
                else:
                    value = body[row - 1] if row <= len(body) else ''
                    segments.append('| ' + value.ljust(card_width - 4) + ' |')
            lines.append(' '.join(segments))
        lines.append('')
    return lines


def resource_lines(view, kind, width):
    from .ui import size, pct, ratio, bar, pressure
    s = view.snapshot
    focus = view.focus[1] if view.focus and view.focus[0] == kind else None
    if focus is not None and kind in ('disk', 'gpu', 'network'):
        field = {'disk': 'disks', 'gpu': 'gpus', 'network': 'network'}[kind]
        s = {**s, field: [item for item in s[field] if (item['id'] if kind == 'gpu' else item['name']) == focus]}
    topology = s.get('resource_topology', {})
    cards = []
    plot_width = max(8, min(30, width - 17))
    def history(resource, item, field, label, maximum=None, unit=''):
        values = list(view.device_history(resource, item, field))[-plot_width:]
        peak = max((value for value in values if value is not None), default=None)
        scale = maximum or peak or 1
        caption = f'0-{maximum:g}' if maximum else ('N/A' if peak is None else f'{peak:.1f}')
        return label + ' ' + view.trend(values, plot_width, scale) + ' | scale ' + caption + unit
    def number(value, places=1):
        return 'N/A' if value is None else f'{value:.{places}f}'
    def gauge(value):
        return bar(value, min(18, plot_width))
    def attach(info):
        return f"PCI {info.get('pci') or 'unknown'} | NUMA {info.get('numa') if info.get('numa') is not None else 'unknown'}"
    if kind == 'memory':
        m = s['memory']
        cards = [('SYSTEM RAM', [gauge(ratio(m['used'], m['total'])),
            f"Used {size(m['used'])} / {size(m['total'])}", f"Available {size(m['available'])}",
            'Used = total - available; includes availability estimate.',
            'History ' + view.trend(view.history['memory'], plot_width), pressure(s, 'memory')]),
            ('CACHE / SWAP', [f"Cache {size(m['cache'])} | Buffers {size(m['buffers'])}",
                'Cache and available overlap; do not add these counters.',
                'Swap ' + gauge(ratio(m['swap_used'], m['swap_total'])),
                f"Swap {size(m['swap_used'])} / {size(m['swap_total'])}"])]
        for node in topology.get('memory_nodes', []):
            total, free = node['total'], node['free']
            occupied = max(0, total - free) if total is not None and free is not None else None
            cards.append((node['id'].upper() + ' MEMORY', [gauge(ratio(occupied, total)),
                f"Total {size(total)} | Free {size(free)}", 'Gauge = non-free, including reclaimable pages.',
                'Local CPUs: ' + (format_cpus(node['cpus']) or 'none reported')]))
        if focus is not None:
            cards = cards[:2] if focus == 'system' else [card for card in cards if card[0] == focus.upper() + ' MEMORY']
        if not topology.get('memory_nodes') and focus is None:
            cards.append(('NUMA TOPOLOGY', ['Node memory counters unavailable.', 'DIMM slots/channels are not inferred.']))
    elif kind == 'disk':
        for d in s['disks']:
            info = topology.get('disks', {}).get(d['name'], {})
            cards.append((d['name'] + ' | ' + d['model'], [
                'Active ' + gauge(d['active_pct']),
                history('disk', d, 'active_pct', 'Active history', 100, '%'),
                history('disk', d, 'read_bps', 'Read history', unit=' B/s'),
                history('disk', d, 'write_bps', 'Write history', unit=' B/s'),
                f"READ  {size(d['read_bps'])}/s | {number(d['read_iops'])} IOPS",
                f"WRITE {size(d['write_bps'])}/s | {number(d['write_iops'])} IOPS",
                f"In flight {d['in_flight']} | Avg queue {number(d['queue_depth'], 2)}",
                f"Device capacity {size(info.get('capacity'))}",
                'Backing devices: ' + (', '.join(info.get('backing', [])) or 'none reported'),
                'Used by: ' + (', '.join(info.get('holders', [])) or 'none reported'),
                'Partitions: ' + (', '.join(info.get('partitions', [])) or 'none reported'), attach(info)]))
        fs = s.get('root_filesystem')
        if fs and focus is None:
            cards.append(('/ FILESYSTEM', [gauge(ratio(fs['used'], fs['total'])),
                f"Used {size(fs['used'])} / {size(fs['total'])}", pressure(s, 'io'),
                'Filesystem capacity is separate from device traffic.']))
    elif kind == 'gpu':
        for g in s['gpus']:
            devices = topology.get('gpus', {})
            info = devices.get(g['id'], {})
            if not info and g['driver'] == 'nvidia':
                try:
                    domain, bus, slot = g['id'].lower().split(':')
                    pci = f'{int(domain, 16):04x}:{bus}:{slot}'
                    info = next((value for value in devices.values() if value.get('pci') == pci), {})
                except (ValueError, TypeError):
                    pass
            cards.append((g['id'] + ' | ' + g['name'], ['GPU busy ' + gauge(g['busy_pct']),
                history('gpu', g, 'busy_pct', 'Busy history', 100, '%'),
                history('gpu', g, 'memory_used', 'VRAM history', unit=' bytes'),
                history('gpu', g, 'power_w', 'Power history', unit=' W'),
                'VRAM ' + gauge(ratio(g['memory_used'], g['memory_total'])),
                f"VRAM {size(g['memory_used'])} / {size(g['memory_total'])}",
                f"Temperature {g['temperature_c'] if g['temperature_c'] is not None else 'N/A'} C",
                f"Power {g['power_w'] if g['power_w'] is not None else 'N/A'} W",
                'Clock: ' + number(g.get('clock_mhz'), 0) + ' MHz', 'Driver: ' + g['driver'], attach(info), g['status']]))
    elif kind == 'network':
        for net in s['network']:
            info = topology.get('network', {}).get(net['name'], {})
            content = [f"Link {net['state']} | {net['speed_mbps'] or 'N/A'} Mb/s",
                       f"RECEIVE {size(net['rx_bps'])}/s", f"SEND    {size(net['tx_bps'])}/s"]
            for direction in ('rx', 'tx'):
                values = list(view.history[(net['name'], net['ifindex'], direction)])[-plot_width:]
                peak = max((v for v in values if v is not None), default=None)
                content.append(direction.upper() + ' history ' + view.trend(values, plot_width, peak or 1))
                content.append('Scale peak ' + size(peak) + '/s')
            content += [f"Totals RX {size(net['rx_bytes'])} / TX {size(net['tx_bytes'])}",
                f"Packets/s RX {number(net['rx_pps'])} / TX {number(net['tx_pps'])}",
                f"Errors {net['rx_errors']}/{net['tx_errors']} | Drops {net['rx_dropped']}/{net['tx_dropped']}",
                'Master: ' + (info.get('master') or 'none reported'),
                'Lower links: ' + (', '.join(info.get('lower', [])) or 'none reported'), attach(info)]
            cards.append((net['name'] + ' | NETWORK INTERFACE', content))
    if not cards:
        cards = [(kind.upper(), ['No readable devices/counters; usage is unknown.'])]
    notes = {'memory': 'RAM availability and NUMA free memory measure different things.',
             'disk': 'Active time is not saturation. Stacked devices may count the same I/O.',
             'gpu': 'Unknown usage is not zero. Shared GPU memory is not total system RAM.',
             'network': 'Each RX/TX history scales to its visible peak, not link utilization. Do not sum layered interfaces.'}
    history_lines = ['History ' + view.trend(view.history['gpu'], plot_width)] if kind == 'gpu' and focus is None else []
    tail = [view.marker_line(plot_width), *indicators(s), *view.overlay_lines()]
    return panels(cards, width) + history_lines + [piece for line in tail + [notes[kind]] for piece in (textwrap.wrap(line, width) or [''])]


def expanded_overview(view, width):
    """A scrollable overview retaining every resource instead of truncating devices."""
    from .topology import topology_map
    s = view.snapshot
    lines, links = [], {}
    def section(title, page, content):
        links[len(lines)] = page
        lines.append('[ ' + title + ' | click for details ]')
        lines.extend(content)
        lines.append('')
    tiles, _ = topology_map(s.get('cpu_hardware', {}), {c['id']: c for c in s['cpu']}, width)
    section('CPU TOPOLOGY', 1, tiles)
    for kind, page in [('memory', 2), ('disk', 3), ('gpu', 4), ('network', 6)]:
        section(kind.upper(), page, resource_lines(view, kind, width))
    from .ui import pct, size
    groups = sorted(s.get('workloads', []), key=lambda g: g['cpu_pct'] or 0, reverse=True)
    content = panels([(g['unit'] or g['id'], [f"CPU {pct(g['cpu_pct'])} | RAM {size(g['memory_current'])}",
                     f"{len(g['pids'])} visible processes", 'p on Workloads: CPU placement']) for g in groups[:6]], width)
    section(f'WORKLOADS (top {min(6, len(groups))} of {len(groups)} by CPU)', 7, content or ['No workload accounting available.'])
    section('PROCESSES', 5, view.process_lines(width))
    return lines, links
