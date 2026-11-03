"""Read Linux counters. Rates use monotonic elapsed time, never refresh assumptions."""

import csv
import io
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import resource
import threading
import time
import uuid
from .hardware import Hardware
from .model import CollectionScope, SourceQuality, Coverage
from .resource_topology import topology
from .sampling import StageTimings, SampleSource, Observation
from .sources import read_text


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


def collection_scope(proc, cgroup_root):
    """Observed context only; a readable root does not prove complete ancestry."""
    def namespace(name):
        try:
            info = (proc / 'self/ns' / name).stat()
            return f'{info.st_dev}:{info.st_ino}'
        except OSError:
            return None
    try:
        root_info = cgroup_root.stat()
        device, inode = root_info.st_dev, root_info.st_ino
    except OSError:
        device = inode = None
    return CollectionScope(uuid.uuid4().hex,
        boot_id=read(proc / 'sys/kernel/random/boot_id') or None,
        pid_namespace=namespace('pid'), cgroup_namespace=namespace('cgroup'),
        cgroup_root_device=device, cgroup_root_inode=inode)


class Collector:
    def __init__(self, proc="/proc", sys="/sys", nvidia=True, clock=time.monotonic,
                 *, background=False, cache_ttl=10, process_limit=10000, process_budget=.2):
        self.proc, self.sys = Path(proc), Path(sys)
        self.scope = collection_scope(self.proc, self.sys / 'fs/cgroup')
        self.query_lock = threading.Lock()
        self.active_query = None
        self.closed = False
        self.clock = clock
        self.background = background
        self.sequence = 0
        self.source_quality, self.source_activity = {}, {}
        self.process_limit, self.process_budget = process_limit, process_budget
        if type(process_limit) is not int or process_limit < 1 or not math.isfinite(process_budget) or process_budget <= 0:
            raise ValueError('Process count and time budgets must be positive')
        if not math.isfinite(cache_ttl) or cache_ttl < 0:
            raise ValueError('Inventory cache time must be finite and nonnegative')
        self.sources = {
            'frequencies': SampleSource('frequencies', self.scope.id, ttl=1 if background else 0, background=background, clock=clock),
            'gpus': SampleSource('gpus', self.scope.id, ttl=2 if background else 0, background=background, clock=clock),
            'cpu_hardware': SampleSource('cpu_hardware', self.scope.id, ttl=cache_ttl, clock=clock),
            'resource_topology': SampleSource('resource_topology', self.scope.id, ttl=cache_ttl, clock=clock),
            'cgroup_tree': SampleSource('cgroup_tree', self.scope.id, ttl=1 if background else 0, clock=clock),
            'target_context': SampleSource('target_context', self.scope.id, background=True, clock=clock, retain_on_error=False),
        }
        from .targets import TargetResolver
        self.target_resolver = TargetResolver(self.proc, self.sys / 'fs/cgroup', self.scope.id, clock=clock)
        self.last_hierarchy_time = None
        from .energy import EnergyMeter
        self.energy = EnergyMeter(self.sys, clock)
        self.last_time = None
        self.last_cpu, self.last_disk, self.last_process = {}, {}, {}
        self.last_network = {}
        self.last_thermal = {}
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
        source = read_text(self.proc / 'stat', limit=2097152)
        for line in (source.text or '').splitlines():
            parts = line.split()
            if parts and re.fullmatch(r"cpu\d*", parts[0]):
                try:
                    # guest and guest_nice are already included in user/nice.
                    values = [int(v) for v in parts[1:9]]
                    if any(value < 0 for value in values):
                        continue
                    counters[parts[0]] = values + [0] * (8 - len(values))
                except ValueError:
                    continue
        result = []
        for name, current in counters.items():
            previous = self.last_cpu.get(name)
            busy = wait = steal = None
            reason = 'warmup' if previous is None else 'counter_reset'
            if previous is not None and all(a >= b for a, b in zip(current, previous)):
                diff = [a - b for a, b in zip(current, previous)]
                total = sum(diff)
                if total:
                    busy = 100 * (total - diff[3] - diff[4]) / total
                    wait, steal = 100 * diff[4] / total, 100 * diff[7] / total
                    reason = None
                else:
                    reason = 'warmup'
            result.append({"id": name, "busy_pct": busy, "iowait_pct": wait,
                           "steal_pct": steal, "frequency_mhz": None, 'busy_reason': reason})
        self.last_cpu = counters
        aggregate = next((row for row in result if row['id'] == 'cpu'), None)
        reason = aggregate['busy_reason'] if aggregate else source.reason or 'parse_error'
        self._quality('cpu', 'ok' if reason is None else 'unavailable', reason)
        return result

    def _quality(self, name, status='ok', reason=None, coverage=None):
        self.source_quality[name] = SourceQuality(name, status, reason, scope_id=self.scope.id,
            sequence=self.sequence, observed_at_wall=time.time(), observed_at_monotonic=self.clock(),
            coverage=coverage or Coverage()).to_dict()

    def _cached(self, name, key, function, default):
        source = self.sources[name]
        source.request(key, function)
        value, quality, activity = source.snapshot(default)
        self.source_quality[name], self.source_activity[name] = quality, activity
        return default if value is None else value

    def frequencies(self, ids):
        values, reasons = {}, {}
        for name in ids:
            result = read_text(self.sys / 'devices/system/cpu' / name / 'cpufreq/scaling_cur_freq')
            value = number(result.text)
            values[name] = value / 1000 if value is not None and value >= 0 else None
            if values[name] is None:
                reasons[name] = result.reason or 'parse_error'
        available = sum(value is not None for value in values.values())
        status = 'ok' if available == len(ids) and ids else 'partial' if available else 'unavailable'
        return Observation(dict(values=values, reasons=reasons), status,
                           None if status == 'ok' else next(iter(reasons.values()), 'not_exposed'),
                           Coverage(examined=len(ids), omitted=0))

    def hardware_inventory(self):
        data = Hardware(self.sys / 'devices/system/cpu').snapshot()
        partial = any(c['socket'] is None or c['core'] is None for c in data['cpus'])
        return Observation(data, 'partial' if partial else 'ok' if data['cpus'] else 'unavailable',
                           'unknown' if partial else None if data['cpus'] else 'not_exposed')

    def hierarchy(self):
        now = self.clock()
        elapsed = now - self.last_hierarchy_time if self.last_hierarchy_time is not None else 0
        data = self.workloads.hierarchy(elapsed)
        self.last_hierarchy_time = now
        reason = 'budget_exceeded' if data['truncated'] else 'unknown' if data['errors'] else None
        return Observation(data, 'partial' if reason else 'ok', reason,
                           Coverage(examined=len(data['nodes']), omitted=None if data['truncated'] else 0,
                                    truncation_reason='directory limit' if data['truncated'] else None))

    def request_target(self, request):
        """Safe for input handlers: only update the worker's latest request."""
        source = self.sources['target_context']
        if request is None:
            if source.key is not None:
                source.clear()
        else:
            request = tuple(request)
            source.request(request, lambda: self.target_resolver.resolve(request))

    def invalidate_inventory(self):
        for name in ('cpu_hardware', 'resource_topology', 'frequencies', 'cgroup_tree', 'target_context'):
            self.sources[name].invalidate()

    def memory(self):
        source = read_text(self.proc / 'meminfo')
        values = fields(source.text or '')
        total, available = values.get("MemTotal"), values.get("MemAvailable")
        if total is not None and total < 0:
            total = None
        if available is not None and available < 0:
            available = None
        reason = None if available is not None else source.reason or ('not_exposed' if values else 'parse_error')
        self._quality('memory.available', 'ok' if reason is None else 'unavailable', reason)
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
            source = read_text(self.proc / 'pressure' / kind)
            for line in (source.text or '').splitlines():
                parts = line.split()
                if parts:
                    entry[parts[0]] = {k: number(v) for k, sep, v in
                                      (part.partition("=") for part in parts[1:]) if sep}
                    for key in ('avg10', 'avg60', 'avg300'):
                        value = entry[parts[0]].get(key)
                        if value is not None and not 0 <= value <= 100:
                            entry[parts[0]][key] = None
            result[kind] = entry
            reason = None if entry.get('some', {}).get('avg10') is not None else source.reason or 'parse_error'
            self._quality('pressure.' + kind, 'ok' if reason is None else 'unavailable', reason)
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
            try:
                device_inode = (self.sys / 'block' / name).stat().st_ino
            except OSError:
                continue  # Device disappeared during the sample.
            result.append({"name": name, "read_bps": rate(2, 512), "write_bps": rate(6, 512),
                           "read_iops": rate(0), "write_iops": rate(4),
                           "active_pct": min(100, active) if active is not None else None,
                           "queue_depth": rate(10, 0.001), "in_flight": v[8],
                           "model": read(self.sys / "block" / name / "device/model") or name,
                           "identity": read(self.sys / "block" / name / "dev") + ":" + str(device_inode)})
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
        skipped, examined, truncated = 0, 0, False
        reasons = []
        started = time.monotonic()
        for path in self.proc.glob("[0-9]*"):
            if examined >= self.process_limit or time.monotonic() - started >= self.process_budget:
                truncated = True
                break
            examined += 1
            try:
                stat = read_text(path / 'stat', missing='disappeared')
                if stat.reason:
                    skipped += 1
                    reasons.append(stat.reason)
                    continue
                text = stat.text
                end = text.rindex(")")
                name = text[text.index("(") + 1:end]
                v = text[end + 2:].split()
                # Remaining fields start at stat field 3 (state).
                identity = (int(path.name), int(v[19]))
                ticks = int(v[11]) + int(v[12])
                resident = max(0, int(v[21])) * self.pages
                counters[identity] = ticks
                cpu = delta(ticks, self.last_process.get(identity), elapsed)
                group = read_text(path / 'cgroup', missing='disappeared')
                cgroup = membership(group.text or '')
                group_reason = group.reason or ('not_exposed' if cgroup is None else None)
                if group_reason:
                    reasons.append(group_reason)
                result.append({"pid": identity[0], "ppid": int(v[1]), "starttime": identity[1], "name": name, "state": v[0],
                               "nice": int(v[16]), "memory": resident,
                               "cpu_pct": cpu / self.ticks * 100 if cpu is not None else None,
                               'cpu_reason': None if cpu is not None else 'warmup' if identity not in self.last_process or elapsed <= 0 else 'counter_reset',
                               "memory_pct": 100 * resident / total_memory if total_memory else None,
                               "cgroup": cgroup, 'cgroup_reason': group_reason})
            except (OSError, ValueError, IndexError):
                skipped += 1
                reasons.append('parse_error')
        self.last_process = counters
        reason = 'budget_exceeded' if truncated else next(iter(reasons), None)
        self._quality('processes', 'partial' if reason else 'ok', reason,
                      Coverage(examined=examined, omitted=None if truncated else skipped,
                               truncation_reason='process/time budget' if truncated else None))
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

    def close(self):
        """Cancel only our read-only external telemetry query, never control jobs."""
        with self.query_lock:
            self.closed = True
            if self.active_query is not None and self.active_query.poll() is None:
                try:
                    self.active_query.kill()
                except ProcessLookupError:
                    pass
        for source in self.sources.values():
            source.close()

    def run_query(self, command, **options):
        timeout = options.pop('timeout')
        options.pop('capture_output', None)
        with self.query_lock:
            if self.closed:
                raise OSError('Collector is closed')
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
            self.active_query = process
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        finally:
            if process.poll() is None:
                process.kill()
                try:
                    process.communicate(timeout=.1)
                except subprocess.TimeoutExpired:
                    pass  # A kernel-blocked process cannot be synchronously reaped.
            with self.query_lock:
                self.active_query = None

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
                completed = self.run_query([self.nvidia,
                    "--query-gpu=name,pci.bus_id,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,clocks.gr",
                    "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=0.8,
                    env={**os.environ, "LC_ALL": "C"})
                parsed = parse_nvidia(completed.stdout) if completed.returncode == 0 else []
                if parsed:
                    result.extend(parsed)
                    nvidia_cards = []
                error = "nvidia-smi query failed or returned no devices"
                if completed.returncode:
                    self._gpu_failure = 'permission_denied' if 'permission' in (completed.stderr or '').lower() else 'unknown'
            except (OSError, subprocess.TimeoutExpired):
                error = "nvidia-smi unavailable or timed out"
                self._gpu_failure = 'timed_out'
        for card in nvidia_cards:
            result.append({"id": card, "name": "NVIDIA", "driver": "nvidia", "busy_pct": None,
                           "memory_used": None, "memory_total": None, "temperature_c": None,
                           "power_w": None, "clock_mhz": None, "status": error})
        return result

    def gpu_observation(self):
        self._gpu_failure = None
        rows = self.gpus()
        available = sum(row['busy_pct'] is not None for row in rows)
        status = 'ok' if rows and available == len(rows) else 'partial' if available else 'unavailable'
        return Observation(rows, status, None if status == 'ok' else self._gpu_failure or 'not_exposed',
                           Coverage(examined=len(rows), omitted=0))

    def thermal_throttling(self):
        values, rows = {}, []
        for path in (self.sys / 'devices/system/cpu').glob('cpu[0-9]*/thermal_throttle'):
            row = {'cpu': path.parent.name}
            for kind in ('core', 'package'):
                raw = read(path / (kind + '_throttle_count'))
                key = (path.parent.name, kind)
                count = int(raw) if raw.isdigit() else None
                previous = self.last_thermal.get(key)
                row[kind + '_events'] = count - previous if count is not None and previous is not None and count >= previous else None
                row[kind + '_total'] = count
                values[key] = count
            rows.append(row)
        self.last_thermal = values
        return rows

    def root_filesystem(self):
        try:
            usage = shutil.disk_usage("/")
            return {"total": usage.total, "used": usage.used, "free": usage.free}
        except OSError:
            return None

    def load(self):
        return [number(x) for x in read(self.proc / "loadavg").split()[:3]]

    def capabilities(self):
        cpu_root = self.sys / 'devices/system/cpu'
        return dict(cgroup_v2=(self.sys/'fs/cgroup/cgroup.controllers').exists(),
            controllers=read(self.sys/'fs/cgroup/cgroup.controllers'), systemctl=bool(shutil.which('systemctl')),
            writable_governors=sum(os.access(p, os.W_OK) for p in cpu_root.glob('cpufreq/policy*/scaling_governor')),
            writable_online=sum(os.access(p, os.W_OK) for p in cpu_root.glob('cpu[0-9]*/online')))

    def sample(self):
        wall_start, cpu_start = time.monotonic(), time.thread_time()
        self.sequence += 1
        self.source_quality, self.source_activity = {}, {}
        timings = StageTimings()
        measure = timings.measure
        now = self.clock()
        elapsed = now - self.last_time if self.last_time is not None else 0
        cpu = measure('cpu', self.cpus)
        cpu_ids = tuple(row['id'] for row in cpu if row['id'] != 'cpu')
        frequencies = measure('frequencies', self._cached, 'frequencies', cpu_ids,
                              lambda: self.frequencies(cpu_ids), {'values': {}, 'reasons': {}})
        for row in cpu:
            row['frequency_mhz'] = frequencies['values'].get(row['id'])
            row['frequency_reason'] = frequencies['reasons'].get(row['id'])
        memory = measure('memory', self.memory)
        disks = measure('disks', self.disks, elapsed)
        network = measure('network', self.network, elapsed)
        processes, skipped = measure('processes', self.processes, elapsed, memory["total"])
        workloads = measure('workloads', self.workloads.sample, processes, elapsed)
        self.last_time = now
        root_fs = measure('root_filesystem', self.root_filesystem)
        loads = measure('load', self.load)
        online = read(self.sys / 'devices/system/cpu/online')
        inventory_key = (read(self.sys / 'devices/system/cpu/present'), online)
        device_key = (inventory_key, tuple(d['identity'] for d in disks),
                      tuple((n['name'], n['ifindex']) for n in network))
        gpus = measure('gpus', self._cached, 'gpus', 'gpu-inventory', self.gpu_observation, [])
        sample = {"schema_version": 1, "timestamp": time.time(), "elapsed_s": elapsed,
                "collection_scope": self.scope.to_dict(),
                "hostname": os.uname().nodename, "cpu_model": self.model,
                "cpu": cpu, "memory": memory, "disks": disks, "gpus": gpus,
                "network": network, "cpu_hardware": measure('cpu_hardware', self._cached, 'cpu_hardware', inventory_key,
                    self.hardware_inventory, {'cpus': [], 'policies': {}}),
                "workloads": workloads, "cgroup_tree": measure('cgroup_tree', self._cached, 'cgroup_tree', 'hierarchy',
                    self.hierarchy, {'nodes': [], 'truncated': False, 'errors': 0, 'limit': 2048}),
                "resource_topology": measure('resource_topology', self._cached, 'resource_topology',
                    (device_key, tuple(g['id'] for g in gpus)),
                    lambda: Observation(topology(self.sys), 'partial', 'unknown'),
                    {'memory_nodes': [], 'disks': {}, 'network': {}, 'gpus': {}}),
                "thermal_throttling": measure('thermal_throttling', self.thermal_throttling),
                "pressure": measure('pressure', self.pressure), "temperatures": measure('temperatures', self.temperatures),
                "energy": measure('energy', self.energy.sample),
                "processes": processes, "processes_skipped": skipped,
                "load": loads, "root_filesystem": root_fs,
                "warnings": (["CPU counters unavailable"] if not cpu else []) +
                            (["Memory counters unavailable"] if memory["total"] is None else [])}

        sample['capabilities'] = measure('capabilities', self.capabilities)
        context, quality, activity = self.sources['target_context'].snapshot()
        if context is not None:
            sample['target_context'] = context
        if self.sources['target_context'].key is not None:
            self.source_quality['target_context'], self.source_activity['target_context'] = quality, activity
        sample['source_quality'] = self.source_quality
        sample['source_activity'] = self.source_activity
        sample['source_cadence_s'] = {name: source.ttl for name, source in self.sources.items()}
        sample['timestamp'] = time.time()  # Publication follows all included observations.
        counts = dict(logical_cpus=sum(c['id'] != 'cpu' for c in cpu), processes=len(processes),
            processes_skipped=skipped, workloads=len(workloads), cgroup_nodes=len(sample['cgroup_tree']['nodes']),
            disks=len(disks), network_interfaces=len(network), gpus=len(sample['gpus']),
            energy_domains=len(sample['energy']['domains']))
        sample['collector'] = dict(duration_ms=(time.monotonic()-wall_start)*1000,
            cpu_ms=(time.thread_time()-cpu_start)*1000,
            rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
            stages=timings.stages, counts=counts, mode='background' if self.background else 'synchronous')
        return sample
