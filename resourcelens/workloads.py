"""Read-only cgroup v2 workload accounting and inherited limits."""

import os
from pathlib import Path, PurePosixPath
import re

from .collect import delta, number, read


def membership(text):
    for line in text.splitlines():
        if line.startswith("0::/"):
            path = line[3:]
            if any(part in (".", "..") for part in path.split("/")):
                return None
            return path
    return None


def attribution(path, uid=None):
    if not path:
        return {"path": None, "unit": None, "manager": None, "kind": "unattributed", "owner_uid": None}
    parts = PurePosixPath(path).parts[1:]
    unit_index = next((i for i in range(len(parts) - 1, -1, -1)
                       if parts[i].endswith((".service", ".scope"))), None)
    if unit_index is None:
        return {"path": path, "unit": None, "manager": None, "kind": "cgroup", "owner_uid": None}
    unit = parts[unit_index]
    owner = next((int(match[1]) for part in parts[:unit_index]
                  if (match := re.fullmatch(r"user@(\d+)\.service", part))), None)
    manager = "user" if owner is not None else "system"
    kind = "user service" if owner is not None else "system service"
    if unit.endswith(".scope"):
        kind = "scope"
    return {"path": "/" + "/".join(parts[:unit_index + 1]), "unit": unit,
            "manager": manager, "kind": kind, "owner_uid": owner}


def key_values(text):
    result = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            result[parts[0]] = int(parts[1])
    return result


def quota_percent(text):
    parts = (text or "").split()
    if len(parts) != 2 or parts[0] == "max":
        return None
    quota, period = number(parts[0]), number(parts[1])
    return 100 * quota / period if quota is not None and period and quota >= 0 else None


def limit_number(text):
    return int(text) if text and text.isdigit() else None


