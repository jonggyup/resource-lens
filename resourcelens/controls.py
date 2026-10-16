"""Temporary systemd CPU settings, verification and conflict-aware session Undo."""

import math
from pathlib import Path
import re
import shutil
import subprocess
import time


class ControlError(RuntimeError):
    pass


def duration_usec(value):
    if value == "infinity":
        return None
    units = {"h": 3600e6, "min": 60e6, "s": 1e6, "ms": 1000, "us": 1, "µs": 1, "ns": .001}
    matches = list(re.finditer(r"(\d+(?:\.\d+)?)\s*(min|ms|us|µs|ns|h|s)", value))
    if not matches or re.sub(r"\s+", "", "".join(match[0] for match in matches)) != re.sub(r"\s+", "", value):
        raise ControlError(f"Unrecognized systemd CPU duration: {value!r}")
    return sum(float(match[1]) * units[match[2]] for match in matches)


def validate_value(property, value):
    if property not in ("weight", "quota"):
        raise ControlError("Only CPU weight and quota are supported")
    text = str(value).strip().lower().rstrip("%")
    if text in ("max", "unset", "default"):
        return None
    if not text.isdigit():
        raise ControlError("Enter a whole number, or 'max'/'default' to reset")
    result = int(text)
    maximum = 10000 if property == "weight" else 100000
    if not 1 <= result <= maximum:
        raise ControlError(f"{property.title()} must be between 1 and {maximum}")
    return result


def same_value(a, b):
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=1e-8, abs_tol=.0001)


