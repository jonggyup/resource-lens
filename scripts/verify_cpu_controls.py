#!/usr/bin/env python3
"""Explicit host integration check using only newly created disposable units.

Run from the repository: python3 scripts/verify_cpu_controls.py --run
Needs permission to create/control system services (normally root).
Creates bounded CPU workloads on one CPU for roughly 15 seconds.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from resourcelens.collect import Collector, read
from resourcelens.controls import ControlSession
from resourcelens.workloads import key_values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="create disposable systemd CPU workloads and verify controls")
    args = parser.parse_args()
    if not args.run:
        parser.error("Pass --run to execute the host integration experiment")
    if os.geteuid() != 0:
        parser.error("This integration experiment requires root; ordinary dashboard monitoring does not")
    prefix = "rlcheck" + uuid.uuid4().hex[:12]
    slice_name = prefix + ".slice"
    units = [prefix + "-a.service", prefix + "-b.service"]
    root = Path("/sys/fs/cgroup")
    cpu = min(os.sched_getaffinity(0))
    result = {"units": units, "slice": slice_name, "pinned_cpu": cpu,
              "root_controllers_before": read(root / "cgroup.subtree_control")}
    created = []

    def command(*args, check=True):
        return subprocess.run(list(args), capture_output=True, text=True, timeout=10, check=check)

    def require(condition, message):
        if not condition:
            raise RuntimeError(message)

    def usage(unit):
        return key_values(read(root / slice_name / unit / "cpu.stat")).get("usage_usec", 0)

    def measure(seconds=3):
        started = time.monotonic()
        before = [usage(unit) for unit in units]
        time.sleep(seconds)
        elapsed = time.monotonic() - started
        values = [(usage(unit) - value) / elapsed / 10000 for unit, value in zip(units, before)]
        return {"cpu_pct": values, "a_to_b_ratio": values[0] / values[1] if values[1] else None, "elapsed_s": elapsed}

    child_code = "import time\nwhile True: sum(i*i for i in range(10000))"
    parent_code = "import subprocess,sys\n" + "\n".join([
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])",
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])", child_code])
    session = ControlSession()
    try:
        for index, unit in enumerate(units):
            command("systemd-run", "--collect", "--unit=" + unit, "--slice=" + slice_name,
                    "--property=CPUAccounting=yes", "--property=CPUWeight=100",
                    f"--property=CPUAffinity={cpu}", "--property=RuntimeMaxSec=120",
                    "--property=TimeoutStopSec=3", "--property=KillMode=control-group",
                    sys.executable, "-c", parent_code if index == 0 else child_code)
            created.append(unit)
        collector = Collector(nvidia=False)
        target = None
        for _ in range(20):
            snapshot = collector.sample()
            target = next((group for group in snapshot["workloads"] if group["unit"] == units[0]), None)
            if target and len(target["pids"]) >= 3:
                break
            time.sleep(.1)
        require(target is not None and len(target["pids"]) >= 3, "Parent and child processes were not grouped together")
        result["grouped_pids"] = len(target["pids"])
        result["baseline"] = measure(2)
        session.apply(session.prepare(target, "weight", "400"))
        result["weighted"] = measure()
        require(result["weighted"]["a_to_b_ratio"] > 1.8 * result["baseline"]["a_to_b_ratio"], "CPU allocation did not respond to weight")
        session.apply(session.prepare(target, "quota", "25"))
        result["quota_25"] = measure()
        require(0 < result["quota_25"]["cpu_pct"][0] < 35, "Measured CPU did not respect the quota within measurement tolerance")
        stats = key_values(read(root / slice_name / units[0] / "cpu.stat"))
        require(stats.get("nr_throttled", 0) > 0, "No kernel quota throttling was observed")
        result["throttling"] = stats
        session.undo()
        session.undo()
        restored = session.backend.snapshot(target)
        require(restored["weight"] == 100 and restored["quota"] is None, "Undo did not restore original settings")
        result["restored"] = measure(2)
        command("systemctl", "--runtime", "set-property", slice_name, "CPUQuota=50%")
        session.apply(session.prepare(target, "quota", "80"))
        inherited = next(group for group in collector.sample()["workloads"] if group["unit"] == units[0])["limits"]
        require(inherited["quota_pct"] == 50 and inherited["quota_source"] == "/" + slice_name,
                "A tighter parent quota was not identified")
        result["inherited_quota"] = {key: inherited[key] for key in ("quota_pct", "quota_source", "quota_complete")}
        session.undo()
        command("systemctl", "--runtime", "set-property", slice_name, "CPUQuota=")
        result["events"] = [{key: event[key] for key in ("property", "requested", "status")} for event in session.events]
        result["passed"] = True
    finally:
        for unit in reversed(created):
            command("systemctl", "stop", unit, check=False)
        # Only these unique experiment-owned units/slice can have overrides removed.
        for unit in created + [slice_name]:
            command("systemctl", "stop", unit, check=False)
            command("systemctl", "revert", unit, check=False)
            command("systemctl", "reset-failed", unit, check=False)
        result["root_controllers_after"] = read(root / "cgroup.subtree_control")
        result["leftover_owned_cgroups"] = [name for name in created + [slice_name] if (root / slice_name / name).exists() or (root / name).exists()]
        print(json.dumps(result, indent=2))
    if result["leftover_owned_cgroups"]:
        raise RuntimeError("Experiment-owned cgroups remained after cleanup")


if __name__ == "__main__":
    main()
