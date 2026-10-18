# 0.9.0

- Add linked Hardware (`H`) and Applications (`A`/`8`) workspaces; preserve Overview.
- Add a responsive application list/detail layout with Usage, Processes, Placement,
  Limits and History tabs, keyboard navigation and clickable targets.
- Link CPU tiles to allowed application groups, memory to accounted groups, and
  focused disks to groups with matching active I/O accounting. Retain application
  links to the CPU placement overlay and mapped disks.
- Reset CPU history on observed cgroup replacement. Label unavailable attribution
  and distinguish allowed placement from execution.
- Preserve asynchronous collection, fast input handling and existing control safeguards.

# 0.8.2

- Reduce default Escape recognition from 10 ms to 1 ms, retaining ESCDELAY overrides.
- Remove the read-only worker join delay; retain query cancellation, recording
  finalization and resource-change acknowledgement.
- Tighten the real-terminal live/stalled Escape exit regression to 250 ms.

# 0.8.1

- Batch queued keyboard input before redraw, bounded to 64 events / 4 ms of handling.
- Preserve event order and redraw boundaries for mouse/resize events.
- Move terminal text sanitization to the regex engine without changing its allowlist.
- Lower default ESCDELAY to 10 ms and read-only worker shutdown waiting to 10 ms.
- Add a reproducible real-PTY key-to-flush benchmark and input regression tests.

# 0.8.0

- Add a Capabilities page with collection wall/CPU time, peak RSS, permissions hints,
  source availability and collector errors.
- Add workload CPU/RAM/name/I/O sorting, stable selection with replacement notices,
  process trees and workload-to-disk navigation.
- Save presentation preferences explicitly; name markers and export redacted reports.
- Add verified per-service disk read/write limits with preview, conflict checks and Undo.
- Capture sustained PSI/quota incidents with bounded prehistory, posthistory and file budgets.
- Add an optional isolated privileged helper with exact-unit policy, root-owned
  receipts, restricted CPU/disk operations and interrupted-attempt recovery.
- Retain 35 ms Escape recognition and bounded read-only collector shutdown;
  cancel an in-flight read-only GPU query on exit.

# 0.7.1

- Reduce default Escape recognition delay to 35 ms; honor positive ESCDELAY overrides.
- Bound read-only collector shutdown waiting to 100 ms and discard late samples.
- Add real-terminal exit-latency regression coverage and a usability priority plan.

# 0.7.0

- Focus individual sockets, memory nodes, disks, GPUs and interfaces while keeping
  the overview summaries; add per-device disk and GPU histories.
- Pin a service to its effective CPU set with resource and pressure accounting.
- Add timeline markers, control outcomes and descriptive before/after comparisons.
- Record bounded private JSONL sessions and replay them without live controls.
- Show host/service pressure, quota throttling and exposed thermal counter events.

# 0.6.0

- Adopt established TUI interaction patterns after reviewing htop, btop, k9s and
  lazygit: clickable contextual action bar, F1/help, F4/filter, F10/quit and j/k.
- Add a searchable `:` view switcher with aliases and keyboard/mouse selection.
- Context help returns to the previous position and blocks resource actions.
- Show read-only/control mode on every page; preserve existing overview layouts.

# 0.5.0

- Keep the six-card overview by default; add mouse/`e` expansion into a scrollable
  topology and resource overview, plus clickable cards/section navigation.
- Responsive memory, disk, GPU and network panels with gauges and clear metrics.
- Read NUMA memory nodes, block backing/holder/partition relationships, network
  master/lower links and available PCI/NUMA attachment from sysfs.
- Additive JSON `resource_topology` field; monitoring remains read-only.

# 0.4.0

- Responsive socket/NUMA/core topology tiles with SMT siblings, utilization,
  governor, clocks and offline state; preserve right-click hardware menus.
- Workloads: `p` opens visual service CPU placement; click threads, core headers
  or socket headers, preview, apply and verify systemd/cgroup settings.
- Parent-aware selection, explicit versus inherited sets, session placement Undo
  (`U`), and conflict/restart protection.
- Live disposable-service tests verified placement and both forms of restoration.

# 0.3.0

- CPU topology with present/offline CPUs, sockets and physical core IDs.
- Right-click CPU menus and mouse/keyboard bulk governor and hotplug controls.
- Shared-policy previews, verified writes, partial-failure recovery and session Undo.
- Hardware support and permissions are detected from sysfs; monitoring stays read-only by default.

# Changelog

## 0.2.0

- Add Workloads page (`8`) with cgroup/systemd grouping, accounting, inherited
  limits, member processes, I/O and throttling/pressure details.
- Add optional `--control` with temporary CPU weight/quota editing (`w`/`l`),
  exact-target preview, confirmation, background requests and systemd/kernel
  verification.
- Add conflict-aware session Undo (`u`), timestamped control events and explicit
  exit handling when changes remain active.
- Record actual enforcement, terminal workflow, permission denial and cleanup
  verification using exclusively disposable systemd units.
- Include workload data in JSON snapshots. Monitoring is still read-only by
  default; user-manager control support requires additional live host testing.

## 0.1.0

- Initial live CPU/GPU/memory/disk dashboard, process view and JSON recording.
- Add per-interface network rates, histories, counters and link information.
- Replace unknown history question marks with unavailable-data messages.
- Add resource cards, readable charts, compact layout, ASCII fallback and tabs.
