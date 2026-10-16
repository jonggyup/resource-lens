"""Terminal presentation, separate from Linux telemetry."""

from collections import defaultdict, deque
import curses
import threading
import time


def clean(value):
    # Kernel process/device names are untrusted terminal content.
    return "".join(c if 32 <= ord(c) < 127 else "?" for c in str(value))


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


def bar(value, width=22):
    if value is None:
        return "[" + " no data ".center(width, ".") + "] N/A"
    fill = round(max(0, min(100, value)) / 100 * width)
    return "[" + "#" * fill + "." * (width - fill) + "] " + pct(value)


def spark(values, width=40, maximum=100):
    values = list(values)[-width:]
    if not values or all(v is None for v in values):
        return "N/A (no measured data)"[:width]
    levels = "._-:=+*#%@"
    return "".join(" " if v is None else levels[min(len(levels) - 1, max(0,
                    int(v / max(1, maximum) * (len(levels) - 1))))] for v in values)


def pressure(snapshot, kind):
    value = snapshot["pressure"].get(kind, {}).get("some", {}).get("avg10")
    return f"{kind.upper()} pressure (10s): {pct(value)}"


class View:
    names = ("Overview", "CPU", "Memory", "Disks", "GPU", "Processes", "Network")

    def __init__(self):
        self.page = 0
        self.scroll = 0
        self.sort = "cpu"
        self.filter = ""
        self.editing_filter = False
        self.paused = False
        self.help = False
        self.history = defaultdict(lambda: deque(maxlen=120))
        self.snapshot = None

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

    def cpu_lines(self, width=60):
        s = self.snapshot
        aggregate = next((c for c in s["cpu"] if c["id"] == "cpu"), {})
        lines = [clean(s["cpu_model"]), bar(aggregate.get("busy_pct"), max(8, min(30, width - 13))),
                 "History " + spark(self.history["cpu"], max(8, width - 10)),
                 "Load " + " / ".join(f"{v:.2f}" if v is not None else "N/A" for v in s["load"]),
                 pressure(s, "cpu"),
                 f"I/O wait {pct(aggregate.get('iowait_pct'))}  Steal {pct(aggregate.get('steal_pct'))}"]
        for c in s["cpu"]:
            if c["id"] != "cpu":
                freq = f"{c['frequency_mhz']:.0f}MHz" if c["frequency_mhz"] is not None else "freq N/A"
                lines.append(f"{c['id']:<7} {bar(c['busy_pct'], max(8, min(20, width - 29)))}  {freq}")
        for sensor in s["temperatures"]:
            lines.append(f"{clean(sensor['name'])}: {sensor['temperature_c']:.1f} C")
        return lines

    def memory_lines(self, width=60):
        m = self.snapshot["memory"]
        lines = [bar(ratio(m["used"], m["total"]), max(8, min(30, width - 13))),
                 f"Used {size(m['used'])} / {size(m['total'])}",
                 f"Available {size(m['available'])}",
                 f"Cache {size(m['cache'])}  Buffers {size(m['buffers'])}",
                 f"Swap {size(m['swap_used'])} / {size(m['swap_total'])}",
                 pressure(self.snapshot, "memory"),
                 "History " + spark(self.history["memory"], max(8, width - 10)),
                 "Used = total - available; reclaimable cache is useful."]
        return lines

    def disk_lines(self, width=60):
        lines = [pressure(self.snapshot, "io"), "Active time history " + spark(self.history["disk"], max(8, width - 22))]
        fs = self.snapshot["root_filesystem"]
        if fs:
            lines.append(f"/ capacity: {size(fs['used'])} / {size(fs['total'])} ({pct(ratio(fs['used'], fs['total']))})")
        for d in self.snapshot["disks"]:
            lines.append(f"{clean(d['name'])}  R {size(d['read_bps'])}/s  W {size(d['write_bps'])}/s")
            lines.append(f"  Active {pct(d['active_pct'])}  In flight {d['in_flight']}  Avg queue " +
                         (f"{d['queue_depth']:.2f}" if d['queue_depth'] is not None else "N/A"))
            if self.page == 3:
                lines.append(f"  {clean(d['model'])} | read/write IOPS " +
                             "/".join("N/A" if d[k] is None else f"{d[k]:.1f}"
                                      for k in ("read_iops", "write_iops")))
        if not self.snapshot["disks"]:
            lines.append("No readable block devices (loop/ram excluded).")
        if self.page == 3:
            lines.extend(["", "Per-device counters; stacked devices may count the same I/O.",
                          "Active time is NOT a saturation score, especially for NVMe.",
                          "Throughput measures block I/O, not cached application reads."])
        return lines

    def gpu_lines(self, width=60):
        lines = []
        for g in self.snapshot["gpus"]:
            lines.extend([f"{clean(g['id'])}: {clean(g['name'])} ({clean(g['driver'])})",
                          bar(g["busy_pct"], max(8, min(30, width - 13))),
                          f"VRAM {size(g['memory_used'])} / {size(g['memory_total'])}",
                          "Temp " + (f"{g['temperature_c']:.1f}C" if g['temperature_c'] is not None else "N/A") +
                          "  Power " + (f"{g['power_w']:.1f}W" if g['power_w'] is not None else "N/A"),
                          clean(g["status"])])
        if not lines:
            lines = ["No GPUs detected by DRM or nvidia-smi.", "GPU usage: N/A (not zero)."]
        lines.append("History " + spark(self.history["gpu"], max(8, width - 10)))
        if self.page == 4:
            lines.extend(["", "AMD: available sysfs busy/VRAM/hwmon counters.",
                          "NVIDIA: nvidia-smi, when installed and accessible.",
                          "Intel/other: detected; busy may be unavailable.",
                          "Integrated/shared memory is not total system RAM."])
        return lines

    def network_lines(self, width=60):
        entries = self.snapshot["network"]
        others = 0
        if self.page == 0:
            entries = [entry for entry in entries if entry["name"] != "lo"] or entries
            entries = sorted(entries, key=lambda entry: (
                entry["state"] == "up", (entry["rx_bps"] or 0) + (entry["tx_bps"] or 0)), reverse=True)
            others = len(self.snapshot["network"]) - min(1, len(entries))
            entries = entries[:1]
        lines = []
        for entry in entries:
            lines.append(f"{clean(entry['name'])} ({clean(entry['state'])})  "
                         f"RX {size(entry['rx_bps'])}/s  TX {size(entry['tx_bps'])}/s")
            if self.page == 6:
                lines.append(f"  Totals RX {size(entry['rx_bytes'])} / TX {size(entry['tx_bytes'])}")
                speed = f"{entry['speed_mbps']:g} Mb/s" if entry["speed_mbps"] else "N/A"
                packets = "/".join("N/A" if entry[k] is None else f"{entry[k]:.1f}"
                                   for k in ("rx_pps", "tx_pps"))
                lines.append(f"  Link speed {speed} | RX/TX packets/s {packets}")
                lines.append(f"  Errors RX/TX {entry['rx_errors']}/{entry['tx_errors']} | "
                             f"Drops RX/TX {entry['rx_dropped']}/{entry['tx_dropped']}")
            for direction in ("rx", "tx"):
                values = self.history[(entry["name"], entry["ifindex"], direction)]
                visible = list(values)[-max(8, width - 30):]
                peak = max((v for v in visible if v is not None), default=None)
                lines.append(f"  {direction.upper()} history {spark(visible, max(8, width - 30), peak or 1)}"
                             f" peak {size(peak)}/s")
            if self.page == 6:
                lines.append("")
        if not entries:
            lines.append("Network: N/A (no readable interface counters)")
        elif self.page == 0:
            lines.append(f"{others} other interfaces | 7 Network: all interfaces and totals")
        if self.page == 6:
            lines.extend(["History scale: each direction's visible peak, NOT link utilization.",
                          "Totals/errors/drops: since interface counter initialization.",
                          "Includes loopback and virtual interfaces; never sum layered traffic.",
                          "Interface counters only; no per-process or Internet-only attribution."])
        return lines

    def process_lines(self):
        rows = [p for p in self.snapshot["processes"] if self.filter.casefold() in
                f"{p['pid']} {p['name']}".casefold()]
        keys = {"cpu": "cpu_pct", "memory": "memory", "pid": "pid", "name": "name"}
        key = keys[self.sort]
        rows.sort(key=lambda p: p[key] if p[key] is not None else -1,
                  reverse=self.sort in ("cpu", "memory"))
        lines = [f"Sort: {self.sort} | Filter: {clean(self.filter) or '(none)'} | {len(rows)} visible",
                 "CPU: 100% = one logical CPU; memory: resident pages (RSS).",
                 f"{'PID':>8} {'CPU%':>8} {'RSS':>11} {'RAM%':>7} {'NI':>4} {'S':>2}  NAME"]
        for p in rows:
            lines.append(f"{p['pid']:>8} {pct(p['cpu_pct']):>8} {size(p['memory']):>11} "
                         f"{pct(p['memory_pct']):>7} {p['nice']:>4} {p['state']:>2}  {clean(p['name'])}")
        lines.append(f"Skipped/exited/unreadable processes: {self.snapshot['processes_skipped']}")
        return lines

    def lines(self, width):
        if self.help:
            return ["RESOURCE LENS - read-only Linux dashboard", "",
                    "1 Overview  2 CPU  3 Memory  4 Disks  5 GPU  6 Processes  7 Network",
                    "Tab / Shift-Tab: next / previous page", "Up/Down, PgUp/PgDn, Home/End: scroll details",
                    "Space: freeze/unfreeze display (collection continues)",
                    "c/m/n/p: process sort by CPU/memory/name/PID",
                    "/: filter processes by name or PID; Enter: finish; Esc: clear",
                    "?: toggle this help   q / Esc: quit", "",
                    "History: newest on right, up to 120 displayed samples.",
                    "CPU/memory/disk/GPU histories use 0-100%; disk/GPU: busiest device.",
                    "Network: per-interface RX/TX rates, scaled to each visible peak.",
                    "History gaps are missing samples; all missing shows N/A.",
                    "N/A means unavailable or awaiting a second sample, never zero.",
                    "PSI = time at least some tasks waited for that resource.",
                    "Monitoring scope: visible host counters/processes, not cgroup limits.",
                    "No frequency, affinity, priority or resource settings are changed."]
        return {1: self.cpu_lines, 2: self.memory_lines, 3: self.disk_lines,
                4: self.gpu_lines, 6: self.network_lines}.get(self.page, lambda w: self.process_lines())(width)

    def key(self, key, height=24):
        if self.editing_filter:
            if key in (10, 13):
                self.editing_filter = False
            elif key == 27:
                self.filter, self.editing_filter = "", False
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.filter = self.filter[:-1]
            elif 32 <= key < 127 and len(self.filter) < 80:
                self.filter += chr(key)
            self.scroll = 0
            return True
        if key in (ord("q"), 27):
            return False
        if ord("1") <= key <= ord("7"):
            self.page, self.scroll = key - ord("1"), 0
        elif key in (9, curses.KEY_BTAB):
            self.page = (self.page + (1 if key == 9 else -1)) % len(self.names)
            self.scroll = 0
        elif key == ord("?"):
            self.help, self.scroll = not self.help, 0
        elif key == ord(" "):
            self.paused = not self.paused
        elif key == ord("/"):
            self.page, self.scroll, self.editing_filter = 5, 0, True
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
    accent = curses.color_pair(1) | curses.A_BOLD
    put(screen, 0, 0, f" RESOURCE LENS | {state} | {s['hostname'] if s else ''} | {stamp} | read-only", accent)
    put(screen, 1, 0, "  ".join(f"{'>' if i == view.page else ' '}{i+1} {name}" for i, name in enumerate(view.names)))
    if not s:
        put(screen, 3, 2, "Collecting first sample...")
    elif view.page == 0 and not view.help and w >= 90 and h >= 20:
        network_height = 6 if h >= 24 else 4
        panel_width, panel_height = (w - 3) // 2, (h - 5 - network_height) // 2
        for i, (title, lines) in enumerate((("CPU", view.cpu_lines(panel_width)),
                                          ("MEMORY", view.memory_lines(panel_width)),
                                          ("GPU", view.gpu_lines(panel_width)),
                                          ("DISKS", view.disk_lines(panel_width)))):
            y, x = 3 + (i // 2) * panel_height, 1 + (i % 2) * (panel_width + 1)
            put(screen, y, x, f" {title} " + "-" * panel_width, accent, panel_width)
            for j, line in enumerate(lines[:max(0, panel_height - 1)]):
                put(screen, y + j + 1, x, line, limit=panel_width)
        y = 3 + panel_height * 2
        put(screen, y, 1, " NETWORK " + "-" * w, accent)
        for j, line in enumerate(view.network_lines(w - 3)[:network_height - 1]):
            put(screen, y + j + 1, 1, line)
    else:
        if view.page == 0 and not view.help:
            lines = []
            for title, values in (("CPU", view.cpu_lines(w)), ("MEMORY", view.memory_lines(w)),
                                  ("GPU", view.gpu_lines(w)), ("DISKS", view.disk_lines(w)),
                                  ("NETWORK", view.network_lines(w))):
                lines.extend([f"-- {title} --"] + values + [""])
        else:
            lines = view.lines(w - 3)
        capacity = h - 5
        view.scroll = min(view.scroll, max(0, len(lines) - capacity))
        for i, line in enumerate(lines[view.scroll:view.scroll + capacity]):
            put(screen, i + 3, 1, line)
    message = error or ("; ".join(s["warnings"]) if s else "")
    if view.editing_filter:
        message = f"Filter: {view.filter}_  Enter: finish  Esc: clear"
    put(screen, h - 2, 0, message, curses.A_BOLD)
    put(screen, h - 1, 0, "q quit | ? help | 1-7 views | Space freeze | arrows scroll | / filter | c/m/n/p sort", accent)
    screen.refresh()


def run(collector, interval):
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
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
        screen.timeout(100)
        view, sequence = View(), -1
        while True:
            with lock:
                current = latest.copy()
            if current["snapshot"] is not None and current["sequence"] != sequence:
                view.update(current["snapshot"])
                sequence = current["sequence"]
            draw(screen, view, interval, current["error"])
            if not view.key(screen.getch(), screen.getmaxyx()[0]):
                break

    worker = threading.Thread(target=collect, daemon=True)
    worker.start()
    try:
        curses.wrapper(terminal)
    finally:
        stop.set()
        worker.join(timeout=2)
