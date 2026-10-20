"""Terminal presentation, separate from Linux telemetry."""

from collections import defaultdict, deque
import curses
import os
import queue
import re
import sys
import textwrap
import threading
import time

from .presentation import GLYPHS, card_layout, chart, compact_overview, draw_card, overview_cards, palette
from .controls import ControlSession
from .hardware_ui import HardwareView
from .placement_ui import PlacementView
from .topology import topology_map
from .placement import format_cpus
from .resource_panels import resource_lines, expanded_overview
from .navigation import NavigationView, actions, context_help
from .focus import FocusView, KINDS
from .investigation import Investigation, indicators
from .usability_ui import UsabilityView
from .usability import tree_rows
from .workspaces import Workspaces, related_groups
from .cgroup_browser import CgroupBrowser
from .application_processes import ApplicationProcesses
from .memory_sharing import MemorySharingView
from .profiling import ProfileView


UNSAFE_TEXT = re.compile('[^\x20-\x7e' + re.escape(''.join(GLYPHS)) + ']')


def clean(value):
    # Keep the same allowlist, with the per-character scan in the regex engine.
    return UNSAFE_TEXT.sub('?', str(value))


def size(value):
    if value is None:
        return "N/A"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(value) < 1024 or unit == "PiB":
            return f"{value:.1f}{unit}"
        value /= 1024


def pct(value):
    return "N/A" if value is None else f"{value:.1f}%"


def ratio(used, total):
    return used / total * 100 if used is not None and total else None


def cpu_setting(property, value):
    if value is None:
        return "default (100)" if property == "weight" else "no local quota"
    return f"{value:g}" if property == "weight" else f"{value:g}% of one CPU"


def bar(value, width=22):
    if value is None:
        return "[" + " no data ".center(width, ".") + "] N/A"
    fill = round(max(0, min(100, value)) / 100 * width)
    return "[" + "#" * fill + "." * (width - fill) + "] " + pct(value)


def spark(values, width=40, maximum=100, unicode=False):
    values = list(values)[-width:]
    if not values or all(v is None for v in values):
        return "N/A (no measured data)"[:width]
    levels = "·▁▂▃▄▅▆▇█" if unicode else "._-:=+*#%@"
    return "".join(" " if v is None else levels[min(len(levels) - 1, max(0,
                    int(v / max(1, maximum) * (len(levels) - 1))))] for v in values)


def pressure(snapshot, kind):
    value = snapshot["pressure"].get(kind, {}).get("some", {}).get("avg10")
    return f"{kind.upper()} pressure (10s): {pct(value)}"


