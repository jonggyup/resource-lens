"""Individual resource focus and pinned service overlays."""
from .hardware import cpulist
from .investigation import indicators

KINDS = {1: 'cpu', 2: 'memory', 3: 'disk', 4: 'gpu', 6: 'network'}


def candidates(snapshot, kind=None):
    values = []
    for socket in sorted({c['socket'] for c in snapshot.get('cpu_hardware', {}).get('cpus', [])}, key=str):
        values.append(('cpu', str(socket), f'CPU socket {socket}'))
    values.append(('memory', 'system', 'System RAM / cache / swap'))
    values += [('memory', n['id'], 'Memory ' + n['id']) for n in snapshot.get('resource_topology', {}).get('memory_nodes', [])]
    for resource, field, label in [('disk', 'disks', 'Disk'), ('gpu', 'gpus', 'GPU'), ('network', 'network', 'Interface')]:
        values += [(resource, item.get('name') if resource != 'gpu' else item['id'],
                    label + ' ' + (item.get('name') or item['id'])) for item in snapshot.get(field, [])]
    return [item for item in values if kind is None or item[0] == kind]


class FocusView:
    def open_focus(self):
        if self.focus:
            self.focus = None
            self.scroll = 0
            return
        if not self.snapshot:
            return
        options = candidates(self.snapshot, KINDS.get(self.page))
        self.modal = dict(stage='focus', options=options, selected=0, query='')

    def focus_lines(self):
        modal = self.modal
        count = max(1, self.screen_size[0] - 10)
        start = modal['selected'] // count * count
        modal['offset'] = start
        return ['Focus a resource panel', 'Arrows select | Enter or click opens | Esc cancels',
                *[('> ' if i == modal['selected'] else '- ') + item[2][:max(8, min(74, self.screen_size[1] - 10))]
                  for i, item in enumerate(modal['options']) if start <= i < start + count],
                'f restores all panels; [ / ] switch focused devices.']

    def focus_key(self, key):
        import curses
        if key == 27:
            self.modal = None
        elif key in (10, 13) and self.modal['options']:
            self.focus = self.modal['options'][self.modal['selected']][:2]
            self.scroll, self.modal = 0, None
        elif key in (curses.KEY_UP, curses.KEY_DOWN, ord('j'), ord('k')) and self.modal['options']:
            self.modal['selected'] = (self.modal['selected'] + (1 if key in (curses.KEY_DOWN, ord('j')) else -1)) % len(self.modal['options'])
        return True

    def cycle_focus(self, step):
        if not self.focus or not self.snapshot:
            return
        items = [item[:2] for item in candidates(self.snapshot, self.focus[0])]
        if items:
            index = items.index(self.focus) if self.focus in items else 0
            self.focus = items[(index + step) % len(items)]
            self.scroll = 0

    def overlay_group(self):
        if not self.overlay or not self.snapshot:
            return None
        return next((g for g in self.snapshot.get('workloads', [])
                     if g['id'] == self.overlay['id'] and g.get('inode') == self.overlay['inode']), None)

    def overlay_cpus(self):
        group = self.overlay_group()
        if group:
            for entry in group.get('limits', {}).get('ancestors', []):
                value = entry.get('cpuset.cpus.effective')
                if value:
                    return set(cpulist(value))
        return None

    def overlay_lines(self):
        from .ui import pct, size
        if not self.overlay:
            return []
        group = self.overlay_group()
        if group is None:
            return ['Pinned service exited, was replaced, or is absent from this sample. o clears overlay.']
        rates = group.get('io_devices', [])
        total = lambda key: sum(d[key] for d in rates) if rates and all(d.get(key) is not None for d in rates) else None
        return [f"SERVICE: {group['unit'] or group['id']} | o clears overlay",
                f"CPU {pct(group['cpu_pct'])} | RAM {size(group['memory_current'])} | {len(group['pids'])} visible processes",
                f"Read {size(total('read_bps'))}/s | Write {size(total('write_bps'))}/s (cgroup device counters)",
                'Highlighted CPUs = effective cgroup set; task affinity can narrow it.' if self.overlay_cpus() is not None else
                'Effective CPU set unavailable; no placement inferred.', *indicators(self.snapshot, group)]
