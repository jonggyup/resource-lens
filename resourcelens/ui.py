"""Terminal presentation, separate from Linux telemetry."""

from collections import defaultdict, deque
import curses
import queue
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


def clean(value):
    # Kernel process/device names are untrusted terminal content.
    return "".join(c if 32 <= ord(c) < 127 or c in GLYPHS else "?" for c in str(value))


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


class View(HardwareView, PlacementView):
    names = ("Overview", "CPU", "Memory", "Disks", "GPU", "Processes", "Network", "Workloads")

    def __init__(self, unicode=False, control=False, session=None):
        self.hardware_init()
        self.placement_init()
        self.overview_expanded = False
        self.overview_links = {}
        self.overview_cards_regions = []
        self.page = 0
        self.scroll = 0
        self.sort = "cpu"
        self.filter = ""
        self.workload_filter = ""
        self.filter_context = "processes"
        self.editing_filter = False
        self.paused = False
        self.help = False
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
        if self.paused:
            return
        self.snapshot = snapshot
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
            self.history["workload:" + group["id"]].append(group["cpu_pct"])

    def workload_rows(self):
        groups = self.snapshot.get("workloads", []) if self.snapshot else []
        groups = [group for group in groups if self.workload_filter.casefold() in
                  f"{group.get('unit') or ''} {group['id']}".casefold()]
        rows = sorted(groups,
                      key=lambda group: group["cpu_pct"] if group["cpu_pct"] is not None else -1, reverse=True)
        if self.workload_id not in {group["id"] for group in rows}:
            self.workload_id = rows[0]["id"] if rows else None
            self.workload_detail = False
        return rows

    def selected_workload(self):
        return next((group for group in self.workload_rows() if group["id"] == self.workload_id), None)

    def workload_lines(self, width=100):
        rows = self.workload_rows()
        group = self.selected_workload()
        mode = "CPU controls enabled: p placement | U undo placement | w weight | l quota | u Undo" if self.control else "Read-only | use --control to enable runtime CPU changes"
        if not self.workload_detail or group is None:
            name_width = max(12, min(45, width - 32))
            lines = [f"{len(rows)} workloads | / find | Enter inspect | Filter: {clean(self.workload_filter) or '(none)'}", mode,
                     "Accounting includes descendants; nested rows may overlap.",
                     f"  {'UNIT / GROUP':<{name_width}} {'CPU':>7} {'MEMORY':>10} {'PIDS':>5}"]
            for row in rows:
                name = clean(row["unit"] or row["id"])
                lines.append(f"{'>' if row['id'] == self.workload_id else ' '} {name[:name_width]:<{name_width}} "
                             f"{pct(row['cpu_pct']):>7} {size(row['memory_current']):>10} {len(row['pids']):>5}")
            if not rows:
                lines.append("No visible workload membership. cgroup v2 is required.")
            return lines
        limits = group["limits"]
        local = limits["ancestors"][0] if limits["ancestors"] else {}
        lines = [f"{clean(group['unit'] or group['id'])} | {group['kind']}",
                 f"Manager: {group['manager'] or 'unknown'} | owner UID: {group['owner_uid'] if group['owner_uid'] is not None else 'system'}",
                 mode, "Esc: workload list | runtime changes remain after exit",
                 f"CPU {pct(group['cpu_pct'])} (100% = one CPU) | memory {size(group['memory_current'])}"]
        lines.extend(textwrap.wrap("Cgroup: " + clean(group["path"] or "unattributed"), width))
        values = self.history["workload:" + group["id"]]
        maximum = max(100, max((value for value in values if value is not None), default=100))
        lines.extend([f"CPU history (0-{maximum:.0f}%): {self.trend(values, max(8, width - 25), maximum)}",
                      f"Local cpu.max: {local.get('cpu.max') or 'unavailable'} | cpu.weight: {local.get('cpu.weight') or 'unavailable'}"])
        if limits["quota_pct"] is not None:
            lines.extend(textwrap.wrap(f"Tightest readable CPU quota: {pct(limits['quota_pct'])} at {limits['quota_source']}", width))
        else:
            lines.append("CPU quota: no finite value found" if limits["quota_complete"] else "CPU quota: unavailable/inactive controller; not assumed unlimited")
        if not limits["quota_complete"]:
            lines.append("Ancestor CPU quota inspection is incomplete.")
        lines.append("Quota is a ceiling, not a guaranteed allocation.")
        lines.append('Effective CPU placement: ' + (local.get('cpuset.cpus.effective') or 'unavailable/inactive'))
        lines.append('p: select CPUs on the topology map | U: Undo placement')
        for event in self.placement_session.events[-5:]:
            if event['target']['path'] == group['path']:
                lines.append('Placement ' + (format_cpus(event['requested']) or 'inherit') + ' [' + event['status'] + ']')
        if limits["memory_max"] is not None:
            lines.extend(textwrap.wrap(f"Tightest memory.max: {size(limits['memory_max'])} at {limits['memory_source']}", width))
        else:
            lines.append("Memory ceiling: no finite value found" if limits["memory_complete"] else "Memory ceiling: unavailable")
        throttled = group["throttled_usec_per_s"]
        lines.append(f"CPU throttling: {throttled / 1000:.1f}ms/s" if throttled is not None else "CPU throttling: unavailable or awaiting sample")
        for kind, entry in group["pressure"].items():
            lines.append(f"{kind.upper()} pressure (10s): {pct(entry.get('avg10'))}")
        lines.extend(["", "Assigned visible processes (nested units are listed separately):"])
        members = {process["pid"]: process for process in self.snapshot["processes"]}
        for pid in group["pids"]:
            process = members.get(pid, {})
            lines.append(f"  {pid:<8} {clean(process.get('name', 'exited')):<25} CPU {pct(process.get('cpu_pct'))}")
        lines.extend(["", "I/O accounting by block device:"])
        for entry in group["io_devices"]:
            lines.append(f"  {entry['device']} R {size(entry['read_bps'])}/s W {size(entry['write_bps'])}/s")
        if not group["io_devices"]:
            lines.append("  No readable I/O accounting entries")
        lines.extend(["", "Local and ancestor settings (nearest first):"])
        for entry in limits["ancestors"]:
            lines.extend(textwrap.wrap(clean(entry["path"]), width))
            for name in ("cpu.max", "cpu.weight", "memory.high", "memory.max", "io.max"):
                lines.extend(textwrap.wrap(f"  {name}: {entry[name] or 'unavailable/empty'}", width))
        lines.extend(["", "CPU control events this session (latest first):"])
        for event in reversed(self.session.events[-10:]):
            before = event["before"][event["property"]]
            lines.append(f"  {time.strftime('%H:%M:%S', time.localtime(event['timestamp']))} {clean(event['target']['unit'])} "
                         f"{event['property']} {cpu_setting(event['property'], before)} -> "
                         f"{cpu_setting(event['property'], event['requested'])} [{event['status']}]")
        if not self.session.events:
            lines.append("  No changes")
        return lines

    def job(self, kind, function, *args):
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
        if modal['stage'] == 'placement_confirm':
            return self.placement_lines()
        if modal["stage"].startswith("hardware_"):
            return self.hardware_lines()
        if modal["stage"] == "busy":
            return ["Applying or checking CPU settings...", "The dashboard continues collecting data.", "Please wait for a confirmed result."]
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
                    "Enter: exit and keep settings | Esc: return | u: Undo (placement, hardware, workload)"]
        return [modal["message"], "", "Enter or Esc: close | 8: inspect workloads"]

    def modal_key(self, key):
        stage = self.modal["stage"]
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
                if any(e['status'] != 'undone' for e in self.placement_session.events):
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
                     '[ All CPUs / sockets settings ]  g menu | right-click thread',
                     'Linux topology grouping; tile positions are not chip coordinates.']
            self.topology_line_regions = {}
        tiles, hits = topology_map(data, metrics, width,
            p['selected'] if p else None, set(p['info']['eligible']) if p else None, p['focus'] if p else None)
        offset = len(lines)
        self.topology_line_regions.update({row + offset: value for row, value in hits.items()})
        lines.extend(tiles)
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
        lines = [f"Sort: {self.sort} | Filter: {clean(self.filter) or '(none)'} | {len(rows)} visible",
                 "CPU: 100% = one logical CPU; memory: resident pages (RSS).",
                 f"{'NAME':<{name_width}} {'PID':>7} {'CPU':>7} {'MEMORY':>10}" +
                 (f" {'RAM':>6} {'NICE':>4} STATE" if width >= 85 else "")]
        for p in rows:
            lines.append(f"{clean(p['name'])[:name_width]:<{name_width}} {p['pid']:>7} {pct(p['cpu_pct']):>7} {size(p['memory']):>10}" +
                         (f" {pct(p['memory_pct']):>6} {p['nice']:>4} {p['state']:>5}" if width >= 85 else ""))
        lines.append(f"Skipped/exited/unreadable processes: {self.snapshot['processes_skipped']}")
        return lines

    def lines(self, width):
        if self.help:
            return ["RESOURCE LENS - Linux resource dashboard", "",
                    "1 Overview  2 CPU  3 Memory  4 Disks  5 GPU  6 Processes  7 Network  8 Workloads",
                    "Tab / Shift-Tab: next / previous page; click a tab to select",
                    "Overview: e or Expand button toggles topology/resource panels.",
                    "Click overview cards or expanded section headings for details.", "Up/Down, PgUp/PgDn, Home/End: scroll details",
                    "Space: freeze/unfreeze display (collection continues)",
                    "c/m/n/p: process sort by CPU/memory/name/PID",
                    "/: filter processes by name or PID; Enter: finish; Esc: clear",
                    "?: toggle this help   q / Esc: quit", "",
                    "History: newest on right, up to 120 displayed samples.",
                    "CPU/memory/disk/GPU histories use 0-100%; disk/GPU: busiest device.",
                    "Network: per-interface RX/TX rates, scaled to each visible peak.",
                    "Overview charts: oldest left, newest right; fixed 0-100% axes.",
                    "Use --ascii for terminals without Unicode block graphics.",
                    "2 CPU: socket / NUMA / core tiles contain SMT thread rows.",
                    "Right-click a thread or g for hardware settings; u hardware Undo.",
                    "8 Workloads: p placement, U Undo placement; click cores to select.",
                    "S = socket, C = physical core; each CPU row is one logical CPU.",
                    "8 Workloads: Up/Down select, Enter inspect, Esc return.",
                    "With --control: w CPU weight, l CPU quota, u Undo latest.",
                    "History gaps are missing samples; all missing shows N/A.",
                    "N/A means unavailable or awaiting a second sample, never zero.",
                    "PSI = time at least some tasks waited for that resource.",
                    "Monitoring scope: visible host counters/processes, not cgroup limits.",
                    "CPU changes require --control and explicit preview confirmation.",
                    "Runtime settings survive UI exit; Undo is session-only."]
        return {1: self.cpu_lines, 2: self.memory_lines, 3: self.disk_lines,
                4: self.gpu_lines, 6: self.network_lines, 7: self.workload_lines}.get(self.page, self.process_lines)(width)

    def mouse(self, x, y, state):
        left = state & (getattr(curses, "BUTTON1_CLICKED", 0) | getattr(curses, "BUTTON1_PRESSED", 0))
        right = state & (getattr(curses, "BUTTON3_CLICKED", 0) | getattr(curses, "BUTTON3_PRESSED", 0))
        if self.modal:
            hit = self.modal_regions.get(y)
            if left and hit and hit[0] <= x < hit[1]:
                if self.modal['stage'] == 'hardware_menu':
                    self.modal['selected'] = hit[2]
                    self.hardware_choose()
                elif self.modal['stage'] == 'placement_confirm' and hit[2] == 'apply':
                    self.placement_confirm_key(10)
                elif self.modal['stage'] == 'hardware_confirm' and hit[2] == 'apply':
                    self.hardware_key(10)
                elif self.modal['stage'] in ('hardware_confirm', 'placement_confirm') and hit[2] == 'cancel':
                    self.modal = None
            return
        if self.page == 0 and not self.help and left:
            if y == 2 and 1 <= x <= 26:
                self.overview_expanded = not self.overview_expanded
                self.scroll = 0
                return
            if y in self.overview_links:
                self.page, self.scroll = self.overview_links[y], 0
                return
            for top, start, height, width, page in self.overview_cards_regions:
                if top <= y < top + height and start <= x < start + width:
                    self.page, self.scroll = page, 0
                    return
        if self.page == 1 and not self.help and (left or right):
            for start, end, kind, ids in self.topology_regions.get(y, []):
                if start <= x - 1 < end:
                    if self.placement:
                        if kind == 'action':
                            self.placement_key({'enter': 10, 'esc': 27}.get(ids[0], ord(ids[0][0])))
                        else:
                            self.placement_toggle(ids)
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
                    return
        if state & getattr(curses, "BUTTON4_PRESSED", 0):
            self.scroll = max(0, self.scroll - 3)
        elif state & getattr(curses, "BUTTON5_PRESSED", 0):
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
                self.mouse(x, y, state)
            except curses.error:
                pass
            return True
        if self.placement:
            return self.placement_key(key)
        if key in (ord("q"), 27):
            if key == 27 and self.page == 7 and self.workload_detail:
                self.workload_detail, self.scroll = False, 0
                return True
            if any(event["status"] in ("confirmed", "unconfirmed") for event in self.session.events + self.hardware.events + self.placement_session.events):
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
                index = next(i for i, row in enumerate(rows) if row["id"] == self.workload_id)
                step = max(1, height - 9) if key in (curses.KEY_NPAGE, curses.KEY_PPAGE) else 1
                if key in (curses.KEY_DOWN, curses.KEY_NPAGE):
                    index = min(len(rows) - 1, index + step)
                elif key in (curses.KEY_UP, curses.KEY_PPAGE):
                    index = max(0, index - step)
                else:
                    index = 0 if key == curses.KEY_HOME else len(rows) - 1
                self.workload_id = rows[index]["id"]
                line, capacity = index + 4, max(1, height - 5)
                self.scroll = min(self.scroll, line) if line < self.scroll else max(self.scroll, line - capacity + 1)
                return True
            if key in (10, 13) and rows:
                self.workload_detail, self.scroll = True, 0
                return True
        if ord("1") <= key <= ord("8"):
            self.page, self.scroll = key - ord("1"), 0
        elif key in (9, curses.KEY_BTAB):
            self.page = (self.page + (1 if key == 9 else -1)) % len(self.names)
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
    state = "FROZEN" if view.paused else "LIVE"
    if error:
        state = "COLLECTOR ERROR"
    elif s and not view.paused and time.time() - s["timestamp"] > max(5, interval * 3):
        state = "STALE"
    accent = view.colors["accent"] | curses.A_BOLD
    muted = view.colors["muted"] | curses.A_DIM
    put(screen, 0, 1, f"RESOURCE LENS  {state}  {interval:g}s  |  {stamp}", accent)
    if s and w >= 95:
        put(screen, 0, 60, s["hostname"], muted)
    labels = ("Home", "CPU", "RAM", "Disk", "GPU", "Tasks", "Net", "Work")
    view.tab_regions = []
    x = 1
    for page, label in enumerate(labels):
        text = f" {page + 1} {label} " if w >= 76 else f" {page + 1} "
        attr = accent | curses.A_REVERSE if page == view.page else muted
        put(screen, 1, x, text, attr)
        view.tab_regions.append((x, x + len(text), page))
        x += len(text) + 1
    if w < 76:
        put(screen, 1, x + 1, view.names[view.page], accent)
    mode = "Runtime CPU controls enabled" if view.control else "Read-only"
    status = mode + " | newest samples at right"
    if not s:
        put(screen, 4, 2, "Collecting the first sample...", muted)
    elif view.page == 0 and not view.help and view.overview_expanded:
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
        if not view.help and view.page in kinds:
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
    message = error or ("; ".join(s["warnings"]) if s else "") or status
    if view.editing_filter:
        value = view.workload_filter if view.filter_context == "workloads" else view.filter
        message = f"Filter: {value}_ ({view.filter_context})  Enter: apply  Esc: clear"
    put(screen, h - 2, 1, message, view.colors["warning"] if error else muted)
    footer = "Tab next | 1-8 views | Space freeze | / find task | ? help | q quit"
    if view.page == 0:
        footer = "e expand/collapse | click section/card for details | arrows scroll | q quit"
    if view.page == 1:
        footer = "Right-click CPU | g all/socket/core menu | u hardware Undo | q quit"
    if view.page == 7:
        footer = "Enter inspect | p placement | U undo placement | w weight | l quota | u undo"
    if view.placement:
        footer = "Arrows focus | Space toggle | a all | n none | r inherit | Enter preview | Esc"
    if w < 76:
        footer = "e expand | arrows scroll | q quit" if view.page == 0 else "Tab next | / find | ? help | q quit"
    put(screen, h - 1, 1, footer, accent)
    if view.modal:
        content_width = max(20, min(76, w - 8))
        lines = [piece for line in view.modal_lines() for piece in (textwrap.wrap(clean(line), content_width) or [""])]
        if view.modal["stage"] in ("confirm", "hardware_confirm", "placement_confirm") and len(lines) > h - 5:
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
            if view.modal['stage'] == 'hardware_menu' and (line.startswith('> ') or line.startswith('- ')):
                view.modal_regions[top + 1 + index] = (left, left + box_width, option_index)
                option_index += 1
            if view.modal['stage'] in ('hardware_confirm', 'placement_confirm') and line.startswith(('[ Apply ]', '[ Cancel ]')):
                view.modal_regions[top + 1 + index] = (left, left + box_width, 'apply' if line.startswith('[ Apply ]') else 'cancel')
            put(screen, top + 1 + index, left + 2, line, curses.A_REVERSE | (curses.A_BOLD if index == 0 else 0), content_width)
    screen.refresh()


