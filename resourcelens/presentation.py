"""Responsive overview cards and terminal charts. No telemetry collection."""

import curses


GLYPHS = frozenset("─│┌┐└┘▁▂▃▄▅▆▇█░·")


def chart(values, width, height=3, maximum=100, unicode=True):
    """Fixed-width, oldest-to-newest chart. Missing points remain gaps."""
    if width < 1 or height < 1:
        return []
    values = list(values)[-width:]
    if not values or all(value is None for value in values):
        rows = [" " * width for _ in range(height)]
        rows[height // 2] = "No measured data"[:width].center(width)
        return rows
    columns = []
    for value in values:
        column = []
        units = max(0, min(1, (value or 0) / max(1, maximum))) * height
        for row in range(height):
            occupied = units - (height - row - 1)
            if value is None:
                glyph = " "
            elif height == 1 and not unicode:
                glyph = "._-:=+*#%@"[min(9, int(units * 9))]
            elif occupied <= 0:
                glyph = ("·" if unicode else ".") if row == height - 1 else " "
            elif unicode:
                glyph = "▁▂▃▄▅▆▇█"[min(7, int(occupied * 8) - 1) if occupied >= 0.125 else 0]
            else:
                glyph = "#"
            column.append(glyph)
        columns.append(column)
    padding = " " * (width - len(columns))
    return [padding + "".join(column[row] for column in columns) for row in range(height)]


def card_layout(height, width):
    """Return page-space cards: (y, x, h, w), or an empty compact layout."""
    if width < 76 or height < 23:
        return []
    body = height - 5
    top = body // 3
    left = (width - 3) // 2
    return [(3 + row * top, 1 + col * (left + 1),
             top if row < 2 else body - top * 2, left)
            for row in range(3) for col in range(2)]


def palette():
    result = {key: 0 for key in ("accent", "cpu", "memory", "disk", "gpu", "network", "muted", "warning")}
    if curses.has_colors():
        try:
            curses.start_color()
            curses.use_default_colors()
            colors = (curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_BLUE,
                      curses.COLOR_YELLOW, curses.COLOR_MAGENTA, curses.COLOR_CYAN,
                      curses.COLOR_WHITE, curses.COLOR_YELLOW)
            for pair, (key, color) in enumerate(zip(result, colors), 1):
                curses.init_pair(pair, color, -1)
                result[key] = curses.color_pair(pair)
        except curses.error:
            pass  # Monochrome/limited terminals retain the same labels and layout.
    return result


def draw_card(screen, put, geometry, title, hint, color, lines, unicode):
    y, x, height, width = geometry
    chars = ("┌", "┐", "└", "┘", "─", "│") if unicode else ("+", "+", "+", "+", "-", "|")
    tl, tr, bl, br, horizontal, vertical = chars
    label = f" {title[:max(1, width - len(hint) - 7)]} "
    put(screen, y, x, tl + label + horizontal * max(0, width - len(label) - 2) + tr, color, width)
    for row in range(1, height - 1):
        put(screen, y + row, x, vertical, color)
        put(screen, y + row, x + width - 1, vertical, color)
    put(screen, y + height - 1, x, bl + horizontal * (width - 2) + br, color, width)
    content_width = max(1, width - 4)
    for row, (text, attribute) in enumerate(lines[:max(0, height - 2)]):
        put(screen, y + 1 + row, x + 2, text, attribute, content_width)
    # Hint remains visible without covering a card's contents.
    put(screen, y, x + max(len(label) + 2, width - len(hint) - 3), hint, color, len(hint))


def overview_cards(view, width, height, colors):
    """Six cards with bounded content; short cards retain headline and history."""
    from .ui import clean, pct, pressure, ratio, size

    s = view.snapshot
    strong = curses.A_BOLD
    muted = colors["muted"] | curses.A_DIM
    limit = max(1, width - 4)
    rows = max(1, height - 2)

    def trend(kind, color, subtitle, current, footnote):
        values = view.history[kind]
        content = [(current, strong | color)]
        if rows >= 5:
            content.append((subtitle, muted))
        graph_height = max(1, rows - len(content) - 2)
        graph_width = max(1, limit - 5)
        for i, line in enumerate(chart(values, graph_width, graph_height, unicode=view.unicode)):
            axis = ("100% " if i == 0 else "  0% " if i == graph_height - 1 else "     ") if graph_height > 1 else "     "
            content.append((axis + line, color))
        content.extend([(f"0-100% | {min(len(values), graph_width)} samples | latest >", muted),
                        (footnote, muted)])
        return content[:rows]

    cpu = next((c for c in s["cpu"] if c["id"] == "cpu"), {})
    cpu_value = cpu.get("busy_pct")
    cpu_head = f"{pct(cpu_value)} busy  |  {max(0, len(s['cpu']) - 1)} logical CPUs"
    temps = s["temperatures"]
    cpu_subtitle = f"Wait {pct(cpu.get('iowait_pct'))}  Steal {pct(cpu.get('steal_pct'))}"
    if temps:
        cpu_subtitle += f"  {max(t['temperature_c'] for t in temps):.0f}C"
    cpu_lines = trend("cpu", colors["cpu"], cpu_subtitle, cpu_head, pressure(s, "cpu"))

    m = s["memory"]
    memory_lines = trend("memory", colors["memory"], f"{size(m['available'])} available | swap {size(m['swap_used'])}",
                         f"{pct(ratio(m['used'], m['total']))}  {size(m['used'])} / {size(m['total'])}",
                         pressure(s, "memory"))

    disk = max(s["disks"], key=lambda d: (d["read_bps"] or 0) + (d["write_bps"] or 0), default=None)
    disk_head = (f"Read {size(disk['read_bps'])}/s  Write {size(disk['write_bps'])}/s"
                 if disk else "No readable disk counters")
    disk_subtitle = (f"Rates: {clean(disk['name'])} | history: highest active time" if disk else "No devices")
    disk_lines = trend("disk", colors["disk"], disk_subtitle, disk_head,
                       pressure(s, "io"))

    gpu = max(s["gpus"], key=lambda g: g["busy_pct"] if g["busy_pct"] is not None else -1, default=None)
    if gpu and gpu["busy_pct"] is not None:
        gpu_lines = trend("gpu", colors["gpu"], clean(gpu["name"]),
                          f"{pct(gpu['busy_pct'])} busy | VRAM {size(gpu['memory_used'])}",
                          "History: busiest GPU")
    else:
        gpu_lines = [("Utilization unavailable", strong),
                     (clean(gpu["name"]) if gpu else "No GPU detected", muted),
                     ("This driver has no readable usage data.", muted),
                     ("Unavailable does not mean idle.", muted)]
        if gpu:
            gpu_lines[2] = (clean(gpu["status"]), muted)

    network = view.primary_network()
    net_lines = []
    if network:
        net_lines.append((f"Receive {size(network['rx_bps'])}/s  Send {size(network['tx_bps'])}/s", strong | colors["network"]))
        if rows >= 5:
            net_lines.append((f"{clean(network['name'])} | {clean(network['state'])} | per-interface traffic", muted))
        for direction in ("rx", "tx"):
            values = view.history[(network["name"], network["ifindex"], direction)]
            visible = list(values)[-max(1, limit - 4):]
            peak = max((v for v in visible if v is not None), default=None)
            net_lines.append((direction.upper() + "  " + chart(visible, max(1, limit - 4), 1,
                                                             maximum=peak or 1, unicode=view.unicode)[0], colors["network"]))
        if rows >= 4:
            # Peak captions match each visible chart window rather than all retained samples.
            rx_values = list(view.history[(network["name"], network["ifindex"], "rx")])[-max(1, limit - 4):]
            tx_values = list(view.history[(network["name"], network["ifindex"], "tx")])[-max(1, limit - 4):]
            rx_peak = max((v for v in rx_values if v is not None), default=None)
            tx_peak = max((v for v in tx_values if v is not None), default=None)
            net_lines.append((f"Peaks RX {size(rx_peak)}/s  TX {size(tx_peak)}/s", muted))
        if rows >= 6:
            net_lines.append(("Charts scale independently to visible peaks.", muted))
    else:
        net_lines = [("No readable interface counters", strong), ("Network usage is unavailable.", muted)]

    top = sorted(s["processes"], key=lambda p: p["cpu_pct"] if p["cpu_pct"] is not None else -1, reverse=True)
    name_width = max(8, min(17, limit - 19))
    process_lines = [(f"{'NAME':<{name_width}} {'CPU':>7} {'MEMORY':>10}", muted)]
    for process in top[:max(0, rows - 2)]:
        process_lines.append((f"{clean(process['name'])[:name_width]:<{name_width}} {pct(process['cpu_pct']):>7} {size(process['memory']):>10}", strong))
    process_lines.append((f"{len(top)} visible processes | 100% = one CPU", muted))
    return [("CPU", "2 details", colors["cpu"], cpu_lines),
            ("MEMORY", "3 details", colors["memory"], memory_lines),
            ("DISKS", "4 details", colors["disk"], disk_lines),
            ("GPU", "5 details", colors["gpu"], gpu_lines),
            ("NETWORK " + (clean(network["name"]) if network else ""), "7 details", colors["network"], net_lines),
            ("TOP PROCESSES", "6 details", colors["accent"], process_lines)]


def compact_overview(view):
    """An overview that remains useful at 40x10; scrolling shows more detail."""
    from .ui import clean, pct, ratio, size

    s = view.snapshot
    cpu = next((c for c in s["cpu"] if c["id"] == "cpu"), {})
    m = s["memory"]
    lines = [f"CPU     {pct(cpu.get('busy_pct'))} busy | {max(0, len(s['cpu']) - 1)} logical CPUs",
             f"Memory  {pct(ratio(m['used'], m['total']))} | {size(m['used'])}/{size(m['total'])}"]
    gpu = max((g["busy_pct"] for g in s["gpus"] if g["busy_pct"] is not None), default=None)
    lines.append("GPU     " + (pct(gpu) if gpu is not None else "Usage unavailable"))
    for d in s["disks"]:
        lines.append(f"Disk {clean(d['name'])}  R {size(d['read_bps'])}/s W {size(d['write_bps'])}/s")
    net = view.primary_network()
    if net:
        lines.append(f"Net {clean(net['name'])} RX {size(net['rx_bps'])}/s TX {size(net['tx_bps'])}/s")
    else:
        lines.append("Network unavailable")
    lines.extend(["", "1-8: details | Tab: next view", "Larger terminal: charts + top processes"])
    return lines