class SystemdBackend:
    def __init__(self, root="/sys/fs/cgroup", runner=subprocess.run):
        self.root = Path(root)
        self.runner = runner
        self.binary = shutil.which("systemctl")

    def command(self, target, *args, timeout=5):
        unit = target.get("unit") or ""
        if not unit.endswith((".service", ".scope")) or unit.startswith("-") or any(
                not (c.isascii() and (c.isalnum() or c in "_.@:-\\")) for c in unit):
            raise ControlError("Select an existing systemd service or scope")
        if target.get("manager") not in ("system", "user"):
            raise ControlError("The owning systemd manager is unknown")
        if not self.binary:
            raise ControlError("systemctl is not installed")
        command = [self.binary, "--no-ask-password"]
        if target["manager"] == "user":
            command.append("--user")
        try:
            result = self.runner(command + list(args), capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ControlError("systemd did not respond; the setting may have changed. Inspect before retrying.") from exc
        if result.returncode:
            reason = (result.stderr or result.stdout or "Operation denied").strip()
            raise ControlError(reason[:500])
        return result.stdout

    def snapshot(self, target):
        from .collect import read
        from .workloads import WorkloadCollector, quota_percent
        if not target.get("controllable"):
            raise ControlError("This group is not a controllable service/scope owned by an accessible manager")
        directory = WorkloadCollector(self.root).directory(target["path"])
        if directory is None:
            raise ControlError("The workload's cgroup disappeared")
        try:
            inode = directory.stat().st_ino
        except OSError as exc:
            raise ControlError("Cannot identify the workload cgroup") from exc
        if inode != target.get("inode"):
            raise ControlError("The cgroup was replaced; select the workload again")
        if "cpu" not in read(self.root / "cgroup.controllers").split():
            raise ControlError("The cgroup v2 CPU controller is unavailable")
        output = self.command(target, "show", "--no-pager",
            "--property=Id,ControlGroup,InvocationID,ActiveState,CPUWeight,CPUQuotaPerSecUSec", "--", target["unit"])
        values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        if values.get("Id") != target["unit"] or values.get("ControlGroup") != target["path"]:
            raise ControlError("systemd unit and selected cgroup do not match")
        if values.get("ActiveState") != "active" or not values.get("InvocationID"):
            raise ControlError("Select an active unit with an identifiable invocation")
        raw_weight = values.get("CPUWeight")
        if raw_weight == "[not set]":
            weight = None
        elif raw_weight and raw_weight.isdigit() and 1 <= int(raw_weight) <= 10000:
            weight = int(raw_weight)
        else:
            raise ControlError("Cannot safely restore this CPUWeight configuration")
        quota = duration_usec(values.get("CPUQuotaPerSecUSec", ""))
        raw_kernel_weight = read(directory / "cpu.weight")
        kernel_max = read(directory / "cpu.max")
        return {"unit": target["unit"], "path": target["path"], "inode": inode,
                "invocation": values["InvocationID"], "weight": weight,
                "quota": quota / 10000 if quota is not None else None,
                "kernel_weight": int(raw_kernel_weight) if raw_kernel_weight.isdigit() else None,
                "kernel_quota": quota_percent(kernel_max), "kernel_max": kernel_max or None}

    def set(self, target, property, value):
        if property == "weight":
            setting = "CPUWeight=" + (f"{value:g}" if value is not None else "")
        elif property == "quota":
            setting = "CPUQuota=" + (f"{value:g}%" if value is not None else "")
        else:
            raise ControlError("Unsupported control property")
        self.command(target, "--runtime", "set-property", "--", target["unit"], setting)


class ControlSession:
    """Only the UI worker calls mutation methods. Records last until UI exit."""

    def __init__(self, backend=None):
        self.backend = backend or SystemdBackend()
        self.events = []

    @staticmethod
    def identity(info):
        return info["path"], info["inode"], info["invocation"]

    @staticmethod
    def kernel_matches(info, property, value):
        if property == "weight":
            return info["kernel_weight"] == (100 if value is None else value)
        if value is None:
            return bool(info["kernel_max"] and info["kernel_max"].split()[0] == "max")
        return same_value(info["kernel_quota"], value)

    def prepare(self, target, property, value):
        value = validate_value(property, value)
        target = {key: target.get(key) for key in ("unit", "path", "manager", "inode", "controllable", "owner_uid")}
        before = self.backend.snapshot(target)
        for setting, kernel_key in (("weight", "kernel_weight"), ("quota", "kernel_max")):
            if before[kernel_key] is not None and not self.kernel_matches(before, setting, before[setting]):
                raise ControlError("systemd and kernel CPU settings disagree; refusing to overwrite untracked configuration")
        if same_value(before[property], value):
            raise ControlError("This setting already has the requested value; no change was made")
        return {"target": dict(target), "property": property, "value": value, "before": before}

    def verify(self, target, property, value, identity):
        deadline = time.monotonic() + 1
        while True:
            current = self.backend.snapshot(target)
            if self.identity(current) != identity:
                raise ControlError("The unit restarted or its cgroup changed during the operation")
            if same_value(current[property], value) and self.kernel_matches(current, property, value):
                return current
            if time.monotonic() >= deadline:
                raise ControlError("Requested CPU setting is not confirmed in both systemd and kernel counters")
            time.sleep(.05)

    def verify_restore(self, target, property, before):
        deadline = time.monotonic() + 1
        kernel_key = "kernel_weight" if property == "weight" else "kernel_max"
        while True:
            current = self.backend.snapshot(target)
            if self.identity(current) != self.identity(before):
                raise ControlError("The unit changed during Undo")
            if same_value(current[property], before[property]) and (
                    self.kernel_matches(current, property, before[property]) or current[kernel_key] == before[kernel_key]):
                return current
            if time.monotonic() >= deadline:
                raise ControlError("Previous CPU setting is not confirmed after Undo")
            time.sleep(.05)

    def apply(self, plan):
        target, property, value, before = (plan[k] for k in ("target", "property", "value", "before"))
        current = self.backend.snapshot(target)
        if self.identity(current) != self.identity(before) or not same_value(current[property], before[property]):
            raise ControlError("The unit or setting changed after preview. Review a fresh preview.")
        if (current.get("kernel_weight"), current.get("kernel_max")) != (before.get("kernel_weight"), before.get("kernel_max")):
            raise ControlError("Kernel CPU settings changed after preview. Review a fresh preview.")
        event = {"timestamp": time.time(), "target": target, "property": property,
                 "before": before, "requested": value, "status": "unconfirmed"}
        self.events.append(event)
        try:
            self.backend.set(target, property, value)
            event["after"] = self.verify(target, property, value, self.identity(before))
            event["status"] = "confirmed"
        except ControlError as exc:
            event["error"] = str(exc)
            raise
        return event

    def undo(self):
        event = next((e for e in reversed(self.events) if e["status"] in ("confirmed", "unconfirmed")), None)
        if event is None:
            raise ControlError("No CPU changes from this session remain to undo")
        target, property = event["target"], event["property"]
        before = event["before"]
        current = self.backend.snapshot(target)
        if self.identity(current) != self.identity(before):
            raise ControlError("Undo refused: the unit restarted or its cgroup was replaced")
        if event["status"] == "unconfirmed" and same_value(current[property], before[property]):
            # A denied request left the original setting intact. No mutation needed.
            if (current.get("kernel_weight"), current.get("kernel_max")) == (before.get("kernel_weight"), before.get("kernel_max")):
                event["status"] = "undone"
                return event
        unchanged_kernel = (current.get("kernel_weight"), current.get("kernel_max")) == (before.get("kernel_weight"), before.get("kernel_max"))
        recoverable = self.kernel_matches(current, property, event["requested"]) or (
            event["status"] == "unconfirmed" and unchanged_kernel)
        if not same_value(current[property], event["requested"]) or not recoverable:
            raise ControlError("Undo refused: the setting differs from our applied value; another actor may have changed it")
        try:
            self.backend.set(target, property, before[property])
            event["restored"] = self.verify_restore(target, property, before)
        except ControlError as exc:
            event["error"] = f"Undo not confirmed: {exc}"
            event["status"] = "unconfirmed"
            raise
        event["status"] = "undone"
        event["undone_at"] = time.time()
        return event
