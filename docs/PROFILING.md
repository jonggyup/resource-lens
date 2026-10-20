# Investigation sessions (0.15.0 preview)

Use this workflow to preserve evidence before and after a workload or configuration
change. It combines Resource Lens counters with Linux perf; it is not a replacement
for perf, an automatic regression verdict, or a complete kernel profiler.

## Interactive capture

Open Applications (`8`), select a process, and press **P** for **Investigate**.
Choose **CPU usage**, **CPU + waits**, or **Energy efficiency**. Energy captures
hardware-domain counters without CPU stack sampling; see [energy](ENERGY.md).
Click a choice, press 1/2/3, or use arrows then
Enter. No collection starts until you choose; Escape closes the menu. In cgroup
mode (`g`), the target is the selected group; inside the hierarchy it is the open
group or PID. The target identity is checked, so a stale selection cannot silently choose a
replacement. Recording replay cannot initiate a live profile.

Collection runs in a background thread for ten seconds. The screen displays the
saved directory, collection status and a short counter/wait summary. Press **D**
for coverage, diagnostics, switch-out stacks and the first 100 lines of the CPU
report; D again returns to the summary. Arrow keys or mouse wheel scroll. **Esc cancels and
returns immediately**; workers finish cleanup in the background. A completed
capture remains saved. Pressing Escape again follows the existing navigation.
The footer's action is clickable. Existing hardware/control shortcuts remain intact.

Artifacts go under `$XDG_STATE_HOME/resource-lens/profiles/` or
`~/.local/state/resource-lens/profiles/`. If the dashboard exits before background
cleanup completes, the initial manifest can remain `collecting`; comparison rejects
it. The perf child has a bounded sleep duration even if the dashboard terminates.

## Scheduler tracing: W / --scheduler

In Applications or a process/cgroup detail view, press **W** instead of P to add
scheduler tracing to the ten-second capture. W is a direct shortcut for **CPU + waits**; the P menu offers both modes. Escape returns immediately and requests background cleanup.
The report is scrollable using Up/Down or the mouse wheel. F1 documents the shortcut.

```bash
python3 -m resourcelens --profile ./waits/run1 --pid 1234 --duration 10 --scheduler
# Skip CPU-clock sampling while retaining the explicitly requested scheduler trace:
python3 -m resourcelens --profile ./waits/run2 --pid 1234 --duration 10 --scheduler --no-perf
```

This requires Linux perf, readable scheduler tracepoints, and permission for
system-wide perf events. It works independently of the schedstats polling setting.
There is no automatic sudo invocation or kernel-setting change. Errors appear under
`scheduler.status` and in the saved log; ordinary counters can still complete.
The initial host PID namespace is required, so container-local PIDs cannot silently
select different host tasks. The namespace check fails closed on unrecognized
namespace inode identifiers.

Collection is system-wide **with kernel filters for the initial target TIDs**:
`sched_switch` includes switches involving those TIDs and `sched_wakeup` includes
wakeups directed to them. This captures wakeups made by other tasks, which attaching
perf only to the target PID would miss. There is no unfiltered fallback. Up to
64 initial threads and 256 cgroup directories are supported; larger scopes are
refused with a reason. A cgroup target snapshots its initial threads and descendants;
this is not continuously updated cgroup attribution. Threads that migrate out can
remain in this frozen scope; new threads are omitted. Choose a stable workload.

The report separates:

| Measurement | Observed endpoints | Meaning |
| --- | --- | --- |
| Off CPU | switch-out to next switch-in | Includes sleeping and time waiting to run |
| Sleep to wakeup | S/D/I switch-out to observed wakeup | Voluntary or involuntary sleep; does not identify a specific I/O request or lock |
| Ready to running | wakeup to switch-in, or R/R+ switch-out to switch-in | Time runnable but not executing; not application request latency |

Percentiles use nearest-rank selection over complete observed intervals. The
observed span is from the first to last decoded event, excluding startup and
quiet periods at either end. Totals sum thread intervals and can exceed wall time. Each row
shows its interval count, p50/p95/p99 and maximum; JSON also contains mean and total.
Wakeups at the beginning can contribute ready-to-running intervals without a full
off-CPU interval. Missing wakeups are counted and never invented. Boundary intervals
without an endpoint are excluded and counted. Long waits spanning the whole capture
can therefore be absent: these distributions are not an unbiased census of all waits.
Trace status `complete` means decoding passed the checks, not that every lifetime or
boundary interval is present. Empty traces are explicitly labelled `empty`.

Switch-out call chains are grouped by task state and ranked by summed off-CPU time.
They belong to the outgoing task, not the incoming task or the wakeup source. The
screen shows the five largest groups; JSON retains ten and decoded events preserve
the raw evidence. Frame-pointer unwinding, symbol availability and stack depth limit
attribution. Sleeping in a timer or waiting in a futex is evidence of where the task
waited, not proof of a performance problem or its root cause.

Artifacts added to the private session directory:

- `sched.data`: native perf trace, usable with `perf script -i .../sched.data`.
- `sched.log`: recorder diagnostics.
- `sched-events.txt`: decoded nanosecond timestamps, scheduler fields and stacks.
- `sched-decode.log`: decoder diagnostics.
- `session.json` → `scheduler`: scope identities, status, distributions, stack
  groups, loss-record count and incomplete-interval diagnostics.

Switch records also contain the neighboring task's identity and stacks; wakeup
records can contain waker stacks. Review raw artifacts before sharing. End-of-capture
identity checks exclude exited/reused TIDs from derived results; any exclusions make
the trace partial. The raw file may retain their events. New or short-lived tasks
are not a complete part of this initial-thread-set view.

Detected loss records, loss diagnostics, malformed events, timestamp regressions,
repeated switch-outs or the 200,000-event parser limit suppress derived distributions
and stack totals. An observed-loss count of zero is not proof that all possible
events were captured. Raw and decoded traces each have a 64 MiB soft size limit;
decoding has a five-second timeout. Checks can overshoot between polls. Parsing
retains at most 32 frames per switch-out and 1,024 distinct stack groups plus overflow.

Completed, valid traces add per-run percentiles to `--compare`. Repeated comparison
averages **per-run percentiles**, not a pooled distribution of all events. Missing or
partial traces are reported separately and their scheduler metrics are not added.
Old sessions remain readable. Counts in each original session are essential when
judging percentiles from short captures.

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

The workflow now includes filtered scheduler intervals and switch-out stacks.
Remaining work includes an interactive saved-session comparison browser, automatic
stack diffs, dynamic thread tracking, hardware PMU analysis, block/network request
latency and lock-owner attribution. A futex stack alone does not establish lock
contention or identify its owner. This remains a preview validated on a Linux 5.15
host; broader kernel/perf-version coverage is needed.

References: [Linux scheduler tracepoints](https://github.com/torvalds/linux/blob/master/include/trace/events/sched.h),
[perf script](https://github.com/torvalds/linux/blob/master/tools/perf/Documentation/perf-script.txt),
[Linux namespace IDs](https://github.com/torvalds/linux/blob/master/include/linux/proc_ns.h),
[Linux perf record](https://github.com/torvalds/linux/blob/master/tools/perf/Documentation/perf-record.txt),
[perf security](https://www.kernel.org/doc/html/latest/admin-guide/perf-security.html),
[proc counters](https://www.kernel.org/doc/html/latest/filesystems/proc.html).