class WorkloadCollector:
    def __init__(self, root="/sys/fs/cgroup"):
        self.root = Path(root).resolve()
        self.previous = {}
        self.hierarchy_previous = {}

    def directory(self, path):
        if not path or not path.startswith("/") or ".." in path.split("/"):
            return None
        target = self.root / path.lstrip("/")
        try:
            target.resolve().relative_to(self.root)
            return target if target.is_dir() else None
        except (OSError, ValueError):
            return None

    def limits(self, path, cache=None, directory=None):
        cache = {} if cache is None else cache
        directory = self.directory(path) if directory is None else directory
        if directory is None:
            return {"ancestors": [], "quota_pct": None, "quota_source": None,
                    "quota_complete": False, "memory_max": None, "memory_source": None,
                    "memory_complete": False, "cpu_controller_active": False}
        chain, current = [], directory
        while current != self.root:
            relative = "/" + str(current.relative_to(self.root))
            if relative not in cache:
                raw = {name: read(current / name) or None for name in (
                    "cpu.max", "cpu.weight", "memory.high", "memory.max", "io.max", "cpuset.cpus.effective")}
                cache[relative] = {"path": relative, **raw, "quota_pct": quota_percent(raw["cpu.max"])}
            chain.append(cache[relative])
            current = current.parent
        quotas = [(entry["quota_pct"], entry["path"]) for entry in chain if entry["quota_pct"] is not None]
        memory = [(limit_number(entry["memory.max"]), entry["path"]) for entry in chain
                  if limit_number(entry["memory.max"]) is not None]
        cap, source = min(quotas, default=(None, None), key=lambda pair: pair[0])
        mem, mem_source = min(memory, default=(None, None), key=lambda pair: pair[0])
        return {"ancestors": chain, "quota_pct": cap, "quota_source": source,
                "quota_complete": bool(chain) and all(entry["cpu.max"] is not None for entry in chain),
                "memory_max": mem, "memory_source": mem_source,
                "memory_complete": bool(chain) and all(entry["memory.max"] is not None for entry in chain),
                "cpu_controller_active": bool(chain and chain[0]["cpu.max"] is not None)}

    def sample(self, processes, elapsed):
        groups = {}
        for process in processes:
            info = attribution(process.get("cgroup"))
            key = info["path"] or "(unattributed)"
            group = groups.setdefault(key, {**info, "id": key, "pids": [], "names": [],
                                            "cpu_pct": None, "memory_current": None,
                                            "io_devices": [], "cpu_stat": {}, "pressure": {},
                                            "throttled_usec_per_s": None, "inode": None})
            group["pids"].append(process["pid"])
            if process["name"] not in group["names"]:
                group["names"].append(process["name"])
        previous, cache = {}, {}
        for group in groups.values():
            directory = self.directory(group["path"])
            group["limits"] = self.limits(group["path"], cache, directory)
            group["controllable"] = bool(group["unit"] and directory and
                (group["manager"] == "system" or group["owner_uid"] == os.getuid()))
            if directory is None:
                continue
            try:
                group["inode"] = directory.stat().st_ino
            except OSError:
                continue
            identity = (group["id"], group["inode"])
            old = self.previous.get(identity, {})
            stats = key_values(read(directory / "cpu.stat"))
            group["cpu_stat"] = stats
            if "usage_usec" in stats:
                usage = delta(stats["usage_usec"], old.get("usage_usec"), elapsed)
                group["cpu_pct"] = usage / 10000 if usage is not None else None
            if "throttled_usec" in stats:
                group["throttled_usec_per_s"] = delta(stats["throttled_usec"], old.get("throttled_usec"), elapsed)
            group["memory_current"] = limit_number(read(directory / "memory.current"))
            io_counters = {}
            for line in read(directory / "io.stat").splitlines():
                parts = line.split()
                if not parts:
                    continue
                values = {key: int(value) for key, sep, value in
                          (part.partition("=") for part in parts[1:]) if sep and value.isdigit()}
                io_counters[parts[0]] = values
                last = old.get("io", {}).get(parts[0], {})
                group["io_devices"].append({"device": parts[0], **values,
                    "read_bps": delta(values["rbytes"], last.get("rbytes"), elapsed) if "rbytes" in values else None,
                    "write_bps": delta(values["wbytes"], last.get("wbytes"), elapsed) if "wbytes" in values else None})
            for kind in ("cpu", "memory", "io"):
                line = next((line for line in read(directory / f"{kind}.pressure").splitlines()
                             if line.startswith("some ")), "")
                group["pressure"][kind] = {key: number(value) for key, sep, value in
                    (part.partition("=") for part in line.split()[1:]) if sep}
            previous[identity] = {**stats, "io": io_counters}
        self.previous = previous
        return sorted(groups.values(), key=lambda group: group["id"])

    def hierarchy(self, elapsed, limit=2048):
        """Bounded directory walk in the collector thread; never on UI input."""
        nodes, previous, pending = [], {}, [self.root]
        errors, truncated = 0, False
        while pending:
            directory = pending.pop()
            try:
                inode = directory.stat().st_ino
                path = '/' + str(directory.relative_to(self.root)) if directory != self.root else '/'
                identity = (path, inode)
                stats = key_values(read(directory / 'cpu.stat'))
                usage = delta(stats['usage_usec'], self.hierarchy_previous.get(identity), elapsed) if 'usage_usec' in stats else None
                nodes.append(dict(path=path, inode=inode, cpu_pct=usage / 10000 if usage is not None else None,
                                  memory_current=limit_number(read(directory / 'memory.current'))))
                previous[identity] = stats.get('usage_usec')
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if entry.is_dir(follow_symlinks=False):
                            if len(nodes) + len(pending) < limit:
                                pending.append(Path(entry.path))
                            else:
                                truncated = True
                                break
            except OSError:
                errors += 1
        self.hierarchy_previous = previous
        return dict(nodes=nodes, truncated=truncated, errors=errors, limit=limit)
