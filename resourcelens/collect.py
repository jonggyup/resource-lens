"""Read Linux counters. Rates use monotonic elapsed time, never refresh assumptions."""

import csv
import io
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from .hardware import Hardware
from .resource_topology import topology


def read(path):
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return ""


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def numeric_file(path, scale=1):
    value = number(read(path))
    return value / scale if value is not None else None


def fields(text):
    result = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            parts = value.split()
            n = number(parts[0]) if parts else None
            if n is not None:
                result[key] = n * (1024 if len(parts) > 1 and parts[1] == "kB" else 1)
    return result


def delta(current, previous, elapsed):
    if previous is None or elapsed <= 0 or current < previous:
        return None
    return (current - previous) / elapsed


def parse_nvidia(text):
    result = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) != 8:
            continue
        name, bus, busy, used, total, temp, watts, clock = (x.strip() for x in row)
        result.append({"id": bus, "name": name, "driver": "nvidia",
                       "busy_pct": number(busy),
                       "memory_used": number(used) * 1024**2 if number(used) is not None else None,
                       "memory_total": number(total) * 1024**2 if number(total) is not None else None,
                       "temperature_c": number(temp), "power_w": number(watts),
                       "clock_mhz": number(clock), "status": "nvidia-smi"})
    return result


class Collector:
    def __init__(self, proc="/proc", sys="/sys", nvidia=True, clock=time.monotonic):
        self.proc, self.sys = Path(proc), Path(sys)
        self.clock = clock
        self.last_time = None
        self.last_cpu, self.last_disk, self.last_process = {}, {}, {}
        self.last_network = {}
        from .workloads import WorkloadCollector
        self.workloads = WorkloadCollector(self.sys / "fs/cgroup")
        self.ticks = os.sysconf("SC_CLK_TCK")
        self.pages = os.sysconf("SC_PAGE_SIZE")
        self.nvidia = shutil.which("nvidia-smi") if nvidia else None
        self.model = next((line.split(":", 1)[1].strip() for line in
                           read(self.proc / "cpuinfo").splitlines()
                           if line.startswith("model name")), "Unknown CPU")

    def cpus(self):
        counters = {}
        for line in read(self.proc / "stat").splitlines():
            parts = line.split()
            if parts and re.fullmatch(r"cpu\d*", parts[0]):
                try:
                    # guest and guest_nice are already included in user/nice.
                    values = [int(v) for v in parts[1:9]]
                    counters[parts[0]] = values + [0] * (8 - len(values))
                except ValueError:
                    continue
        result = []
        for name, current in counters.items():
            previous = self.last_cpu.get(name)
            busy = wait = steal = None
            if previous is not None and all(a >= b for a, b in zip(current, previous)):
                diff = [a - b for a, b in zip(current, previous)]
                total = sum(diff)
                if total:
                    busy = 100 * (total - diff[3] - diff[4]) / total
                    wait, steal = 100 * diff[4] / total, 100 * diff[7] / total
            frequency = None
            if name != "cpu":
                frequency = numeric_file(self.sys / "devices/system/cpu" / name /
                                         "cpufreq/scaling_cur_freq", 1000)
            result.append({"id": name, "busy_pct": busy, "iowait_pct": wait,
                           "steal_pct": steal, "frequency_mhz": frequency})
        self.last_cpu = counters
        return result

    def memory(self):
        values = fields(read(self.proc / "meminfo"))
        total, available = values.get("MemTotal"), values.get("MemAvailable")
        swap_total, swap_free = values.get("SwapTotal"), values.get("SwapFree")
        return {"total": total, "available": available,
                "used": max(0, total - available) if total is not None and available is not None else None,
                "cache": max(0, values.get("Cached", 0) + values.get("SReclaimable", 0)
                             - values.get("Shmem", 0)) if values else None,
                "buffers": values.get("Buffers"), "swap_total": swap_total,
                "swap_used": max(0, swap_total - swap_free)
                if swap_total is not None and swap_free is not None else None}

    def pressure(self):
        result = {}
        for kind in ("cpu", "memory", "io"):
            entry = {}
            for line in read(self.proc / "pressure" / kind).splitlines():
                parts = line.split()
                if parts:
                    entry[parts[0]] = {k: number(v) for k, sep, v in
                                      (part.partition("=") for part in parts[1:]) if sep}
            result[kind] = entry
        return result

    def disks(self, elapsed):
        devices = {p.name for p in (self.sys / "block").glob("*")
                   if not p.name.startswith(("loop", "ram"))}
        counters, result = {}, []
        for line in read(self.proc / "diskstats").splitlines():
            parts = line.split()
            if len(parts) < 14 or parts[2] not in devices:
                continue
            try:
                v = [int(x) for x in parts[3:]]
            except ValueError:
                continue
            name = parts[2]
            counters[name] = v
            previous = self.last_disk.get(name)
            def rate(index, scale=1):
                value = delta(v[index], previous[index] if previous else None, elapsed)
                return value * scale if value is not None else None
            active = rate(9, 0.1)
            result.append({"name": name, "read_bps": rate(2, 512), "write_bps": rate(6, 512),
                           "read_iops": rate(0), "write_iops": rate(4),
                           "active_pct": min(100, active) if active is not None else None,
                           "queue_depth": rate(10, 0.001), "in_flight": v[8],
                           "model": read(self.sys / "block" / name / "device/model") or name})
        self.last_disk = counters
        return result

    def network(self, elapsed):
        counters, result = {}, []
        for line in read(self.proc / "net/dev").splitlines():
            name, sep, raw = line.rpartition(":")
            if not sep:
                continue
            name = name.strip()
            try:
                values = [int(x) for x in raw.split()]
            except ValueError:
                continue
            if len(values) < 16:
                continue
            dev = self.sys / "class/net" / name
            ifindex = numeric_file(dev / "ifindex")
            identity = (name, ifindex)
            previous = self.last_network.get(identity)
            counters[identity] = values
            def rate(index):
                return delta(values[index], previous[index] if previous else None, elapsed)
            speed = numeric_file(dev / "speed")
            result.append({"name": name, "ifindex": ifindex,
                           "state": read(dev / "operstate") or "unavailable",
                           "speed_mbps": speed if speed is not None and speed > 0 else None,
                           "rx_bps": rate(0), "tx_bps": rate(8),
                           "rx_pps": rate(1), "tx_pps": rate(9),
                           "rx_bytes": values[0], "tx_bytes": values[8],
                           "rx_errors": values[2], "tx_errors": values[10],
                           "rx_dropped": values[3], "tx_dropped": values[11]})
        self.last_network = counters
        return sorted(result, key=lambda entry: entry["name"])

    def processes(self, elapsed, total_memory):
        from .workloads import membership
        counters, result = {}, []
        skipped = 0
        for path in self.proc.glob("[0-9]*"):
            try:
                text = (path / "stat").read_text()
                end = text.rindex(")")
                name = text[text.index("(") + 1:end]
                v = text[end + 2:].split()
                # Remaining fields start at stat field 3 (state).
                identity = (int(path.name), int(v[19]))
                ticks = int(v[11]) + int(v[12])
                resident = max(0, int(v[21])) * self.pages
                counters[identity] = ticks
                cpu = delta(ticks, self.last_process.get(identity), elapsed)
                result.append({"pid": identity[0], "name": name, "state": v[0],
                               "nice": int(v[16]), "memory": resident,
                               "cpu_pct": cpu / self.ticks * 100 if cpu is not None else None,
                               "memory_pct": 100 * resident / total_memory if total_memory else None,
                               "cgroup": membership(read(path / "cgroup"))})
            except (OSError, ValueError, IndexError):
                skipped += 1
        self.last_process = counters
        return result, skipped

    def temperatures(self):
        result = []
        for hw in (self.sys / "class/hwmon").glob("hwmon*"):
            driver = read(hw / "name")
            if driver not in ("coretemp", "k10temp", "zenpower", "cpu_thermal"):
                continue
            for sensor in hw.glob("temp*_input"):
                value = numeric_file(sensor, 1000)
                if value is not None:
                    result.append({"name": read(sensor.with_name(sensor.name.replace("_input", "_label")))
                                   or f"{driver}/{sensor.stem}", "temperature_c": value})
        return result

    def gpus(self):
        result = []
        nvidia_cards = []
        for card in sorted((self.sys / "class/drm").glob("card[0-9]*")):
            if not re.fullmatch(r"card\d+", card.name):
                continue
            dev = card / "device"
            driver = (dev / "driver").resolve().name if (dev / "driver").exists() else "unknown"
            if driver == "nvidia":
                nvidia_cards.append(card.name)
                continue
            vendor = read(dev / "vendor")
            label = {"0x1002": "AMD", "0x8086": "Intel", "0x102b": "Matrox"}.get(vendor, vendor)
            sensor = next(iter((dev / "hwmon").glob("hwmon*")), None)
            busy = numeric_file(dev / "gpu_busy_percent")
            result.append({"id": card.name, "name": f"{label or 'GPU'} {read(dev / 'device')}",
                           "driver": driver, "busy_pct": busy,
                           "memory_used": numeric_file(dev / "mem_info_vram_used"),
                           "memory_total": numeric_file(dev / "mem_info_vram_total"),
                           "temperature_c": numeric_file(sensor / "temp1_input", 1000) if sensor else None,
                           "power_w": numeric_file(sensor / "power1_average", 1e6) if sensor else None,
                           "clock_mhz": None,
                           "status": "sysfs" if busy is not None else "driver exposes no GPU utilization counter"})
        error = "nvidia-smi not installed or disabled"
        if self.nvidia:
            try:
                completed = subprocess.run([self.nvidia,
                    "--query-gpu=name,pci.bus_id,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,clocks.gr",
                    "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=0.8,
                    env={**os.environ, "LC_ALL": "C"})
                parsed = parse_nvidia(completed.stdout) if completed.returncode == 0 else []
                if parsed:
                    result.extend(parsed)
                    nvidia_cards = []
                error = "nvidia-smi query failed or returned no devices"
            except (OSError, subprocess.TimeoutExpired):
                error = "nvidia-smi unavailable or timed out"
        for card in nvidia_cards:
            result.append({"id": card, "name": "NVIDIA", "driver": "nvidia", "busy_pct": None,
                           "memory_used": None, "memory_total": None, "temperature_c": None,
                           "power_w": None, "clock_mhz": None, "status": error})
        return result

    def sample(self):
        now = self.clock()
        elapsed = now - self.last_time if self.last_time is not None else 0
        cpu = self.cpus()
        memory = self.memory()
        disks = self.disks(elapsed)
        network = self.network(elapsed)
        processes, skipped = self.processes(elapsed, memory["total"])
        workloads = self.workloads.sample(processes, elapsed)
        self.last_time = now
        try:
            usage = shutil.disk_usage("/")
            root_fs = {"total": usage.total, "used": usage.used, "free": usage.free}
        except OSError:
            root_fs = None
        loads = read(self.proc / "loadavg").split()
        return {"schema_version": 1, "timestamp": time.time(), "elapsed_s": elapsed,
                "hostname": os.uname().nodename, "cpu_model": self.model,
                "cpu": cpu, "memory": memory, "disks": disks, "gpus": self.gpus(),
                "network": network, "cpu_hardware": Hardware(self.sys / "devices/system/cpu").snapshot(),
                "workloads": workloads, "resource_topology": topology(self.sys),
                "pressure": self.pressure(), "temperatures": self.temperatures(),
                "processes": processes, "processes_skipped": skipped,
                "load": [number(x) for x in loads[:3]], "root_filesystem": root_fs,
                "warnings": (["CPU counters unavailable"] if not cpu else []) +
                            (["Memory counters unavailable"] if memory["total"] is None else [])}
