# Resource Lens

A live Linux terminal dashboard for CPU, GPU, memory, disks, network, and processes.
See what is busy, how usage changes, and whether workloads are waiting for
resources. Runs over SSH and uses Python's standard library: no runtime Python
packages, additional daemon or database. Monitoring needs no root access.

**Version 0.8.0 is a preview with optional temporary CPU and disk controls.** Monitoring
is read-only by default. `--control` enables previewed, confirmed CPU weight and
quota changes for supported systemd services/scopes, plus CPU placement on a socket/core topology map,
CPU governors and logical CPU online/offline controls. Per-service disk bandwidth controls are also available. Memory controls, direct frequency
limits and nice/per-process affinity editing remain future work.
It does not claim functionality is unique to Linux tools.

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

## Focus, investigate, and replay

Press `f` on Overview or a resource page to choose a socket, memory node,
disk, GPU, or network interface. Other resources remain summarized on Overview.
Press `[` / `]` to cycle devices of the same kind; `f` restores the full view.
Disk panels show separate activity/read/write histories; GPU panels show busy,
VRAM and power histories when exposed. Percentage charts use 0–100%; throughput,
VRAM and power charts scale to the visible peak. Missing readings stay unknown.

On Workloads (`8`), select a service and press `o` to pin it to the CPU map.
The overlay shows group CPU, memory, I/O, pressure and quota throttling, and
highlights its effective cgroup CPU set. This is allowed placement, not measured
per-core service execution; thread affinity and child cgroups may narrow it.
Press `o` outside Workloads to clear it. Exited/replaced cgroups are not reattached.

Press `M` to mark a baseline; `9` opens Timeline. Up/Down selects an event.
Control apply/Undo attempts also create events with verified or unconfirmed outcomes.
Charts include a separate marker row. Comparisons report available sample means
in the 30 seconds before/after an event, with per-metric counts and absolute changes.
A pinned-service baseline compares that service; otherwise it compares the host.
These are descriptive observations, not causal attribution or latency predictions.
Live comparisons retain up to 600 UI-observed samples; collection can outpace UI updates.

```bash
# Record to a NEW file (existing files are never overwritten).
python3 -m resourcelens --record session.jsonl
# Optional controls and their events can be recorded too.
python3 -m resourcelens --control --record tuning.jsonl --record-max-mb 128
# Replay without live collection or resource controls.
python3 -m resourcelens --replay session.jsonl
# Headless recording and replay export.
python3 -m resourcelens --record batch.jsonl --json --count 30
python3 -m resourcelens --replay batch.jsonl --json
```

Replay: Left/Right steps one sample, `,` / `.` skips ten, `(` / `)` jumps to
start/end, and Space plays/pauses. Playback uses `--interval`, not original timing.
Graphs/comparisons rebuild from up to 120 samples ending at the cursor; advance
past an event to see its after-window. Replay markers are temporary and do not
modify the recording. Replay cannot be combined with `--control` or `--record`.

Recordings contain host/process names, cgroup paths and telemetry; review them
before sharing. Files are created with mode 0600. The default cap is 256 MiB;
reaching it stops recording while monitoring continues. Samples are recorded even
when the live display is frozen. An interrupted final JSON line is skipped with
a warning. The versioned JSONL reader bounds line size and sample/event counts;
recordings should come from Resource Lens, not arbitrary hand-edited JSON.

