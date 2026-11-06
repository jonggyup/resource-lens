# Resource Lens

Monitor Linux resources, find the processes and services behind their use, and
review settings before changing them. Resource Lens runs in a terminal, including
over SSH. Monitoring starts in read-only mode and needs no root access.

## Start

Requires Linux and Python 3.10+ with `curses`. There are no runtime Python dependencies.
From this project directory:

```bash
python3 -m resourcelens
```

Press **G** (Shift+G) for a task walkthrough, **?** for the current view's shortcuts,
or **s** to find a process, service or device. Tabs, resource cards and bottom
buttons are clickable. Use the mouse wheel or arrows to scroll. Esc goes back or
cancels; **q** exits from a main view.

For an optional installed command:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/resource-lens
```

## Find what is happening

| Task | Start here |
| --- | --- |
| Find a busy resource | Overview; click its card for details |
| Find a process or service | **s**, type a name or PID, then Enter |
| Investigate a slow process | **8 Applications**, select a process, Enter |
| Inspect memory use and sharing | **V** on a process; **m** in its inspector for group memory |
| Understand a number | **E Explain** |
| Diagnose missing or old data | **H Health**; **0 System** for sources and collection details |
| Review service controls | **r** in a process inspector |
| Review applied settings and Undo | **J Changes** |
| Compare before and after | **M** to mark the change, then **9 Timeline** |
| Reopen a recording or profile | **L Sessions** |

Applications starts with processes ranked by CPU usage. Enter opens Summary,
Usage, Limits, Evidence and Actions. **g** on the process list switches to service
groups (cgroups); a group can contain many processes. Group totals include children.
The separate **6 Processes** view includes an optional parent/child tree (**t**).

| Key | View |
| --- | --- |
| 1 | Overview |
| 2 | CPU: sockets, cores, threads, frequency, temperature and energy |
| 3 | Memory: available RAM, cache, swap and NUMA nodes |
| 4 | Disks: traffic, activity, queues and device relationships |
| 5 | GPU: readings provided by the driver |
| 6 | Processes: sortable list or process tree |
| 7 | Network: interface traffic, totals, errors and drops |
| 8 | Applications: process inspection and service groups |
| 9 | Timeline: markers and before/after measurements |
| 0 | System: supported measurements, sources, permissions and collection cost |

**N/A means unavailable, not zero.** Process CPU at 100% means one logical CPU,
so a process using several CPUs can exceed 100%. Old or failed readings retain
an age or explanation. **E** explains measurement meanings and limits; **H** gives
reasons and next steps for missing data.

## Review and change settings

Press **C** to enable control menus. Each change has a target preview and a
separate confirmation. Existing system permissions still apply.

| Setting | Where |
| --- | --- |
| CPU frequency governor or online/offline state | **2 CPU**, then **g** or click a CPU, core or socket |
| Service CPU weight or quota | Service review: **w** weight, **l** quota |
| Allowed CPUs for a service | Service review: **p** placement |
| Service disk bandwidth | Service review: **b** |
| Service MemoryHigh | Service review: **h** |

A service change affects the service and its child groups, including other
processes. CPU placement shares CPU capacity; it does not reserve exclusive cores.
MemoryHigh triggers reclaim and throttling above a threshold; usage can exceed it.
MemoryMax is visible but has no editor. The optional CPU/disk helper does not
authorize memory changes.

**Settings remain active after exit.** Disabling menus does not undo them.
**J Changes** shows verification and Undo availability. Restoration requires the
current session and checks for replaced targets or conflicting settings. Verification
checks the configured value; observe the workload to assess its effect.

See the [user guide](docs/USER_GUIDE.md), [MemoryHigh details](docs/MEMORYHIGH.md),
and [optional helper setup](docs/HELPER.md).

## Capture CPU activity, waits or energy use

Select a process or group in Applications and press **P Investigate**. Choose
**CPU usage**, **CPU + waits**, **Energy use**, or **Counters only**, then a duration.
**W** starts a ten-second CPU + waits capture directly. Esc cancels; **D** opens
capture details. Tracing depends on kernel support and permissions.

CPU shows all exposed energy counters, grouped by socket where the hardware
identifies one. Package power appears beside each socket heading; package and
DRAM readings stay separate. Energy readings include other workloads and can
overlap. They are not energy owned by the selected process.

```bash
python3 -m resourcelens --profile ./baseline/run1 --pid 1234 --duration 10
python3 -m resourcelens --profile ./candidate/run1 --pid 1234 --duration 10
python3 -m resourcelens --compare ./baseline ./candidate
python3 -m resourcelens --energy-info
python3 -m resourcelens --profile ./energy-run --pid 1234 --duration 10 --energy --no-perf
```

Replace 1234 with the target PID. Resource measurements alone do not measure
request latency or prove a speedup. For energy per completed job, supply a measured
work count and unit as described in the [energy guide](docs/ENERGY.md).
See the [profiling guide](docs/PROFILING.md) for permissions and interpretation.

## Save and reopen measurements

```bash
python3 -m resourcelens --once
python3 -m resourcelens --json --count 2 --interval 0.5
python3 -m resourcelens --record ./recording.jsonl
python3 -m resourcelens --replay ./recording.jsonl
python3 -m resourcelens --sessions
```

**L Sessions** reopens saved recordings and profiles. **b** selects a baseline;
**c** compares a candidate. **o** on a complete profile adds a measured application
outcome; **x** exports a redacted summary. **R** exports a dashboard/replay report.
Raw recordings and profile files remain separate from redacted exports.

Replay is read-only and never applies settings or inspects recorded PIDs on the
current machine. See [saved sessions](docs/SESSIONS.md) and
[measurement and comparison details](docs/UNDERSTANDING.md).

## Development and reference

```bash
python3 -m unittest discover -s tests -v
git diff --check
```

Control tests use fixtures and mocks. Ordinary tests do not change host resource
policies. Explicit host integration experiments are separate; see
[validation](docs/VALIDATION.md) before invoking them.

- [Everyday user guide](docs/USER_GUIDE.md)
- [Collection benchmarks](docs/BENCHMARKING.md)
- [Data contracts](docs/DATA_CONTRACTS.md)
- [Improvement blueprint](docs/BLUEPRINT.md)
- [Cleanup review](docs/CLEANUP.md)
- [Contributing](CONTRIBUTING.md)

Version 0.16.1 is a preview. Hardware readings depend on drivers, permissions and
namespace visibility. Counter fixtures verify parsing and arithmetic; they do not
calibrate real sensors. The program uses Python's standard library, with no
required daemon, telemetry service or external data upload. MIT licensed.
