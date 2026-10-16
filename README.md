# Resource Lens

A live Linux terminal dashboard for CPU, GPU, memory, disks, network, and processes.
See what is busy, how usage changes, and whether workloads are waiting for
resources. Runs over SSH and uses Python's standard library: no runtime Python
packages, daemon, database, or root access required.

**Version 0.1.0 is a monitoring prototype.** Resource controls are planned;
this version does not change priorities, frequencies, CPU online state,
cgroups, or bandwidth limits. It does not claim functionality is unique to Linux tools.

## Run immediately

Requires Linux and Python 3.10+ with `curses` (included by most distribution
Python packages). From this repository directory:

```bash
python3 -m resourcelens
```

From the parent exploration workspace:

```bash
cd /users/jonggyu/tool_explore/projects/resource-lens
python3 -m resourcelens
```

For an optional installed command, in an environment with pip and setuptools:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/resource-lens
```

Installation isn't required for the source command. On Debian/Ubuntu, missing
`venv` support can be supplied by the `python3-venv` package; the source command
also works without it.

## Views

| Key | View | What it shows |
| --- | --- | --- |
| `1` | Overview | Six cards: CPU, memory, disk, GPU, network and busiest processes |
| `2` | CPU | Aggregate and each online logical CPU, I/O wait, steal, load, available frequencies and CPU temperatures |
| `3` | Memory | Used/available RAM, cache, buffers, swap and memory pressure |
| `4` | Disks | Per-device read/write rates, IOPS, active time, in-flight requests, average queue depth and `/` capacity |
| `5` | GPU | Detected GPUs, available utilization, VRAM, temperature and power |
| `6` | Processes | PID, name, CPU, resident memory, nice value and state |
| `7` | Network | Per-interface RX/TX rates and histories, transfer totals, packet rates, link state/speed, errors and drops |

Overview uses six colored cards at 76+ columns and 23+ rows. Each resource has
a prominent current value and a chart; unavailable GPU data has a plain
explanation instead of an empty graph. CPU/memory/disk/GPU detail pages also
have expanded history charts. Colors identify resources rather than declaring
that high utilization is bad. On smaller terminals a compact, scrollable
summary keeps the main values visible. Minimum size: 40 columns by 10 rows.

UTF-8 terminals use block charts and box borders. For ASCII-only terminals:

```bash
python3 -m resourcelens --ascii
```

The selected navigation tab is highlighted. Click a tab when your terminal
supports mouse reporting, or use numbers and Tab. The footer shows the current
scroll position and relevant shortcuts. Process names appear first so they
remain visible on narrow terminals. The overview's busiest-process card is
always sorted by CPU; sorting/filtering applies to the full Tasks page.

| Key | Action |
| --- | --- |
| `Tab` / `Shift-Tab` | Next / previous view |
| Mouse | Click a navigation tab; wheel scrolls detail pages where supported |
| Up/Down, Page Up/Down, Home/End | Scroll detail pages and narrow overview |
| `Space` | Freeze/unfreeze the display; collection continues |
| `c` / `m` / `n` / `p` | Sort processes by CPU / memory / name / PID |
| `/` | Filter processes by name or PID; Enter finishes, Esc clears |
| `?` | Help |
| `q` / `Esc` | Quit (Esc clears an active filter first) |

History runs oldest to newest from left to right, retaining up to 120 displayed
samples. CPU/memory/disk/GPU charts have a fixed 0-100% scale. Network RX and
TX histories instead scale each interface/direction to its visible peak byte
rate, which is labeled next to
the graph; they do not represent link utilization. New samples enter on the
right; startup space on the left is not fabricated history. Missing samples
appear as gaps; entirely missing histories display a plain message. Single-row
ASCII charts use `._-:=+*#%@` intensity; multi-row ASCII charts use `#` height.
An unsupported GPU cannot produce a utilization history. CPU/memory histories show their respective
percentages; disk/GPU histories show the busiest device, whose identity may
change. Sampling defaults to one second. Frozen intervals are omitted from
display history, so it is a sequence of samples rather than a continuous
wall-clock axis.

## Snapshots and recording

```bash
# Text output, also suitable for non-interactive sessions
python3 -m resourcelens --once

# Refresh every half-second
python3 -m resourcelens --interval 0.5

# Record 60 measured samples as JSON Lines (contains host/process names)
python3 -m resourcelens --json --count 60 > resource-samples.jsonl

# Avoid querying the NVIDIA driver
python3 -m resourcelens --no-nvidia
```

`--json` without `--count` streams until interrupted. JSON schema version 1
includes timestamps, measured elapsed seconds, CPU, memory, GPU, disk, network,
process, pressure and temperature data. Unavailable numbers are `null`, not
zero. Text/JSON modes prime the counters and wait one interval before
producing the first measured sample. Interactive mode initially shows N/A for
rates until the second sample. CPU/disk counter resets also produce N/A for
the affected interval.

## GPU support