class View(HardwareView, PlacementView, NavigationView, FocusView, UsabilityView, Workspaces, ApplicationProcesses):
    names = ("Overview", "CPU", "Memory", "Disks", "GPU", "Processes", "Network", "Applications", "Timeline", "Capabilities")

    def __init__(self, unicode=False, control=False, session=None):
        self.memory_sharing = None
        self.profile_view = None
        self.profile_jobs = []
        self.proc_root = '/proc'
        self.application_process_init()
        self.browser = None
        self.browser_button = None
        self.workspace_init()
        self.usability_init()
        self.hardware_init()
        self.placement_init()
        self.overview_expanded = False
        self.overview_links = {}
        self.overview_cards_regions = []
        self.focus = None
        self.overlay = None
        self.investigation = Investigation()
        self.sample_times = deque(maxlen=120)
        self.recorder = None
        self.replay = None
        self.replay_delta = 0
        self.replay_playing = False
        self.pending_action = {}
        self.page = 0
        self.scroll = 0
        self.sort = "cpu"
        self.filter = ""
        self.workload_filter = ""
        self.filter_context = "processes"
        self.editing_filter = False
        self.paused = False
        self.help = False
        self.help_return_scroll = 0
        self.action_regions = []
        self.history = defaultdict(lambda: deque(maxlen=120))
        self.snapshot = None
        self.unicode = unicode
        self.colors = {key: 0 for key in ("accent", "cpu", "memory", "disk", "gpu", "network", "muted", "warning")}
        self.tab_regions = []
        self.control = control
        self.session = session or ControlSession()
        self.workload_id = None
        self.workload_detail = False
        self.modal = None
        self.results = queue.SimpleQueue()
        self.screen_size = (24, 80)

    def trend(self, values, width=40, maximum=100):
        return spark(values, width, maximum, unicode=self.unicode)

    def update(self, snapshot):
        self.investigation.observe(snapshot)
        if self.paused:
            return
        self.snapshot = snapshot
        self.sample_times.append(snapshot['timestamp'])
        active_devices = set()
        for kind, items, fields in [('disk', snapshot['disks'], ('active_pct', 'read_bps', 'write_bps')),
                                    ('gpu', snapshot['gpus'], ('busy_pct', 'memory_used', 'power_w'))]:
            for item in items:
                identity = str(item.get('identity', item.get('id', item.get('name'))))
                for field in fields:
                    key = f'device:{kind}:{identity}:{field}'
                    active_devices.add(key)
                    self.history[key].append(item.get(field))
        for key in list(self.history):
            if isinstance(key, str) and key.startswith('device:') and key not in active_devices:
                del self.history[key]
        aggregate = next((c for c in snapshot["cpu"] if c["id"] == "cpu"), {})
        self.history["cpu"].append(aggregate.get("busy_pct"))
        memory = snapshot["memory"]
        self.history["memory"].append(ratio(memory["used"], memory["total"]))
        self.history["disk"].append(max((d["active_pct"] for d in snapshot["disks"]
                                          if d["active_pct"] is not None), default=None))
        self.history["gpu"].append(max((g["busy_pct"] for g in snapshot["gpus"]
                                         if g["busy_pct"] is not None), default=None))
        identities = {(entry["name"], entry["ifindex"]) for entry in snapshot["network"]}
        for key in list(self.history):
            if isinstance(key, tuple) and key[:2] not in identities:
                del self.history[key]
        for entry in snapshot["network"]:
            for direction in ("rx", "tx"):
                self.history[(entry["name"], entry["ifindex"], direction)].append(entry[f"{direction}_bps"])
        active = {"workload:" + group["id"] for group in snapshot.get("workloads", [])}
        for key in list(self.history):
            if isinstance(key, str) and key.startswith("workload:") and key not in active:
                del self.history[key]
        for group in snapshot.get("workloads", []):
            if self.app_history_identity.get(group['id']) != group.get('inode'):
                self.history['workload:'+group['id']].clear()
            self.history["workload:" + group["id"]].append(group["cpu_pct"])
        self.app_history_identity = {g['id']:g.get('inode') for g in snapshot.get('workloads',[])}

    def device_history(self, kind, item, field):
        identity = str(item.get('identity', item.get('id', item.get('name'))))
        return self.history[f'device:{kind}:{identity}:{field}']

    def marker_line(self, width):
        times = list(self.sample_times)[-width:]
        marks = [' '] * len(times)
        if times:
            for event in self.investigation.events:
                if times[0] <= event['timestamp'] <= times[-1]:
                    index = min(range(len(times)), key=lambda i: abs(times[i] - event['timestamp']))
                    marks[index] = '|'
        return 'Marks ' + ''.join(marks).rjust(width) + ' (M mark; 9 compare)'

    def mark_event(self, label, status='marker', target=None, timestamp=None):
        event = self.investigation.mark(label, timestamp, target, status)
        if self.recorder:
            self.recorder.write('event', event)
        return event

    def open_profile(self, scheduler=False):
        if self.replay:
            self.modal=dict(stage='result',message='Profiling requires a live target; disabled during replay.')
            return
        target=None
        if self.browser and self.browser.process:
            pid,start=self.browser.process
            target=dict(pid=pid,expected_start=start)
        elif self.browser:
            target=dict(cgroup='/sys/fs/cgroup/'+self.browser.path.lstrip('/'),expected_inode=self.browser.inode)
        elif self.page==7 and self.application_mode=='processes':
            rows=self.application_process_rows()
            selected=self.selected_application_process(rows)
            if selected is not None:
                row=rows[selected]
                target=dict(pid=row['pid'],expected_start=row.get('starttime'))
        elif self.page==7:
            group=self.selected_workload()
            if group and group.get('path'):
                target=dict(cgroup='/sys/fs/cgroup/'+group['path'].lstrip('/'),expected_inode=group.get('inode'))
        if target:
            if scheduler:target['scheduler']=True
            self.profile_view=ProfileView(target,choose=not scheduler)
            self.profile_jobs=[job for job in self.profile_jobs if job.result is None and job.error is None and not job.stop.is_set()]
            self.profile_jobs.append(self.profile_view)
        else:
            self.modal=dict(stage='result',message='Select a process or cgroup in Applications, then press P to profile for 10 seconds.')

    def open_memory_sharing(self):
        if self.replay:
            self.modal = dict(stage='result',message='Memory sharing inspection requires live /proc data; it is disabled during replay.')
            return
        target = None
        if self.browser and self.browser.process:
            target = next((p for p in self.snapshot.get('processes',[]) if (p['pid'],p.get('starttime')) == self.browser.process),None)
        elif self.page == 7 and self.application_mode == 'processes':
            rows = self.application_process_rows()
            selected = self.selected_application_process(rows)
            if selected is not None:
                target = rows[selected]
                self.application_process_manual = True
        if target:
            self.memory_sharing = MemorySharingView(target,self.snapshot.get('processes',[]),self.proc_root)
        else:
            self.modal = dict(stage='result',message='Select a process in Applications, or open a PID inside a cgroup, then press V.')

    def open_cgroup_browser(self, root=False):
        group = self.selected_workload()
        if self.snapshot and (root or group):
            self.browser = CgroupBrowser(self.snapshot, '/' if root else group.get('path') or '/')

    def workload_rows(self):
        groups = related_groups(self.snapshot, self.relation) if self.snapshot else []
        groups = [group for group in groups if self.workload_filter.casefold() in
                  f"{group.get('unit') or ''} {group['id']}".casefold()]
        def order(group):
            if self.workload_sort == 'name':
                return (group.get('unit') or group['id']).casefold()
            if self.workload_sort == 'io':
                return sum((d.get('read_bps') or 0)+(d.get('write_bps') or 0) for d in group.get('io_devices', []))
            value = group.get('memory_current' if self.workload_sort == 'memory' else 'cpu_pct')
            return value if value is not None else -1
        rows = sorted(groups, key=order, reverse=self.workload_sort != 'name')
        if self.workload_id is None:
            self.workload_id = rows[0]['id'] if rows else None
        elif self.workload_id not in {group['id'] for group in rows}:
            self.workload_notice = 'Selection exited or is filtered out; use arrows to select another workload.'
            self.workload_detail = False
        group = next((g for g in rows if g['id'] == self.workload_id), None)
        identity = (group['id'], group.get('inode')) if group else None
        if self.workload_identity and identity and identity[0] == self.workload_identity[0] and identity != self.workload_identity:
            self.workload_notice = 'Selected service restarted/replaced; review its new identity before controlling it.'
            self.workload_detail = False
        if identity is not None:
            self.workload_identity = identity
        return rows

    def selected_workload(self):
        return next((group for group in self.workload_rows() if group["id"] == self.workload_id), None)

    def workload_lines(self, width=100):
        rows = self.workload_rows()
        group = self.selected_workload()
        mode = "CPU controls enabled: p placement | U undo placement | w weight | l quota | u Undo" if self.control else "Read-only | use --control to enable runtime CPU changes"
        if not self.workload_detail or group is None:
            name_width = max(12, min(45, width - 32))
            lines = [f"{len(rows)} workloads | / find | Enter open | Filter: {clean(self.workload_filter) or '(none)'}", mode,
                     self.workload_notice or self.relation_label or f"Sort: {self.workload_sort} | c CPU / m RAM / n name / i I/O | b disk limits / B Undo",
                     f"  {'UNIT / GROUP':<{name_width}} {'CPU':>7} {'MEMORY':>10} {'PIDS':>5}"]
            for row in rows:
                name = clean(row["unit"] or row["id"])
                lines.append(f"{'>' if row['id'] == self.workload_id else ' '} {name[:name_width]:<{name_width}} "
                             f"{pct(row['cpu_pct']):>7} {size(row['memory_current']):>10} {len(row['pids']):>5}")
            if not rows:
                lines.append("No matching groups. Clear search with / and relationship with x; cgroup v2 is required.")
            return lines
        return self.application_lines(width)

    def job(self, kind, function, *args, allow_replay=False):
        if self.replay and not allow_replay:
            self.modal = {'stage': 'result', 'message': 'Replay is read-only; live control operations are disabled.'}
            return
        self.pending_action = {'plan': args[0] if args and isinstance(args[0], dict) else {}, 'timestamp': time.time(), 'target': args[0].get('target') if args and isinstance(args[0], dict) else None}
        self.modal = {"stage": "busy", "kind": kind}
        def execute():
            try:
                self.results.put((kind, function(*args), None))
            except Exception as exc:
                self.results.put((kind, None, str(exc)))
        threading.Thread(target=execute, daemon=True).start()

    def poll_actions(self):
        while not self.results.empty():
            kind, result, error = self.results.get()
            if kind.startswith("utility_"):
                self.utility_result(kind, result, error)
                continue
            if kind in ('apply', 'undo', 'hardware_apply', 'hardware_undo', 'placement_apply', 'placement_undo'):
                target = (result or {}).get('target') or self.pending_action.get('target')
                identity = {'id': target['path'], 'inode': target.get('inode')} if target and target.get('path') else None
                label = kind.replace('_', ' ') + (': ' + target['unit'] if target and target.get('unit') else '')
                detail = result or self.pending_action.get('plan', {})
                plan = detail.get('plan', detail)
                if 'setting' in plan:
                    label += f" {plan['setting']}={plan.get('value')} scope={plan.get('scope')}"
                elif 'property' in detail:
                    value = detail.get('before', {}).get(detail['property']) if kind == 'undo' else detail.get('requested', detail.get('value'))
                    label += f" {detail['property']}={value}"
                elif kind.startswith('placement'):
                    value = detail.get('before', {}).get('allowed') if kind.endswith('undo') else detail.get('requested', detail.get('value'))
                    label += ' CPUs=' + (format_cpus(value) if value else 'inherit')
                self.mark_event(label, 'unconfirmed' if error else 'verified', identity, self.pending_action.get('timestamp'))
            if error:
                self.modal = {"stage": "result", "message": error}
            elif kind == 'placement_open':
                self.placement_ready(result)
            elif kind == 'placement_prepare':
                self.modal = dict(stage='placement_confirm', plan=result)
            elif kind in ('placement_apply', 'placement_undo'):
                self.placement = None
                self.page, self.workload_detail, self.scroll = 7, True, 0
                self.modal = dict(stage='result', message='CPU placement restored and verified.' if kind == 'placement_undo' else 'CPU placement verified in systemd and the kernel. U on Workloads: Undo placement.')
            elif kind == "hardware_prepare":
                self.modal = {"stage": "hardware_confirm", "plan": result}
            elif kind.startswith("hardware_"):
                self.modal = {"stage": "result", "message": "Hardware change restored and verified." if kind == "hardware_undo" else "Hardware change verified. CPU page: u to Undo."}
            elif kind == "prepare":
                self.modal = {"stage": "confirm", "plan": result}
            else:
                self.modal = {"stage": "result", "message": (
                    f"{result['target']['unit']}: previous CPU {result['property']} restored and verified." if kind == "undo" else
                    f"{result['target']['unit']}: CPU {result['property']} confirmed in systemd and the kernel. u: Undo latest change.")}

    def modal_lines(self):
        modal = self.modal
        if modal['stage'].startswith('utility_'):
            return self.utility_lines()
        if modal['stage'] == 'focus':
            return self.focus_lines()
        if modal['stage'] == 'navigate':
            return self.navigation_lines()
        if modal['stage'] == 'placement_confirm':
            return self.placement_lines()
        if modal["stage"].startswith("hardware_"):
            return self.hardware_lines()
        if modal["stage"] == "busy":
            return ["Running the requested action...", "The dashboard continues collecting data.", "Please wait for a confirmed result."]
        if modal["stage"] == "input":
            property = modal["property"]
            return [f"Set CPU {property} for {modal['target']['unit']}",
                    "Weight: 1-10000, relative priority among sibling groups." if property == "weight" else
                    "Quota: whole percent; 100% = one CPU, 200% = two CPUs.",
                    "Enter 'default' to reset weight or 'max' to remove quota.",
                    "Runtime only. The next screen previews the actual setting.",
                    "Value: " + modal["value"] + "_", "Enter: preview | Esc: cancel"]
        if modal["stage"] == "confirm":
            plan = modal["plan"]
            property = plan["property"]
            return ["Confirm temporary CPU setting", f"Unit: {plan['target']['unit']}",
                    f"Manager: {plan['target']['manager']}", "Cgroup: " + plan["target"]["path"],
                    f"{property.title()}: {cpu_setting(property, plan['before'][property])} -> {cpu_setting(property, plan['value'])}",
                    "Weight affects competition; quota limits CPU time.",
                    "Applies to this entire unit and its descendants.",
                    "Remains active after UI exit, until changed or reboot.",
                    "Undo restores the previous value using systemd.",
                    "Enter: apply and verify | Esc: cancel"]
        if modal["stage"] == "quit":
            return ["CPU changes may still be active.", "Runtime settings remain after leaving this dashboard.",
                    "This session's Undo history is lost when you exit.",
                    "Enter: exit and keep settings | Esc: return | u: Undo (disk, placement, hardware, workload)"]
        return [modal["message"], "", "Enter or Esc: close | 8: inspect workloads"]

    def modal_key(self, key):
        stage = self.modal["stage"]
        if stage.startswith("utility_"):
            return self.utility_modal_key(key)
        if stage == 'focus':
            return self.focus_key(key)
        if stage == 'navigate':
            return self.navigation_key(key)
        if stage == 'hardware_menu':
            key = {ord('j'): curses.KEY_DOWN, ord('k'): curses.KEY_UP}.get(key, key)
        if stage == 'placement_confirm':
            return self.placement_confirm_key(key)
        if stage.startswith("hardware_"):
            return self.hardware_key(key)
        if stage == "busy":
            return True
        if stage == "quit":
            if key in (10, 13):
                return False
            if key == ord("u"):
                if any(e['status'] != 'undone' for e in self.disk_session.events):
                    self.job("utility_disk_undo", self.disk_session.undo)
                elif any(e['status'] != 'undone' for e in self.placement_session.events):
                    self.job('placement_undo', self.placement_session.undo)
                elif any(e['status'] != 'undone' for e in self.hardware.events):
                    self.job('hardware_undo', self.hardware.undo)
                else:
                    self.job("undo", self.session.undo)
            elif key == 27:
                self.modal = None
            return True
        if key == 27:
            self.modal = None
            return True
        if stage == "input":
            if key in (10, 13):
                modal = self.modal
                self.job("prepare", self.session.prepare, modal["target"], modal["property"], modal["value"])
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.modal["value"] = self.modal["value"][:-1]
            elif 32 <= key < 127 and len(self.modal["value"]) < 12:
                self.modal["value"] += chr(key)
        elif stage == "confirm" and key in (10, 13):
            width = max(20, min(76, self.screen_size[1] - 8))
            length = sum(max(1, len(textwrap.wrap(clean(line), width))) for line in self.modal_lines())
            if length <= self.screen_size[0] - 5:
                self.job("apply", self.session.apply, self.modal["plan"])
        elif stage == "result" and key in (10, 13, ord("8")):
            self.modal = None
            if key == ord("8"):
                self.page = 7
        return True

    def cpu_lines(self, width=60):
        s = self.snapshot
        aggregate = next((c for c in s['cpu'] if c['id'] == 'cpu'), {})
        data = s.get('cpu_hardware', {'cpus': [], 'policies': {}})
        if self.focus and self.focus[0] == 'cpu' and not self.placement:
            data = {**data, 'cpus': [c for c in data['cpus'] if str(c['socket']) == self.focus[1]]}
        metrics = {c['id']: c for c in s['cpu']}
        p = self.placement
        if p:
            lines = ['Place: ' + clean(p['target']['unit']),
                     'Selected: ' + ('inherit parent' if p['inherit'] else format_cpus(p['selected']) or '(empty)'),
                     'x selected | - eligible | ! excluded | click CPU/core/socket',
                     '[ All ]  [ None ]  [ Inherit ]  [ Preview ]  [ Cancel ]']
            self.topology_line_regions = {3: [(0, 7, 'action', ('a',)), (9, 17, 'action', ('n',)),
                                             (19, 30, 'action', ('r',)), (32, 43, 'action', ('enter',)),
                                             (45, 55, 'action', ('esc',))]}
        else:
            lines = [clean(s['cpu_model']),
                     'CPU ' + bar(aggregate.get('busy_pct'), 18) + ' | ' + pressure(s, 'cpu'),
                     'History ' + self.trend(self.history['cpu'], max(8, width - 10)),
                     '[ All CPUs / sockets settings ]  g menu | click/right-click thread',
                     'Shift-click CPU/core/socket: related apps | tile positions are not chip coordinates.']
            self.topology_line_regions = {}
        if not p:
            lines += self.overlay_lines()
            lines += indicators(s)
            lines.append(self.marker_line(max(8, min(50, width - 30))))
        overlay_cpus = self.overlay_cpus() if self.overlay else None
        tiles, hits = topology_map(data, metrics, width,
            p['selected'] if p else overlay_cpus, set(p['info']['eligible']) if p else {c['id'] for c in data['cpus']}, p['focus'] if p else None)
        offset = len(lines)
        self.topology_line_regions.update({row + offset: value for row, value in hits.items()})
        lines.extend(tiles)
        if not p:
            energy=s.get('energy')
            if energy:
                domains=energy.get('domains',[])
                values=[d['label']+': '+(f"{d['watts']:.2f} W" if d.get('watts') is not None else d['status']) for d in domains[:3]]
                lines.append('Hardware energy: '+(' | '.join(values) if values else 'unavailable (no exposed energy counters)'))
                if domains:lines.append('Separate hardware domains, not additive or process-attributed. 0: full sensor inventory.')
        if not data['policies']:
            lines.append('Frequency governors: unavailable on this system.')
        for policy, info in data['policies'].items():
            lines.append(f"{policy}: {info['governor'] or 'N/A'} | shared CPUs {format_cpus(info['cpus'])}")
        for sensor in s['temperatures']:
            lines.append(f"{clean(sensor['name'])}: {sensor['temperature_c']:.1f} C")
        return lines

    def memory_lines(self, width=60):
        return resource_lines(self, "memory", width)

    def disk_lines(self, width=60):
        return resource_lines(self, "disk", width)

    def gpu_lines(self, width=60):
        return resource_lines(self, "gpu", width)

    def primary_network(self):
        entries = self.snapshot["network"]
        entries = [entry for entry in entries if entry["name"] != "lo"] or entries
        return max(entries, key=lambda entry: (
            entry["state"] == "up", (entry["rx_bps"] or 0) + (entry["tx_bps"] or 0)), default=None)

    def network_lines(self, width=60):
        if self.page == 0 and not self.overview_expanded:
            net = self.primary_network()
            if not net:
                return ['Network: N/A (no readable interface counters)']
            return [f"{clean(net['name'])} ({clean(net['state'])}) RX {size(net['rx_bps'])}/s TX {size(net['tx_bps'])}/s",
                    f"{len(self.snapshot['network']) - 1} other interfaces | 7 Network: all interfaces and totals"]
        return resource_lines(self, "network", width)

    def process_lines(self, width=100):
        rows = [p for p in self.snapshot["processes"] if self.filter.casefold() in
                f"{p['pid']} {p['name']}".casefold()]
        keys = {"cpu": "cpu_pct", "memory": "memory", "pid": "pid", "name": "name"}
        key = keys[self.sort]
        rows.sort(key=lambda p: p[key] if p[key] is not None else -1,
                  reverse=self.sort in ("cpu", "memory"))
        name_width = max(10, min(30, width - 32))
        lines = [f"Sort: {self.sort} | t tree={self.process_tree} | Filter: {clean(self.filter) or '(none)'} | {len(rows)} visible",
                 "CPU: 100% = one logical CPU; memory: resident pages (RSS).",
                 f"{'NAME':<{name_width}} {'PID':>7} {'CPU':>7} {'MEMORY':>10}" +
                 (f" {'RAM':>6} {'NICE':>4} STATE" if width >= 85 else "")]
        for p, depth in (tree_rows(rows) if self.process_tree else [(p,0) for p in rows]):
            label = ("  " * min(depth, 8) + p["name"])
            lines.append(f"{clean(label)[:name_width]:<{name_width}} {p['pid']:>7} {pct(p['cpu_pct']):>7} {size(p['memory']):>10}" +
                         (f" {pct(p['memory_pct']):>6} {p['nice']:>4} {p['state']:>5}" if width >= 85 else ""))
        lines.append(f"Skipped/exited/unreadable processes: {self.snapshot['processes_skipped']}")
        return lines

    def lines(self, width):
        if self.help:
            return [piece for line in context_help(self)
                    for piece in (textwrap.wrap(line, max(20, width)) or [''])]
        return {1: self.cpu_lines, 2: self.memory_lines, 3: self.disk_lines,
                9: self.capability_lines, 4: self.gpu_lines, 6: self.network_lines, 7: self.workload_lines, 8: lambda width: [piece for line in self.investigation.lines() for piece in (textwrap.wrap(line, width) or [''])]}.get(self.page, self.process_lines)(width)

    def mouse(self, x, y, state):
        wheel_up = state & getattr(curses, "BUTTON4_PRESSED", 0)
        wheel_down = state & getattr(curses, "BUTTON5_PRESSED", 0)
        left = state & (getattr(curses, "BUTTON1_CLICKED", 0) | getattr(curses, "BUTTON1_PRESSED", 0))
        right = state & (getattr(curses, "BUTTON3_CLICKED", 0) | getattr(curses, "BUTTON3_PRESSED", 0))
        if self.modal:
            hit = self.modal_regions.get(y)
            if left and hit and hit[0] <= x < hit[1]:
                if self.modal['stage'] == 'focus':
                    self.modal['selected'] = hit[2]
                    self.focus_key(10)
                elif self.modal['stage'] == 'navigate':
                    self.modal['selected'] = hit[2]
                    self.navigation_key(10)
                elif self.modal['stage'] == 'hardware_menu':
                    self.modal['selected'] = hit[2]
                    self.hardware_choose()
                elif self.modal['stage'] == 'placement_confirm' and hit[2] == 'apply':
                    self.placement_confirm_key(10)
                elif self.modal['stage'] == 'hardware_confirm' and hit[2] == 'apply':
                    self.hardware_key(10)
                elif self.modal['stage'] == 'utility_disk_confirm' and hit[2] == 'apply':
                    self.utility_modal_key(10)
                elif self.modal['stage'] in ('hardware_confirm', 'placement_confirm', 'utility_disk_confirm') and hit[2] == 'cancel':
                    self.modal = None
            return
        if self.profile_view:
            if left and y == self.screen_size[0]-1:
                for start,end,key in self.action_regions:
                    if start<=x<end:return self.key(key,self.screen_size[0])
            if left:self.profile_view.mouse(x,y)
            if wheel_up:self.profile_view.key(curses.KEY_UP,self.screen_size[0])
            elif wheel_down:self.profile_view.key(curses.KEY_DOWN,self.screen_size[0])
            return
        if self.memory_sharing:
            if left and y == self.screen_size[0] - 1:
                for start,end,key in self.action_regions:
                    if start <= x < end:return self.key(key,self.screen_size[0])
            if left:
                for start,end,row,tab in self.memory_sharing.regions:
                    if y == row and start <= x < end:
                        self.memory_sharing.tab,self.memory_sharing.scroll=tab,0
                        return
            for button,key in ((curses.BUTTON4_PRESSED,curses.KEY_UP),(getattr(curses,'BUTTON5_PRESSED',0),curses.KEY_DOWN)):
                if state & button:
                    for _ in range(3):self.memory_sharing.key(key,self.screen_size[0])
            return
        if self.browser:
            if left and y == self.screen_size[0] - 1:
                for start,end,key in self.action_regions:
                    if start <= x < end:
                        return self.key(key,self.screen_size[0])
            if self.browser.mouse(x,y,state,self.snapshot,self.screen_size[0]) is False:
                self.browser = None
            return
        if self.page == 7 and left and self.application_mode_region and y == 2 and self.application_mode_region[0] <= x < self.application_mode_region[1]:
            return self.key(ord('g'),self.screen_size[0])
        if self.page == 7 and self.application_mode == 'processes':
            if left:
                for start,end,row,identity in self.application_process_regions:
                    if y == row and start <= x < end:
                        self.application_process_manual = True
                        self.application_process = identity
                        return
            for button,key in ((curses.BUTTON4_PRESSED,curses.KEY_UP),(getattr(curses,'BUTTON5_PRESSED',0),curses.KEY_DOWN)):
                if state & button:
                    for _ in range(3):self.application_process_key(key,self.screen_size[0])
                    return
        if left and self.page == 7 and self.browser_button and y == 2 and self.browser_button[0] <= x < self.browser_button[1]:
            self.open_cgroup_browser()
            return
        if left and y == self.screen_size[0] - 1:
            for start, end, key in self.action_regions:
                if start <= x < end:
                    return self.key(key, self.screen_size[0])
        if self.help:
            if state & getattr(curses, 'BUTTON4_PRESSED', 0):
                self.scroll = max(0, self.scroll - 3)
            elif state & getattr(curses, 'BUTTON5_PRESSED', 0):
                self.scroll += 3
            return
        if left and not self.placement:
            if self.page == 7:
                for start,end,row,tab in self.app_tab_regions:
                    if y==row and start<=x<end:
                        self.app_tab,self.scroll=tab,0
                        return
                for start,end,row,identity,inode in self.app_regions:
                    if y==row and start<=x<end:
                        self.workload_id,self.workload_identity=identity,(identity,inode)
                        self.workload_notice,self.workload_detail,self.scroll='',False,0
                        return
                if self.app_regions and y>=5:
                    if x > self.app_regions[0][1]:
                        self.workload_detail = True
                    return  # Right detail pane is not a workload-list row.
        if self.page == 0 and not self.help and left:
            if self.page == 0 and y == 2 and 1 <= x <= 26:
                self.overview_expanded = not self.overview_expanded
                self.scroll = 0
                return
            if y in self.overview_links:
                self.page, self.scroll = self.overview_links[y], 0
                self.focus = None
                return
            for top, start, height, width, page in self.overview_cards_regions:
                if top <= y < top + height and start <= x < start + width:
                    self.page, self.scroll = page, 0
                    self.focus = None
                    return
        if self.page == 7 and self.application_mode == 'cgroups' and not self.workload_detail and left and y >= 7:
            rows = self.workload_rows()
            index = y - 3 + self.scroll - 4
            if 0 <= index < len(rows):
                group = rows[index]
                self.workload_id, self.workload_identity = group['id'], (group['id'],group.get('inode'))
                self.workload_notice = ''
                return
        if self.page == 1 and not self.help and (left or right):
            for start, end, kind, ids in self.topology_regions.get(y, []):
                if start <= x - 1 < end:
                    if self.placement:
                        if kind == 'action':
                            self.placement_key({'enter': 10, 'esc': 27}.get(ids[0], ord(ids[0][0])))
                        else:
                            self.placement_toggle(ids)
                    elif left and state & getattr(curses, 'BUTTON_SHIFT', 0) and kind in ('cpu','core','socket'):
                        self.hardware_selection = tuple(ids)
                        self.open_related()
                    elif kind == 'cpu':
                        self.hardware_menu(('id', ids[0]))
                    elif kind == 'socket':
                        cpu = next(c for c in self.snapshot['cpu_hardware']['cpus'] if c['id'] == ids[0])
                        if cpu['socket'] is not None:
                            self.hardware_menu(('socket', cpu['socket']))
                    return
        if self.page == 1 and not self.help and (left or right) and y in self.cpu_regions:
            self.hardware_menu(self.cpu_regions[y])
            return
        if self.placement and y == 1:
            return
        if state & (getattr(curses, "BUTTON1_CLICKED", 0) | getattr(curses, "BUTTON1_PRESSED", 0)) and y == 1:
            for start, end, page in self.tab_regions:
                if start <= x < end:
                    self.page, self.scroll = page, 0
                    self.focus = None
                    return
        wheel_up = state & getattr(curses, "BUTTON4_PRESSED", 0)
        wheel_down = state & getattr(curses, "BUTTON5_PRESSED", 0)
        if self.page == 7 and self.app_regions and (wheel_up or wheel_down):
            self.workload_detail = x > self.app_regions[0][1]
            if not self.workload_detail:
                for _ in range(3):
                    self.key(curses.KEY_UP if wheel_up else curses.KEY_DOWN, self.screen_size[0])
                return
        if wheel_up:
            self.scroll = max(0, self.scroll - 3)
        elif wheel_down:
            self.scroll += 3

    def key(self, key, height=24):
        if self.modal:
            if key == curses.KEY_MOUSE:
                try:
                    _, x, y, _, state = curses.getmouse()
                    self.mouse(x, y, state)
                except curses.error:
                    pass
                return True
            return self.modal_key(key)
        if self.editing_filter:
            if key == curses.KEY_MOUSE:
                try:
                    _, x, y, _, state = curses.getmouse()
                    if y == self.screen_size[0] - 1:
                        self.mouse(x, y, state)
                except curses.error:
                    pass
                return True
            field = "workload_filter" if self.filter_context == "workloads" else "filter"
            if key in (10, 13):
                self.editing_filter = False
            elif key == 27:
                setattr(self, field, "")
                self.editing_filter = False
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                setattr(self, field, getattr(self, field)[:-1])
            elif 32 <= key < 127 and len(getattr(self, field)) < 80:
                setattr(self, field, getattr(self, field) + chr(key))
            self.scroll = 0
            return True
        if key == curses.KEY_MOUSE:
            try:
                _, x, y, _, state = curses.getmouse()
                if self.mouse(x, y, state) is False:
                    return False
            except curses.error:
                pass
            return True
        if self.profile_view:
            if not self.profile_view.key(key,height):self.profile_view=None
            return True
        if key in (ord('P'),ord('W')) and not self.help and not self.placement and not self.memory_sharing:
            self.open_profile(scheduler=key==ord('W'))
            return True
        if self.memory_sharing:
            if key == ord('r'):
                if self.memory_sharing.result is None and self.memory_sharing.error is None:
                    return True
                old=self.memory_sharing
                old.stop.set()
                self.memory_sharing=MemorySharingView(old.target,self.snapshot.get('processes',[]),self.proc_root)
            elif not self.memory_sharing.key(key,height):
                self.memory_sharing=None
            return True
        if key == ord('V') and not self.help and not self.placement:
            self.open_memory_sharing()
            return True
        if self.browser:
            if not self.browser.key(key,self.snapshot,height):
                self.browser = None
            return True
        key = {curses.KEY_F1: ord('?'), curses.KEY_F4: ord('/'), curses.KEY_F10: ord('q')}.get(key, key)
        if key == ord('?') or self.help and key == 27:
            if self.help:
                self.help, self.scroll = False, self.help_return_scroll
            else:
                self.help_return_scroll, self.scroll, self.help = self.scroll, 0, True
            return True
        if self.help:
            if key in (curses.KEY_DOWN, ord('j'), curses.KEY_NPAGE):
                self.scroll += max(1, height - 5) if key == curses.KEY_NPAGE else 1
            elif key in (curses.KEY_UP, ord('k'), curses.KEY_PPAGE):
                self.scroll = max(0, self.scroll - (max(1, height - 5) if key == curses.KEY_PPAGE else 1))
            elif key == curses.KEY_HOME:
                self.scroll = 0
            elif key == curses.KEY_END:
                self.scroll = 10**9
            return True
        if self.placement:
            return self.placement_key(key)
        if self.page == 7 and key == ord('g'):
            self.application_mode = 'cgroups' if self.application_mode == 'processes' else 'processes'
            self.scroll,self.workload_detail = 0,False
            return True
        if self.application_process_key(key,height):
            return True
        if self.page == 7 and key in (10, 13, curses.KEY_RIGHT, ord('v'), ord('T')):
            self.open_cgroup_browser(root=key == ord('T'))
            return True
        if self.workspace_key(key):
            return True
        if self.utility_key(key):
            return True
        if self.replay and key in (curses.KEY_LEFT, curses.KEY_RIGHT, ord(' '), ord('('), ord(')'), ord(','), ord('.')):
            if key == ord(' '):
                self.replay_playing = not self.replay_playing
            else:
                self.replay_delta += {curses.KEY_LEFT: -1, curses.KEY_RIGHT: 1, ord('('): -len(self.replay.offsets), ord(')'): len(self.replay.offsets), ord(','): -10, ord('.'): 10}[key]
                self.replay_playing = False
            return True
        if key == ord('f') and self.page in (0, 1, 2, 3, 4, 6):
            self.open_focus()
            return True
        if key in (ord('['), ord(']')) and self.focus:
            self.cycle_focus(-1 if key == ord('[') else 1)
            return True
        if key == ord('o'):
            if self.page == 7:
                group = self.selected_workload()
                if group:
                    self.overlay = {'id': group['id'], 'inode': group.get('inode')}
                    self.focus = None
                    self.page, self.scroll = 1, 0
            else:
                self.overlay = None
            return True
        if key == ord('M') and self.snapshot:
            self.mark_event('Manual baseline', target=self.overlay, timestamp=self.snapshot['timestamp'])
            return True
        if self.page == 8 and key in (curses.KEY_UP, curses.KEY_DOWN, ord('j'), ord('k')):
            count = len(self.investigation.events)
            if count:
                self.investigation.selected = (self.investigation.selected + (1 if key in (curses.KEY_DOWN, ord('j')) else -1)) % count
                self.scroll = 0
            return True
        if key == ord(':'):
            self.modal = dict(stage='navigate', query='', selected=0)
            return True
        key = {ord('j'): curses.KEY_DOWN, ord('k'): curses.KEY_UP}.get(key, key)
        if key in (ord("q"), 27):
            if key == 27 and self.page == 7 and self.workload_detail:
                self.workload_detail, self.scroll = False, 0
                return True
            if any(event["status"] in ("confirmed", "unconfirmed") for event in self.session.events + self.hardware.events + self.placement_session.events + self.disk_session.events):
                self.modal = {"stage": "quit"}
                return True
            return False
        if self.page == 0 and not self.help and key == ord('e'):
            self.overview_expanded = not self.overview_expanded
            self.scroll = 0
            return True
        if self.page == 1 and key in (ord('g'), ord('u')):
            if key == ord('g') or not self.control:
                self.hardware_menu()
            else:
                self.job('hardware_undo', self.hardware.undo)
            return True
        if self.page == 7 and key in (ord('p'), ord('U')):
            if key == ord('p'):
                self.open_placement()
            elif self.control:
                self.job('placement_undo', self.placement_session.undo)
            return True
        if self.page == 7 and key in (ord("w"), ord("l"), ord("u")):
            if not self.control:
                self.modal = {"stage": "result", "message": "Controls are disabled. Restart with --control to enable temporary CPU changes."}
            elif self.screen_size[0] < 18 or self.screen_size[1] < 70:
                self.modal = {"stage": "result", "message": "Resize to at least 70 columns by 18 rows to preview CPU controls."}
            elif key == ord("u"):
                self.job("undo", self.session.undo)
            else:
                group = self.selected_workload()
                if not group or not group["controllable"]:
                    self.modal = {"stage": "result", "message": "Select a systemd service/scope owned by an accessible manager."}
                else:
                    self.modal = {"stage": "input", "target": dict(group), "property": "weight" if key == ord("w") else "quota", "value": ""}
            return True
        if self.page == 7 and not self.workload_detail:
            rows = self.workload_rows()
            if rows and key in (curses.KEY_DOWN, curses.KEY_UP, curses.KEY_NPAGE, curses.KEY_PPAGE, curses.KEY_HOME, curses.KEY_END):
                index = next((i for i, row in enumerate(rows) if row["id"] == self.workload_id), -1)
                step = max(1, height - 9) if key in (curses.KEY_NPAGE, curses.KEY_PPAGE) else 1
                if key in (curses.KEY_DOWN, curses.KEY_NPAGE):
                    index = min(len(rows) - 1, index + step)
                elif key in (curses.KEY_UP, curses.KEY_PPAGE):
                    index = max(0, index - step)
                else:
                    index = 0 if key == curses.KEY_HOME else len(rows) - 1
                self.workload_id = rows[index]["id"]
                self.workload_identity = (rows[index]["id"], rows[index].get("inode"))
                self.workload_notice = ""
                line, capacity = index + 4, max(1, height - 5)
                self.scroll = min(self.scroll, line) if line < self.scroll else max(self.scroll, line - capacity + 1)
                return True
        if ord("1") <= key <= ord("9"):
            self.page, self.scroll = key - ord("1"), 0
            self.focus = None
        elif key in (9, curses.KEY_BTAB):
            self.page = (self.page + (1 if key == 9 else -1)) % len(self.names)
            self.focus = None
            self.scroll = 0
        elif key == ord("?"):
            self.help, self.scroll = not self.help, 0
        elif key == ord(" "):
            self.paused = not self.paused
        elif key == ord("/"):
            self.filter_context = "workloads" if self.page == 7 else "processes"
            self.page, self.scroll, self.editing_filter = (7 if self.page == 7 else 5), 0, True
            if self.page == 7:
                self.workload_detail = False
        elif key in map(ord, "cmnp"):
            self.sort = {ord("c"): "cpu", ord("m"): "memory", ord("n"): "name", ord("p"): "pid"}[key]
            self.scroll = 0
        elif key in (curses.KEY_DOWN, curses.KEY_NPAGE):
            self.scroll += max(1, height - 5) if key == curses.KEY_NPAGE else 1
        elif key in (curses.KEY_UP, curses.KEY_PPAGE):
            self.scroll = max(0, self.scroll - (max(1, height - 5) if key == curses.KEY_PPAGE else 1))
        elif key == curses.KEY_HOME:
            self.scroll = 0
        elif key == curses.KEY_END:
            self.scroll = 10**9
        return True


