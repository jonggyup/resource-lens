# Investigation sessions (0.13.0 preview)

Use this workflow to preserve evidence before and after a workload or configuration
change. It combines Resource Lens counters with Linux perf; it is not a replacement
for perf, an automatic regression verdict, or a complete kernel profiler.

## Interactive capture

Open Applications (`8`), select a process, and press **P**. In cgroup mode (`g`),
P captures the selected group; inside the hierarchy it captures the open group or
PID. The target identity is checked, so a stale selection cannot silently choose a
replacement. Recording replay cannot initiate a live profile.

Collection runs in a background thread for ten seconds. The screen displays the
saved directory, collection status, counters, diagnostics, and the first 100 lines
of a readable perf report. Arrow keys or mouse wheel scroll. **Esc cancels and
returns immediately**; workers finish cleanup in the background. A completed
capture remains saved. Pressing Escape again follows the existing navigation.
The footer's action is clickable. Existing hardware/control shortcuts remain intact.

Artifacts go under `$XDG_STATE_HOME/resource-lens/profiles/` or
`~/.local/state/resource-lens/profiles/`. If the dashboard exits before background
cleanup completes, the initial manifest can remain `collecting`; comparison rejects
it. The perf child has a bounded sleep duration even if the dashboard terminates.

## CLI capture

From the repository, use `python3 -m resourcelens`; after installation,
`resource-lens` accepts the same options. Replace 1234 with a live target PID:

```bash
python3 -m resourcelens --profile ./baseline/run1 --pid 1234 \
  --duration 30 --label 'checkout load, build abc123, 100 requests/s'
python3 -m resourcelens --profile ./candidate/run1 --pid 1234 \
  --duration 30 --label 'checkout load, build def456, 100 requests/s'
python3 -m resourcelens --compare ./baseline/run1 ./candidate/run1
```

Each destination must be new. Durations are 0.2–300 seconds. PID profiling covers
that process's threads, excluding descendant processes; use a cgroup for a service
with multiple processes. An absolute cgroup v2 directory is required:

```bash
python3 -m resourcelens --profile ./service-run \
  --cgroup /sys/fs/cgroup/system.slice/example.service --duration 20
```

Cgroup CPU profiles use perf's system-wide cgroup filter and may require additional
permissions. CPU sampling is 49 Hz with `cpu-clock` and frame-pointer call chains.
Install your distribution's perf package (often `linux-tools` or `perf`). No tool
automatically changes perf permissions, schedstats settings, or kernel security.
Use `--no-perf` to collect counters without sampling.

A counter session can complete while its CPU profile fails. Inspect both `status`
and `profile.status`; CLI exit status 0 means counter collection completed, **not**
that call stacks were collected. `perf.log` retains the reason. Missing perf is
`unavailable`; decoding/collection errors are `failed`. Ctrl-C saves a cancelled
manifest when cleanup can run and exits 130. Other partial captures exit 1.

## Repeat and compare

Run at least three captures per variant, each under the same steady workload, with
consistent warmup and durations. Use new directories `baseline/run2`, `run3`, etc.
Alternate variants where possible to reduce time-of-day and thermal bias.
Compare the parent directories to aggregate their immediate child sessions:

```bash
python3 -m resourcelens --compare ./baseline ./candidate > comparison.json
```

The result reports count, mean, sample standard deviation, minimum, maximum,
absolute difference and relative mean difference. Zero baselines have no relative
percentage. Only metrics present in every session are compared. Configuration
changes and differences within repeat sets are surfaced. Incomplete sessions are
rejected. These are descriptive statistics, not significance tests.

You can attach **externally measured** workload outcomes using repeatable
`--outcome NAME=VALUE`, for example `--outcome p99_ms=12.5 --outcome requests_per_s=100`.
Include units in each name and use measurements corresponding to the capture's
workload window. Resource Lens does not run your benchmark or measure request
latency itself; supplied values are not independently verified. Without outcomes,
the comparison explicitly states that application performance is unmeasured.
Build/workload descriptions are recorded with `--label`; different labels trigger
a reminder to verify workload equivalence.

## Evidence and interpretation

Each session contains:

- `session.json`: schema, target identity, label, supplied outcomes, timestamp,
  actual duration, raw counter samples every roughly 250 ms, metrics, warnings,
  kernel version, machine, online CPUs, governors, perf/scheduler settings, and
  PID executable/affinity or selected cgroup limits.
- `perf.data`, when available: native profile, inspect with
  `perf report -i /path/to/session/perf.data`.
- `perf.log`: collector diagnostics, including lost-sample messages.
- `perf-report.txt`: decoded stacks, generated with a five-second timeout and
  a two-percent display threshold. Use native perf to examine smaller contributors.

Directories are mode 0700, files mode 0600, and existing destinations are never
overwritten. Artifacts include executable paths, symbols, PID/cgroup identities
and raw process status; review them before sharing. No uploads occur.

CPU percent uses one CPU as 100%, and may exceed 100% for multithreaded targets.
Process RSS is an end-of-window value; it is not unique resident memory.
Process major faults are reported per second. Cgroup throttling is cumulative
throttled microseconds divided by wall time, **not a request-latency percentage**.
Scheduler runqueue wait is reported only when schedstats is enabled and only for
threads with matching identities at both endpoints. Short-lived threads, blocked
I/O, locks, and off-CPU stacks are not covered by that estimate. A scan is bounded
to 4096 threads; truncation is recorded in raw samples.

Call chains depend on symbols and frame pointers. `recorded` means perf could
decode the file; it does not promise sufficient samples or complete stacks. Read
the report's sample count and lost samples. Idle or short targets can have little
evidence. Counter and perf windows are only approximately aligned; sampling,
process startup and decoding add overhead. A 64 MiB perf-data threshold is checked
between samples; it is a soft limit and can overshoot before cleanup. Kernel reads
and process termination can extend the requested wall-clock duration.

## Next profiling work

This release establishes target → saved evidence → repeat comparison. It does not
implement off-CPU stack attribution, scheduler latency distributions, hardware PMU
analysis, block/network request latency, lock contention, automatic stack diffs,
or an interactive baseline browser. The next useful step is explicit opt-in
scheduler trace capture with per-target filtering and trace-loss accounting,
followed by a saved-session comparison UI. Those need validation on supported
kernels and privilege configurations before presenting causal explanations.

References: [Linux perf record](https://github.com/torvalds/linux/blob/master/tools/perf/Documentation/perf-record.txt),
[perf security](https://www.kernel.org/doc/html/latest/admin-guide/perf-security.html),
[proc counters](https://www.kernel.org/doc/html/latest/filesystems/proc.html).