| Hardware | Backend | Limitations |
| --- | --- | --- |
| AMD with supported amdgpu sysfs counters | `/sys/class/drm/card*/device` and hwmon | Shows only fields the driver exposes; hardware-tested AMD support is still pending |
| NVIDIA | Optional installed `nvidia-smi`, with an 800ms query timeout | Unsupported fields remain N/A; hardware-tested NVIDIA support is still pending |
| Intel, Matrox and other DRM devices | DRM discovery, any compatible readable counters | Utilization is often unavailable; no privileged Intel engine-counter backend yet |

An unsupported GPU is displayed with its reason. A device with no telemetry
is **not** reported as idle. GPU collection runs in the background sampler,
so a bounded NVIDIA query does not block keyboard input. This prototype
does not offer per-process GPU attribution. VRAM counters are not a universal
measurement of shared/integrated GPU memory.

## Understand the numbers

- **CPU:** interval deltas from `/proc/stat`; busy is total minus idle and I/O
  wait, and includes steal. Guest ticks are already included in user/nice and
  are not counted twice. Steal and I/O wait are also shown separately. CPU
  topology here means online logical CPUs; SMT threads are not physical cores.
- **Process CPU:** 100% means one logical CPU, so multithreaded processes can
  exceed 100%. Processes are matched by PID and start time to avoid PID-reuse
  spikes. Brief processes that exit between samples are not captured.
- **Memory:** used = `MemTotal - MemAvailable`. Cache/buffers are contextual
  values, not disjoint pieces to add to used. Process memory is RSS and
  includes shared pages; summing it does not give physical RAM consumption.
- **Disk:** sectors are converted using Linux's 512-byte accounting units.
  Throughput is block-device I/O, not application bytes or page-cache reads.
  Partitions and loop/ram devices are excluded. Stacked devices such as LVM,
  RAID and their underlying disks are shown separately, never summed into a
  misleading system total. Active time is not a saturation score for parallel
  devices. `/` capacity describes only the root filesystem.
- **Pressure:** Linux PSI `some avg10`: the percentage of time in the last
  ten seconds during which at least some tasks were stalled for that resource.
  It is context, not an automatic bottleneck diagnosis.
- **Network:** rates are bytes/second from `/proc/net/dev` counter deltas,
  not bits/second. Totals/errors/drops are cumulative since interface counter
  initialization, not since dashboard startup. The dedicated page includes
  loopback and virtual interfaces; overview shows one interface, preferring
  non-loopback links that are up and then the highest current throughput, plus
  a count of other interfaces. Press `7` for the complete list.
  Traffic can appear at multiple layers (VPN, bridge, veth, physical NIC), so
  interfaces are never summed into a system-wide total. Rates are N/A on first
  discovery, counter reset or a detected replacement via changed interface
  index. Link state is the kernel's operational state (`unknown` is common for
  loopback); it does not prove Internet access. Reported link speed is optional
  metadata in Mb/s, not a traffic cap. No packet capture, IP/MAC addresses,
  per-process network attribution or external probes are used.
- **Scope:** the `/proc` and `/sys` visible to the process. Containers may
  expose host CPU/memory totals alongside a restricted process list. This
  version does not normalize usage against cgroup limits or quotas.
- **Frequency/temperature:** driver-reported samples where available.
  Frequency is not guaranteed to be an independently controllable per-core
  clock. No inferred power or temperature values are invented.

Sampling is sequential, not an atomic machine-wide snapshot. Rates use actual
monotonic elapsed time; the chosen refresh interval is not assumed exact.
High process counts and GPU queries can make sampling slower than requested.
The header marks a stale or failed collector rather than silently presenting
old data as live. Process names are sanitized before terminal rendering;
command arguments and environments are not collected.

## Verification and development

```bash
python3 -m unittest discover -s tests -v
git diff --check
```

Tests cover counter units and resets, guest CPU accounting, process lifecycle,
missing data, GPU parser fixtures, filtering, JSON CLI output, and an actual
pseudo-terminal session with navigation and resize. See
[docs/VALIDATION.md](docs/VALIDATION.md) for measured host verification and
remaining limitations, and [CONTRIBUTING.md](CONTRIBUTING.md) to contribute.

Architecture:

- `resourcelens/collect.py`: Linux telemetry and counter-to-rate calculations.
- `resourcelens/ui.py`: presentation, history, keyboard navigation, sampler.
- `resourcelens/presentation.py`: responsive cards, chart rendering and colors.
- `resourcelens/__main__.py`: CLI and text/JSON modes.

Future resource control should use systemd/cgroup interfaces with capability
detection, effective-setting feedback, and explicit undo. Hardware-wide
controls and per-application allocation must remain distinguishable. See
[docs/ROADMAP.md](docs/ROADMAP.md) and the proposed
[feature sequence](docs/FEATURE_PLAN.md). These controls are for discussion;
they are not implemented in the visualization release.

Metric references: [Linux disk statistics](https://docs.kernel.org/admin-guide/iostats.html),
[Linux PSI](https://docs.kernel.org/accounting/psi.html),
[AMDGPU telemetry](https://docs.kernel.org/gpu/amdgpu/driver-misc.html),
[NVIDIA SMI](https://docs.nvidia.com/deploy/nvidia-smi/index.html),
[Linux network statistics](https://docs.kernel.org/networking/statistics.html).

MIT licensed. No telemetry service or external data upload.