def put(screen, y, x, text, attr=0, limit=None):
    h, w = screen.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w - 1:
        return
    try:
        screen.addnstr(y, x, clean(text), max(0, min(w - x - 1, limit if limit is not None else w)), attr)
    except curses.error:
        pass  # Terminal may resize between bounds check and write.


def draw(screen, view, interval, error=None):
    screen.erase()
    h, w = screen.getmaxyx()
    view.screen_size = h, w
    view.action_regions = []
    view.application_process_regions = []
    view.application_mode_region = None
    view.browser_button = None
    view.app_regions = []
    view.app_tab_regions = []
    view.overview_links = {}
    view.overview_cards_regions = []
    view.topology_regions = {}
    view.cpu_regions = {}
    view.modal_regions = {}
    if h < 10 or w < 40:
        put(screen, 0, 0, "Resize to at least 40 x 10. q: quit")
        screen.refresh()
        return
    s = view.snapshot
    stamp = time.strftime("%H:%M:%S", time.localtime(s["timestamp"])) if s else "waiting"
    state = "REPLAY" if view.replay else "FROZEN" if view.paused else "LIVE"
    if error:
        state = "COLLECTOR ERROR"
    elif s and not view.replay and not view.paused and time.time() - s["timestamp"] > max(5, interval * 3):
        state = "STALE"
    accent = view.colors["accent"] | curses.A_BOLD
    muted = view.colors["muted"] | curses.A_DIM
    put(screen, 0, 1, f"RESOURCE LENS  {state}  {interval:g}s  |  {stamp}", accent)
    if s and w >= 95:
        put(screen, 0, 60, s["hostname"], muted)
    labels = ("Home", "CPU", "RAM", "Disk", "GPU", "Tasks", "Net", "Apps", "Time", "Status")
    view.tab_regions = []
    x = 1
    for page, label in enumerate(labels):
        text = f" {(page + 1) % 10} {label} " if w >= 105 else f" {(page + 1) % 10} "
        attr = accent | curses.A_REVERSE if page == view.page else muted
        put(screen, 1, x, text, attr)
        view.tab_regions.append((x, x + len(text), page))
        x += len(text) + 1
    if w < 105:
        put(screen, 1, x + 1, view.names[view.page], accent)
    mode = "Runtime resource controls enabled" if view.control else "Read-only"
    status = mode + " | newest samples at right"
    if not s:
        put(screen, 4, 2, "Collecting the first sample...", muted)
    elif view.profile_view:
        view.profile_view.draw(screen,w,h)
    elif view.memory_sharing:
        view.memory_sharing.draw(screen,w,h)
        status = "Memory sharing | [] tabs | arrows scroll | r refresh | Esc back"
    elif view.browser:
        view.browser.draw(screen,s,w,h)
        tree = s.get('cgroup_tree') or {}
        coverage = "PARTIAL: unreadable directories / node limit" if tree.get('truncated') or tree.get('errors') else 'visible directories'
        status = "Cgroup browser | "+coverage+" | direct PIDs; group counters include descendants"
    elif view.page == 7 and not view.help and view.application_mode == 'processes':
        status = view.draw_application_processes(screen,w,h)
    elif view.page == 7 and not view.help and w >= 100 and h >= 20:
        status = view.draw_applications(screen,w,h)
    elif view.page == 0 and not view.help and view.focus:
        kind, identity = view.focus
        summary = compact_overview(view)
        lines = summary[:summary.index('')] + [f'FOCUS {kind}: {identity} | f restore | [ / ] switch']
        lines += view.cpu_lines(w - 3) if kind == 'cpu' else resource_lines(view, kind, w - 3)
        capacity = h - 5
        view.scroll = min(view.scroll, max(0, len(lines) - capacity))
        for i, line in enumerate(lines[view.scroll:view.scroll + capacity]):
            put(screen, i + 3, 1, line, accent if i < 5 else 0)
        status = 'Focused overview | other resources summarized above'
    elif not view.help and view.page == 0 and view.overview_expanded:
        lines, links = expanded_overview(view, w - 3)
        capacity = h - 5
        view.scroll = min(view.scroll, max(0, len(lines) - capacity))
        for i, line in enumerate(lines[view.scroll:view.scroll + capacity]):
            index = view.scroll + i
            if index in links:
                view.overview_links[i + 3] = links[index]
            section_page = next((links[key] for key in sorted(links, reverse=True) if key <= index), 1)
            color = view.colors[{1: 'cpu', 2: 'memory', 3: 'disk', 4: 'gpu', 6: 'network'}.get(section_page, 'accent')]
            put(screen, i + 3, 1, line, color | (curses.A_BOLD if index in links else 0))
        status = f"Expanded overview | {view.scroll + 1}-{min(len(lines), view.scroll + capacity)} of {len(lines)} | arrows/wheel scroll"
    elif view.page == 0 and not view.help:
        layout = card_layout(h, w)
        if layout:
            first = layout[0]
            cards = overview_cards(view, first[3], first[2], view.colors)
            view.overview_cards_regions = [(*geometry, page) for geometry, page in zip(layout, (1, 2, 3, 4, 6, 5))]
            for geometry, (title, hint, color, content) in zip(layout, cards):
                draw_card(screen, put, geometry, title, hint, color, content, view.unicode)
            status = mode + " | 2-8 details | newest samples at right"
        else:
            lines = compact_overview(view)
            capacity = h - 5
            view.scroll = min(view.scroll, max(0, len(lines) - capacity))
            for i, line in enumerate(lines[view.scroll:view.scroll + capacity]):
                put(screen, i + 3, 1, line, curses.A_BOLD if i < 2 else 0)
            status = f"Overview | {view.scroll + 1}-{min(len(lines), view.scroll + capacity)} of {len(lines)} | arrows scroll"
    else:
        lines = view.lines(w - 3)
        # Put a readable multi-row chart above percentage-based detail pages.
        kinds = {2: "memory", 3: "disk", 4: "gpu"}
        if not view.help and view.page in kinds and not view.focus:
            kind = kinds[view.page]
            color = view.colors[kind]
            plot = chart(view.history[kind], max(1, w - 9), 3, unicode=view.unicode)
            lines = ["History: 0-100%, oldest left / newest right"] + [
                ("100% " if i == 0 else "  0% " if i == 2 else "     ") + row
                for i, row in enumerate(plot)] + [""] + [
                line for line in lines if not line.startswith(("History ", "Active time history "))]
        else:
            color = accent
        capacity = h - 5
        view.scroll = min(view.scroll, max(0, len(lines) - capacity))
        for i, line in enumerate(lines[view.scroll:view.scroll + capacity]):
            index = view.scroll + i
            if view.page == 5 and not view.help:
                attribute = accent | curses.A_REVERSE if index == 2 else (curses.A_BOLD if index >= 3 and index % 2 else 0)
            elif view.page == 7 and not view.workload_detail and not view.help:
                attribute = accent | curses.A_REVERSE if line.startswith(">") or index == 3 else 0
            else:
                attribute = color if index < 5 else (accent if i == 0 else 0)
            if view.page in (2, 3, 4, 6) and not view.help and line.startswith('+'):
                attribute = view.colors[{2: 'memory', 3: 'disk', 4: 'gpu', 6: 'network'}[view.page]] | curses.A_BOLD
            if view.page == 1 and not view.help:
                view.topology_regions[i + 3] = view.topology_line_regions.get(index, [])
                if line.startswith('[ All CPUs'):
                    view.cpu_regions[i + 3] = ('all', None)
            put(screen, i + 3, 1, line, attribute)
            if view.page == 1 and not view.help:
                for start, end, kind, ids in view.topology_regions.get(i + 3, []):
                    attr = view.colors['cpu']
                    if kind in ('core', 'socket'):
                        attr |= curses.A_BOLD
                    if view.overlay and kind == 'cpu' and ids[0] in (view.overlay_cpus() or set()):
                        attr |= curses.A_REVERSE
                    if view.placement and kind == 'cpu':
                        if ids[0] in view.placement['selected']:
                            attr |= curses.A_REVERSE
                        if ids[0] not in view.placement['info']['eligible']:
                            attr = muted
                        if ids[0] == view.placement['focus']:
                            attr |= curses.A_BOLD
                    put(screen, i + 3, start + 1, line[start:end], attr, end - start)

        status = f"{'Help' if view.help else view.names[view.page]} | {view.scroll + 1}-{min(len(lines), view.scroll + capacity)} of {len(lines)} | arrows scroll"
    if view.page == 0 and not view.help:
        put(screen, 2, 1, '[ - Collapse overview ] e' if view.overview_expanded else '[ + Expand overview ] e', accent)
    if view.page == 7 and not view.browser and not view.memory_sharing and not view.profile_view and not view.help and view.application_mode == 'cgroups':
        label = '[ Enter Open group ]'
        put(screen,2,1,label,accent | curses.A_REVERSE)
        view.browser_button = (1,1+len(label))
        put(screen,2,24,'T: root | [] metrics | D: details')
    if view.page == 7 and not view.browser and not view.memory_sharing and not view.profile_view and not view.help:
        label = '[ g: '+('Cgroups' if view.application_mode == 'processes' else 'Processes')+' ]'
        x = 1 if view.application_mode == 'processes' else max(1,w-len(label)-2)
        put(screen,2,x,label,accent | curses.A_REVERSE)
        view.application_mode_region = (x,x+len(label))
    if view.focus:
        status += f' | focus {view.focus[1]} (f restores)'
    if view.replay:
        status = f'REPLAY {view.replay.index + 1}/{len(view.replay.offsets)} | Left/Right step | Space play/pause | ' + status
    if view.recorder:
        status = (view.recorder.error or 'REC ' + view.recorder.path) + ' | ' + status
    if view.incident_status:
        status = view.incident_status + " | " + status
    message = error or ("; ".join(s["warnings"]) if s else "") or status
    if view.editing_filter:
        value = view.workload_filter if view.filter_context == "workloads" else view.filter
        message = f"Filter: {value}_ ({view.filter_context})  Enter: apply  Esc: clear"
    message = ("[CONTROL] " if view.control else "[Read-only] ") + message
    put(screen, h - 2, 1, message, view.colors["warning"] if error else muted)
    x = 1
    for label, key in actions(view):
        text = ' ' + label + ' '
        if x + len(text) > w - 1:
            continue
        put(screen, h - 1, x, text, accent | curses.A_REVERSE, len(text))
        view.action_regions.append((x, x + len(text), key))
        x += len(text) + 1
    if view.modal:
        content_width = max(20, min(76, w - 8))
        lines = [piece for line in view.modal_lines() for piece in (textwrap.wrap(clean(line), content_width) or [""])]
        if view.modal["stage"] in ("confirm", "hardware_confirm", "placement_confirm", "utility_disk_confirm") and len(lines) > h - 5:
            lines = ["The full target preview does not fit this terminal.",
                     "Resize larger to review and confirm, or Esc to cancel.",
                     "Enter is disabled until the complete preview is visible."]
        box_height = min(h - 2, len(lines) + 4)
        box_width = min(w - 2, content_width + 4)
        top, left = (h - box_height) // 2, (w - box_width) // 2
        for row in range(box_height):
            put(screen, top + row, left, " " * box_width, curses.A_REVERSE, box_width)
        put(screen, top, left, "+" + "-" * (box_width - 2) + "+", accent, box_width)
        put(screen, top + box_height - 1, left, "+" + "-" * (box_width - 2) + "+", accent, box_width)
        option_index = view.modal.get('offset', 0)
        for index, line in enumerate(lines[:box_height - 3]):
            if view.modal['stage'] in ('hardware_menu', 'navigate', 'focus') and (line.startswith('> ') or line.startswith('- ')):
                view.modal_regions[top + 1 + index] = (left, left + box_width, option_index)
                option_index += 1
            if view.modal['stage'] in ('hardware_confirm', 'placement_confirm', 'utility_disk_confirm') and line.startswith(('[ Apply ]', '[ Cancel ]')):
                view.modal_regions[top + 1 + index] = (left, left + box_width, 'apply' if line.startswith('[ Apply ]') else 'cancel')
            put(screen, top + 1 + index, left + 2, line, curses.A_REVERSE | (curses.A_BOLD if index == 0 else 0), content_width)
    screen.refresh()