def run(collector, interval, ascii=False, control=False):
    latest = {"snapshot": None, "sequence": 0, "error": None}
    lock, stop = threading.Lock(), threading.Event()

    def collect():
        while not stop.is_set():
            started = time.monotonic()
            try:
                snapshot = collector.sample()
                with lock:
                    latest.update(snapshot=snapshot, sequence=latest["sequence"] + 1, error=None)
            except Exception as exc:
                with lock:
                    latest["error"] = f"Collection failed: {type(exc).__name__}: {exc}"
            stop.wait(max(0.05, interval - (time.monotonic() - started)))

    def terminal(screen):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        screen.timeout(100)
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS)
            curses.mouseinterval(0)
        except curses.error:
            pass
        encoding = (sys.stdout.encoding or "").lower().replace("-", "")
        view, sequence = View(unicode=not ascii and encoding == "utf8", control=control), -1
        view.colors = palette()
        previous_render = None
        while True:
            view.poll_actions()
            with lock:
                current = latest.copy()
            if current["snapshot"] is not None and current["sequence"] != sequence:
                view.update(current["snapshot"])
                sequence = current["sequence"]
            stale = bool(view.snapshot and time.time() - view.snapshot["timestamp"] > max(5, interval * 3))
            render = (id(view.snapshot), view.page, view.scroll, view.sort, view.filter,
                      view.editing_filter, view.workload_filter, view.help, view.paused, current["error"],
                      screen.getmaxyx(), stale, repr(view.modal), view.workload_id, view.workload_detail, repr(view.placement), view.overview_expanded)
            if render != previous_render:
                draw(screen, view, interval, current["error"])
                previous_render = render
            if not view.key(screen.getch(), screen.getmaxyx()[0]):
                break

    worker = threading.Thread(target=collect, daemon=True)
    worker.start()
    try:
        curses.wrapper(terminal)
    finally:
        stop.set()
        worker.join(timeout=2)