Resource details show CPU/memory/I/O PSI and kernel-exposed thermal event counters;
the service overlay adds quota throttling. PSI measures time with stalled tasks,
not utilization. Thermal events indicate counter transitions during a sample,
not current throttle duration or a complete machine-wide health assessment.
Missing counters are unknown; repeated package counters are not summed.
See the kernel's [PSI documentation](https://docs.kernel.org/accounting/psi.html)
and [thermal counter documentation](https://cdn.kernel.org/doc/html/latest/admin-guide/thermal/intel_thermal_throttle.html).

## Responsiveness and Escape

Escape closes the current help/menu/filter first; at the top level it exits.
The default escape-sequence wait is 35 ms. For a connection that delivers arrow,
function-key or mouse sequences slowly, increase it explicitly:

```bash
ESCDELAY=150 python3 -m resourcelens
```

`q` / F10 exits without Escape-sequence detection. Active resource changes still
require the existing exit acknowledgement; a control operation in progress is
not cancelled by Escape. Read-only collection runs separately from input and
shutdown waits at most 100 ms for that worker. A final unfinished sample may be
discarded; an in-flight external GPU query is cancelled on exit. A recording write already in progress is allowed to finish, so slow
recording storage can still delay exit. Input wakes the UI immediately; changing
`--interval` changes sampling frequency, not keyboard responsiveness.

See [real-world usability priorities](docs/USABILITY.md) and
[measured responsiveness](docs/VALIDATION.md).

## Everyday workflows (0.8.0)

| Key | Workflow |
| --- | --- |
| `0` | Capabilities, permissions hints, collector wall/CPU time, process peak RSS, errors and missing-data explanations |
| Workloads `c` / `m` / `n` / `i` | Sort by CPU, memory, name or observed I/O |
| Workloads mouse click | Select a row; Enter inspects it |
| Workloads `d` | Open the first accounting device that maps to a visible whole disk |
| Processes `t` | Toggle a process tree; sorting applies within branches |
| `S` | Save presentation preferences explicitly |
| `N` | Name a baseline marker |
| `R` | Export a private, redacted JSON comparison report |
| Workloads `b` / `B` | Preview/apply disk bandwidth / Undo latest disk change |

Selection follows a cgroup identity across sorting. If it exits or is filtered
out, controls require another selection; replacement is reported explicitly.
Process trees use visible parent PIDs; missing parents become roots. Collector
CPU time excludes external helper processes such as nvidia-smi. Peak RSS describes
the entire monitor, not just its collector. These are observed costs, not an
accuracy guarantee at large process counts.

Preferences are read from `$XDG_CONFIG_HOME/resource-lens/preferences.json`
(default `~/.config/resource-lens/preferences.json`). `S` atomically saves view,
process/workload sort/filter, tree and overview expansion. `--no-config` ignores
saved preferences. Control enablement, limits, helper paths and automatic capture
are never restored from presentation preferences.

`R` writes reports under `$XDG_STATE_HOME/resource-lens/reports` (default
`~/.local/state/resource-lens/reports`), with mode 0600. Redacted reports omit host
names, cgroup paths and event labels. Reports retain metric values and timestamps;
review even redacted data before sharing. CLI export can optionally retain identifiers:

```bash
python3 -m resourcelens --replay session.jsonl --report comparison.json
python3 -m resourcelens --replay session.jsonl --report private.json --report-identifiers
```

Report files must be new. Interactive reports use the retained comparison samples;
CLI replay reports use the last 600 samples. Older events without retained samples
remain listed with no invented comparison data. Reports include current aggregate
metrics; a fresh live report has no before/after history yet.

### Per-service disk bandwidth

Launch with `--control`, select a service on Workloads (`8`), and press `b`.
Enter a block device, direction, and rate, for example `sda write 10M`.
`K/M/G` mean powers of 1024 bytes/sec; `max` removes that device's limit in the
chosen direction. Review the device major/minor, backing devices, holders, old
and new settings; Enter or click Apply confirms. `B` undoes the latest disk change.
A clipped preview cannot be applied.

Limits use systemd `IOReadBandwidthMax` / `IOWriteBandwidthMax` and verify `io.max`.
Other device limits are preserved. Preview/apply/Undo reject changed cgroup,
invocation, device identity or settings. Limits affect descendants and remain
active after exit. Undo restores effective values; an explicit unlimited entry
may remain in the runtime systemd configuration. Concurrent writers are detected
when observed, but systemd does not provide a transactional compare-and-set API.

Use whole-disk or device-mapper names exposed under `/dev` (such as `sda` or
`dm-0`), not arbitrary file paths. The correct layer depends on the storage stack;
`d` is a convenience link, not automatic attribution for every layered/partition
layout. Cache hits bypass block I/O; buffered writeback attribution depends on the
filesystem and workload. Read-back verification establishes configured limits;
measure the workload to establish enforcement. See [live enforcement evidence](docs/VALIDATION.md).

### Sustained incident capture

```bash
python3 -m resourcelens --incidents ./incidents \
  --alert-threshold 20 --alert-duration 10 --alert-cooldown 60
```

Opt-in capture triggers when host CPU/memory/I/O PSI avg10 reaches the threshold
for the specified duration, or a visible workload's quota-throttled microseconds
per second / 10000 reaches that numeric threshold. Quota throttling is a cgroup
counter rate, not CPU utilization. These signals may overlap and are not added.
Missing signals or sample gaps reset the sustained interval. A restart changes
workload identity. Defaults: 20 threshold, 10 seconds sustained, 60 seconds cooldown.

Files contain up to 120 pre-trigger samples (also bounded to 8 MiB of serialized
prehistory), the triggering event and 10 seconds after it. Each file caps at
16 MiB; the directory stops creating files at 10 `incident-*.jsonl` files. Move
old captures to re-arm; existing captures are never deleted automatically.
Capture status appears in the footer; triggers appear on the live Timeline. Replay a file with `--replay`.
Recordings contain identifiers, are created mode 0600, and are **not redacted**.
The current implementation watches PSI/quota signals; it does not yet trigger
on application latency or network errors. It needs the monitor running and is
not a background service.

### Unprivileged dashboard with optional helper

Existing systemd authorization remains the default. For an administrator-approved
set of system services, the optional isolated helper performs CPU weight/quota
and disk bandwidth operations while the UI stays unprivileged:

```bash
python3 -m resourcelens --control --helper /usr/local/libexec/resource-lens-helper
```

This requires explicit installation, exact-unit policy and sudo authorization.
Nothing is installed or granted automatically. The helper cannot change CPU
hotplug/governors/placement; those retain their existing authorization paths.
See [helper installation, policy and recovery](docs/HELPER.md).

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
| `0` | Capabilities | Collection cost/health, supported sources, permission hints and missing-data explanations |
| `9` | Timeline | Markers, control outcomes and measured before/after comparisons |
| `8` | Workloads | Grouped service/cgroup CPU, memory, I/O, members, pressure, throttling and inherited limits; optional CPU controls |

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
| `/` on Workloads | Filter workloads by unit name or cgroup path |
| Workloads: Up/Down, Enter, Esc | Select a group, inspect details, return to list |
| Workloads with `--control`: `w`, `l`, `u` | CPU weight, CPU quota, Undo latest session change |
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

## Navigation and contextual help

- **F1 / ?** opens help for the current page or CPU placement editor. Esc, F1
  or ? returns to your previous scroll position. Help cannot execute controls.
- **:** opens a searchable view switcher. Type `cpu`, `ram`, `net`, `services`
  or a view number, use Up/Down, and press Enter (or click a result). Esc cancels.
  This switcher only navigates views; it does not execute shell commands.
- **j/k** scroll or select workloads, alongside arrow keys. Typed filter text
  and view-search text retain ordinary j/k characters.
- **F4 / /** filters processes or workloads. **F10 / q** exits outside editors
  and dialogs, retaining the existing acknowledgement for active settings.
- Bottom action labels are clickable. They adapt to the current context and
  available width; F1 shows the complete help when some buttons cannot fit.
- The status line always identifies **Read-only** or **CONTROL** mode.

These changes follow a review of htop, btop, k9s and lazygit. See
[UI research and adoption decisions](docs/TUI_REVIEW.md) for primary sources,
what was adopted and what was deferred. The original six-card overview remains
the default.

## Expand the overview and inspect resource panels

The default overview keeps its six existing summary cards. Press **e** or click
**[ + Expand overview ]** to switch to a scrollable overview containing CPU
topology, memory nodes, all disks, GPUs and network interfaces, followed by the
top six workloads and the process table. Press **e** again or click Collapse to
restore the summary cards. Expansion state is retained while navigating pages,
but is not saved after exit.

- Scroll with arrows, PgUp/PgDn, Home/End or the mouse wheel.
- Click a summary card or an expanded section heading to open its detail page.
- Press `1` to return to the overview. The expanded view adapts to terminal width;
  it scrolls rather than silently omitting devices.
- The expand/collapse control works in read-only mode and does not change any
  resource settings. CPU controls remain on their dedicated pages.

Resource details now use responsive visual panels:

| Page | Visual information |
| --- | --- |
| `2` CPU | Socket/NUMA/core tiles, SMT threads, utilization, governors, clocks and offline state |
| `3` Memory | RAM used/available, cache and swap, per-NUMA-node memory gauges and local CPU lists |
| `4` Disks | Per-device activity and read/write metrics, queue depth, capacity, backing/holder relationships, partitions and PCI/NUMA attachment |
| `5` GPU | Per-GPU usage and VRAM gauges, temperature, power, clock, driver and available PCI/NUMA attachment |
| `7` Network | Per-interface RX/TX histories, rates, totals, errors/drops, link speed, master/lower links and attachment |

Topology comes from readable sysfs relationships. Unknown PCI or NUMA placement
stays unknown; memory DIMM slots/channels, board layout and network routes are
not inferred. GPU attachment is matched through DRM PCI information when it is
available. Block backing/holder relationships describe kernel device layering,
not filesystem-to-disk attribution. A panel for `/` reports its capacity separately.

RAM used is total minus available; node gauges show total minus free, which
includes reclaimable pages. Cache and available overlap. GPU utilization is
reported device busy time, not a claim about a particular compute engine.
Disk active time is not saturation; stacked disks and network interfaces can
count the same traffic more than once. Each network direction is scaled to its
visible peak, not automatically to link capacity. Missing counters remain N/A.

See [captured terminal panels](docs/resource-panels-evidence.json) for the actual
summary, expanded overview, and resource detail screens used in validation.

## Visual CPU topology and service placement

The CPU page (`2`) groups **socket → NUMA node → physical core → SMT threads**.
Each core is a box containing its logical CPUs, live utilization bars, governor
and reported MHz (in thread-row order). Offline threads remain visible. Linux's
thread-sibling lists determine which CPUs share a core; CPU numbering alone does
not. Die IDs help identify cores on multi-die packages. Unknown topology is shown
as unknown. Tiles wrap to fit the terminal; arrows or the mouse wheel scroll.

For example, this host reports CPU 0 and CPU 4 as siblings:

```text
[ SOCKET 1 ]
  NUMA 0
+ Core 0 / Die 0-----------+ + Core 1 / Die 0-----------+
|   CPU0   [##...]  40%    | |   CPU1   [.....]   0%    |
|   CPU4   [#....]  20%    | |   CPU5   [.....]   0%    |
| gov: N/A                 | | gov: N/A                 |
| MHz: N/A/N/A             | | MHz: N/A/N/A             |
+--------------------------+ +--------------------------+
```

This depicts topology relationships, not physical silicon coordinates or socket
locations on a motherboard. NUMA information is displayed; memory placement is
not changed. See the [captured live terminal](docs/CPU_TOPOLOGY.txt).

To place a service on selected CPUs:

```bash
python3 -m resourcelens --control
```

1. Open Workloads (`8`), select a service/scope with arrows or `/` to filter,
   then press **p**.
2. Click logical CPU rows to toggle them. Click a core header to toggle its
   eligible SMT threads, or a socket header to toggle its eligible CPUs.
   Selected CPUs are highlighted and marked `x`; `-` means eligible/unselected,
   `!` means excluded by parent restrictions or offline.
3. Use the buttons **All**, **None**, **Inherit**, **Preview**, **Cancel**.
   Keyboard equivalents: arrows focus, Space toggles, `a` all eligible, `n` none,
   `r` inherit, Enter previews, Esc cancels. Inherit removes the local restriction;
   selecting All writes an explicit set of the currently eligible CPUs.
4. Review the service, previous set and expected effective set, then Apply.
5. Workload details show the effective cpuset. Press uppercase **U** there to
   undo the latest placement change. Lowercase `u` still undoes weight/quota;
   `u` on CPU undoes hardware changes.

Placement uses systemd's runtime `AllowedCPUs=` and cgroup v2 `cpuset`, covering
existing threads and future members of the service's descendant hierarchy.
The parent cgroup and online CPU set constrain the selection. Per-thread affinity
and child cgroup settings can further narrow execution. The UI does not directly
edit per-thread affinity, but the kernel may adjust affinity when cpusets change.
Undo restores cgroup placement, not an archive of every thread's affinity mask. Placement does not reserve CPUs exclusively, change CPU quota,
or move memory between NUMA nodes. It currently supports systemd services/scopes,
not arbitrary individual processes or container-runtime APIs.

The preview is rechecked before writing, and success requires matching systemd,
`cpuset.cpus`, and `cpuset.cpus.effective` values. Undo refuses unit replacement,
external configuration changes or changed ancestor availability. Requests that
fail verification remain unconfirmed; inspect the effective cpuset before retrying.
Some systemd versions have a reported empty-AllowedCPUs reset issue; a successful
API response alone is never considered successful restoration. Runtime settings
survive UI exit; session Undo history does not. There is no automatic elevation.

References: [systemd resource controls](https://github.com/systemd/systemd/blob/main/man/systemd.resource-control.xml),
[kernel cpuset behavior](https://docs.kernel.org/admin-guide/cgroup-v2.html#cpuset),
[reported systemd reset issue](https://github.com/systemd/systemd/issues/43846).

## CPU governors and online/offline controls

```bash
python3 -m resourcelens --control
```

Press **2** for CPU details. Right-click a logical CPU row inside a core box to open its settings, or
click **All CPUs / sockets settings**. Press **g** for the same menu without a
mouse. Choose with Up/Down + Enter or left-click. The all-CPU menu includes
socket and individual CPU choices; scroll through choices with Up/Down.

- Set a governor for one logical CPU's shared policy, one socket, or all CPUs
  across all sockets. Choices come from the kernel's available governors.
- Bring present logical CPUs online or take them offline. This is scheduler
  hotplug, not physical hardware removal. Taking an entire physical core offline
  requires taking all its SMT threads offline; rows show `S` socket and `C` core IDs.
- Review the affected logical CPUs and old/new settings, then click Apply or
  press Enter. Esc or Cancel leaves settings alone. A shared frequency policy
  can affect CPUs outside the selected socket or CPU; the preview includes them.
- Press **u** on the CPU page to restore the latest hardware change in this
  session. Workloads has its own Undo history. The exit dialog undoes placement
  entries first, then hardware entries, then weight/quota entries.

Hardware writes use the existing permissions on `/sys/devices/system/cpu`.
`--control` does not grant permission and the app never invokes sudo. Standard
unprivileged sessions normally receive permission errors; an administrator must
provide access or run the application with appropriate privileges. Systemd
workload authorization is separate. Containers, virtual machines and some
hardware/drivers expose no governor controls; the UI reports this explicitly.
CPU0 often lacks a hotplug switch. Requests that include a CPU without a required
switch, or that would offline every online CPU, are rejected before writing.

Changes are machine-wide and remain after exiting. No boot configuration is
written. Power management daemons may overwrite governors. Batches are sequential,
not atomic: an error can leave some entries changed. The UI reports partial
failure and retains an Undo record. Undo checks topology and current values and
stops on external conflicts; it is best-effort and cannot eliminate races with
other administrators. Undo history is lost on exit or crash. Hotplug can alter
CPU affinity and driver behavior; Undo restores the CPU setting, not those
secondary effects. All-offline governor policies may disappear from sysfs,
preventing automatic Undo until the topology/policy is restored.

Kernel references: [CPUFreq policies and governors](https://docs.kernel.org/admin-guide/pm/cpufreq.html)
and [CPU hotplug](https://docs.kernel.org/core-api/cpu_hotplug.html).

## Inspect and control a workload

Press `8`, then `/` to find a service by name. Enter completes the filter;
Up/Down selects a row; Enter opens its details; Esc returns to the list.
Processes are grouped using actual cgroup v2 membership, not guessed names.
The nearest systemd service/scope is used where available; unrecognized
membership remains an unattributed/cgroup row.

Accounting comes from the group's kernel counters and includes descendants.
Nested units can appear separately, so rows are **not disjoint and must not be
summed**. PID lists show visible processes assigned to the group; nested units
are listed separately. Details include per-device I/O, group PSI, CPU
throttling, local settings, and the tightest readable ancestor CPU/memory
ceiling with its source. Missing limit files mean unavailable or an inactive
controller, not unlimited. CPU quota is an upper bound, not guaranteed CPU time.

Enable editing deliberately:

```bash
python3 -m resourcelens --control
```

On page `8`, select the intended service/scope:

1. Press `w` for **CPU weight**, or `l` for **CPU quota**.
2. Type a whole number and press Enter to preview the current/requested values,
   exact unit, owning manager, and cgroup scope. Esc cancels without a mutation.
3. Press Enter again to apply. Success requires read-back from **both systemd
   and the kernel**. The request runs in the background while sampling continues.
4. Observe CPU usage/throttling; `u` restores the latest session change, across
   all workloads. Details include timestamped session control events.

Weight is 1-10000, relative to sibling groups under contention; `default`
resets it. Quota is a whole percent, 1-100000: **100% = one logical CPU**, not
the entire machine. `max` removes the local quota; an ancestor may still cap
the group. Controls apply to the entire selected unit and its descendants.

### Authorization, lifetime and Undo

- `systemctl` and systemd with cgroup v2 CPU support are required for controls.
  The selected unit must be active and match the observed cgroup and invocation.
  Unsupported units/groups remain inspectable. Only numeric/default CPU
  weights are editable; an unsupported existing weight mode is refused.
- The UI does not invoke sudo/pkexec or install a privileged helper. It uses
  systemd's existing authorization through `systemctl --no-ask-password`.
  Current-user services use the user manager; system services use the system
  manager. Other users' user-manager units remain read-only. An unavailable
  bus or denied change produces an error, not a success indicator. Use an
  account/policy authorized to manage the intended unit; the app does not
  grant authorization itself.
- Changes use **`--runtime`**. They survive closing the UI and can survive
  restarting the service; they remain until changed, removed, or rebooted.
  They are not persistent configuration in `/etc`. Exiting with outstanding
  changes asks explicitly whether to keep them.
- Undo records are **in memory for the current UI session only**. Undo refuses
  a detected unit restart, replaced cgroup, or conflicting current setting.
  It restores the previous manager value and verifies observable kernel
  state. systemd may retain runtime reset/override files or enabled controllers;
  Undo does not promise byte-for-byte restoration of runtime configuration
  files. Do not use `systemctl revert` on a real unit just to remove our one
  property: it can remove unrelated overrides.
- Conflict checks are best effort: systemd exposes no compare-and-swap for
  this workflow, so an external writer can race between inspection and the
  mutation. Run one tuning authority per workload when changing settings.
  An existing disagreement between manager settings and readable kernel
  settings is refused before editing, to avoid overwriting untracked tuning.
- A failed or timed-out request can leave uncertain state; its event remains
  `unconfirmed`. Inspect the current setting before retrying. Undo can recover
  an unchanged/manager-only partial request when observations still match;
  conflicting state is refused rather than overwritten. Force-interrupting
  the UI does not automatically restore changes.

This release does not regroup unmanaged processes or edit parent slices. A
small terminal that cannot show the full target preview cannot confirm a change.

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
process, workload, pressure and temperature data. Unavailable numbers are `null`, not
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
  version shows workload limits separately and does not normalize host
  percentage charts against a selected cgroup's limits or quotas.
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

The automated suite uses synthetic/mocked control backends and never changes
host resource policies. An **optional explicit host integration experiment**
creates two uniquely named transient services and an owned slice, runs bounded
CPU work, changes/undoes their settings, then stops/reverts only its own units:

```bash
# Requires root to create the disposable system services; about 15 seconds.
python3 scripts/verify_cpu_controls.py --run
```

It is not part of CI or ordinary startup. See validation for measured results
and cleanup evidence.

Tests cover counter units and resets, guest CPU accounting, process lifecycle,
missing data, GPU parser fixtures, filtering, JSON CLI output, and an actual
pseudo-terminal session with navigation and resize. See
[docs/VALIDATION.md](docs/VALIDATION.md) for measured host verification and
remaining limitations, and [CONTRIBUTING.md](CONTRIBUTING.md) to contribute.

Architecture:

- `resourcelens/collect.py`: Linux telemetry and counter-to-rate calculations.
- `resourcelens/ui.py`: presentation, history, keyboard navigation, sampler.
- `resourcelens/presentation.py`: responsive cards, chart rendering and colors.
- `resourcelens/workloads.py`: cgroup membership, accounting and inherited limits.
- `resourcelens/controls.py`: systemd runtime operations, verification and session Undo.
- `resourcelens/__main__.py`: CLI and text/JSON modes.

Further resource controls should retain capability detection, effective-setting
feedback and explicit Undo. Further hardware controls and per-application
allocation must remain distinguishable. See
[docs/ROADMAP.md](docs/ROADMAP.md) and the proposed
[feature sequence](docs/FEATURE_PLAN.md), which distinguishes the implemented
CPU milestone from remaining plans.

Metric references: [Linux disk statistics](https://docs.kernel.org/admin-guide/iostats.html),
[Linux PSI](https://docs.kernel.org/accounting/psi.html),
[AMDGPU telemetry](https://docs.kernel.org/gpu/amdgpu/driver-misc.html),
[NVIDIA SMI](https://docs.nvidia.com/deploy/nvidia-smi/index.html),
[Linux network statistics](https://docs.kernel.org/networking/statistics.html).

MIT licensed. No telemetry service or external data upload.