def read_input(screen, view):
    """Apply queued keys in order, then render once; bound work to avoid starvation."""
    key = screen.getch()
    if key == -1:
        return True
    deadline = time.monotonic() + .004
    screen.timeout(0)
    try:
        for index in range(64):
            if not view.key(key, screen.getmaxyx()[0]):
                return False
            if index == 63 or key in (curses.KEY_MOUSE, curses.KEY_RESIZE) or time.monotonic() >= deadline:
                break
            key = screen.getch()
            if key == -1:
                break
            if key in (curses.KEY_MOUSE, curses.KEY_RESIZE):
                # A preceding navigation key may have changed the layout. Draw
                # its new hit regions before handling the next mouse/resize event.
                curses.ungetch(key)
                break
        return True
    finally:
        screen.timeout(100)


def run(collector, interval, ascii=False, control=False, recorder=None, replay=None, preferences=None, incidents=None, helper=None):
    latest = {"snapshot": None, "sequence": 0, "error": None}
    lock, stop = threading.Lock(), threading.Event()
    alerts = queue.SimpleQueue()

    def collect():
        while not stop.is_set():
            started = time.monotonic()
            try:
                snapshot = collector.sample()
                if stop.is_set():
                    break  # Discard an unfinished final read; do not publish after exit.
                if incidents:
                    try:
                        event = incidents.observe(snapshot)
                        if event:
                            alerts.put(event)
                    except (OSError, ValueError) as exc:
                        incidents.status = 'Incident recording failed: ' + str(exc)
                if recorder:
                    recorder.write('sample', snapshot)
                with lock:
                    latest.update(snapshot=snapshot, sequence=latest["sequence"] + 1, error=None)
            except Exception as exc:
                with lock:
                    latest["error"] = f"Collection failed: {type(exc).__name__}: {exc}"
            stop.wait(max(0.05, interval - (time.monotonic() - started)))
        if incidents:
            incidents.close()

    def terminal(screen):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        # A bare Escape otherwise commonly waits one second in ncurses.
        # Keep sequence decoding enabled for arrows/function keys/mouse over SSH.
        try:
            escape_ms = int(os.environ.get('ESCDELAY', '1'))
            curses.set_escdelay(escape_ms if escape_ms > 0 else 1)
        except (ValueError, curses.error):
            curses.set_escdelay(1)
        screen.timeout(100)  # Input wakes immediately; this only bounds idle polling.
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS)
            curses.mouseinterval(0)
        except curses.error:
            pass
        encoding = (sys.stdout.encoding or "").lower().replace("-", "")
        view, sequence = View(unicode=not ascii and encoding == "utf8", control=control and replay is None), -1
        view.proc_root = str(getattr(collector, "proc", "/proc"))
        for name,value in (preferences or {}).items():
            setattr(view, name, value)
        if helper:
            from .helper import RemoteSession
            view.session = RemoteSession(helper, 'cpu')
            view.disk_session = RemoteSession(helper, 'disk')
        view.colors = palette()
        view.recorder, view.replay = recorder, replay
        replay_position, replay_deadline = -1, time.monotonic()
        if replay:
            view.investigation.events = list(replay.events)

        previous_render = None
        while True:
            view.poll_actions()
            while not alerts.empty():
                event = alerts.get()
                view.mark_event(event["label"], event["status"], event.get("target"), event["timestamp"])
            if replay:
                desired = replay.index + view.replay_delta
                view.replay_delta = 0
                if view.replay_playing and time.monotonic() >= replay_deadline:
                    desired += 1
                    replay_deadline = time.monotonic() + interval
                desired = max(0, min(len(replay.offsets) - 1, desired))
                if desired != replay_position:
                    view.history.clear()
                    view.sample_times.clear()
                    view.investigation.samples.clear()
                    for sample in replay.window(desired):
                        view.update(sample)
                    replay_position = desired
                if desired == len(replay.offsets) - 1:
                    view.replay_playing = False
            with lock:
                current = latest.copy()
            if current["snapshot"] is not None and current["sequence"] != sequence:
                view.update(current["snapshot"])
                sequence = current["sequence"]
            view.collector_error = current['error']
            view.incident_status = incidents.status if incidents else None
            stale = bool(not replay and view.snapshot and time.time() - view.snapshot["timestamp"] > max(5, interval * 3))
            render = (id(view.snapshot), view.page, view.scroll, view.sort, view.filter,
                      view.editing_filter, view.workload_filter, view.help, view.paused, current["error"],
                      screen.getmaxyx(), stale, repr(view.modal), view.workload_id, view.workload_detail, repr(view.placement), view.overview_expanded, view.focus, repr(view.overlay), len(view.investigation.events), view.investigation.selected, view.replay_playing, view.workload_sort, view.process_tree, view.incident_status, view.application_mode, view.application_process, view.app_tab, repr(view.relation), view.hardware_selection, (view.browser.path, view.browser.inode, view.browser.selected, view.browser.process, view.browser.sort) if view.browser else None, (view.memory_sharing.tab, view.memory_sharing.scroll, id(view.memory_sharing.result), view.memory_sharing.error) if view.memory_sharing else None, (view.profile_view.scroll, id(view.profile_view.result), view.profile_view.error, view.profile_view.choosing, view.profile_view.selection, view.profile_view.details) if view.profile_view else None)
            if render != previous_render:
                draw(screen, view, interval, current["error"])
                previous_render = render
            if not read_input(screen, view):
                for job in view.profile_jobs:job.stop.set()
                break

    worker = threading.Thread(target=collect, daemon=True)
    if not replay:
        worker.start()
    try:
        curses.wrapper(terminal)
    finally:
        stop.set()
        if not replay:
            close = getattr(collector, "close", None)
            if close:
                close()
            # Collection is read-only and the worker is a daemon. A slow kernel
            # read or GPU query must not hold the terminal application open.
            # Recorder.close serializes with any write already in progress.
            # Do not wait for an unfinished read-only sample. The daemon exits
            # cooperatively; completed recording writes are still finalized.
            if incidents and not worker.is_alive():
                incidents.close()
