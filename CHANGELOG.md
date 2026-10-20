# Unreleased

- Group help by current task, with highlighted headings and keys, aligned descriptions,
  and a stacked layout for narrow terminals. Simplify the control-mode dialog.

- Use C for the in-app control switch and advertise ?, / and q for help, filtering and exit.
- Keep clickable actions and existing F1/F4/F10 aliases for compatibility.

# 0.15.1

- Launch without feature flags; enable/disable control menus with F2 or the clickable mode indicator.
- Offer in-app enablement from disabled CPU, placement and disk controls.
- Keep explicit per-change confirmation, permissions, Undo history and replay isolation.
- Document existing flag-free energy capture through P → Energy efficiency.

# 0.15.0

- Add read-only powercap/hwmon energy-domain discovery and CPU/Status display.
- Add P → 3 Energy efficiency, --energy-info and --profile --energy capture.
- Save per-domain counter evidence, joules, average watts and sampling coverage.
- Add caller-supplied completed work and per-domain joules/work comparisons.
- Reject guessed rollover/reset intervals, omit incomplete normalized results,
  distinguish unavailable energy from zero, and never sum overlapping domains.
- Keep energy hardware-scoped; no process attribution or automatic power controls.

# 0.14.1

- Replace the ambiguous P action with an Investigate menu: CPU usage or CPU + waits.
- Add mouse selection and explicit mode explanations before collection starts.
- Retain W as a direct CPU + waits shortcut and all CLI capture options.
- Open results with a concise summary; D toggles stacks, diagnostics and coverage.
- Keep failures visible in the summary and cancel/back actions specific to each stage.
- Fix mouse clicks/scrolling crashing the profile overlay due to uninitialized wheel flags.

# 0.14.0

- Add opt-in `W` / `--scheduler` tracing for selected initial process/cgroup threads.
- Use kernel-filtered perf scheduler events to separate off-CPU, sleep-to-wakeup,
  and ready-to-running distributions and show time-weighted switch-out stacks.
- Save raw traces, decoded events, diagnostics, interval coverage and loss markers.
- Suppress derived results for detected event loss, malformed events, discontinuities
  and parser limits; exclude exited/reused threads and reject unsupported namespaces.
- Include valid per-run scheduler percentiles in repeated CLI comparisons.
- Preserve `P` CPU profiling and asynchronous Escape cancellation.

# 0.13.0

- Add asynchronous `P` investigation sessions for selected processes/cgroups.
- Save bounded counter evidence, configuration and optional native perf call stacks.
- Add CLI capture and repeated baseline/candidate comparison with variability,
  externally supplied workload outcomes, and explicit missing/failed evidence.
- Preserve responsive cancellation, target identity checks and private artifacts.
- Keep scheduler runqueue estimates distinct from blocked-time profiling.

# 0.12.0

- Add on-demand `V` memory inspection for selected processes and cgroup PID details.
- Show RSS/PSS, private/shared resident bars, PSS categories, swap and hugetlb counters.
- List threads sharing a process address space and peers with overlapping file-backed
  mappings, distinguishing shared-mode mappings from private common backing.
- Keep inspection asynchronous, bounded, permission-aware and cancellable; reject
  PID reuse and disable live inspection while replaying recorded data.
- Label physical-page attribution and anonymous sharing relationships as unavailable.

# 0.11.0

- Default Applications to individual processes ranked by CPU usage, highest first.
- Add a clickable `g` process/cgroup toggle and persist the chosen mode with `S`.
- Support process search, CPU/RSS/name/PID sorting and identity-stable selection.
- Rank child cgroups and direct processes together by CPU in the hierarchy browser;
  add CPU/memory/name sorting and keep missing measurements last.
- Keep group controls explicit in cgroup mode; retain group metrics and navigation.

# 0.10.1

- Make Enter/Right open the selected group or process consistently, including replay.
- Make Esc retrace hierarchy navigation with selection restored; `q` returns directly
  to Applications, while Left/Backspace goes to the parent.
- Keep metrics alongside the list; use `D` to focus scrolling and `[`/`]` to open
  metric tabs on narrow terminals. Update buttons, help and documentation.

# 0.10.0

- Add an actual cgroup hierarchy browser: `v`/Right enters a selected workload,
  `T` opens root, Enter/click opens child groups or process details, and Left goes up.
- Show direct processes separately from child cgroups, including visible empty groups.
- Add background collection of cgroup CPU/memory counters with a 2,048-directory
  cap, partial-coverage reporting and identity-aware navigation.
- Preserve existing Applications metrics/control shortcuts and replay compatibility.

# 0.9.2

- Remove redundant H/A shortcuts, workspace buttons and the duplicate Hardware page.
- Keep expanded Overview (`1`, then `e`), Applications (`8`), detail tabs and all controls.
- Migrate saved Hardware-page preferences to expanded Overview.

# 0.9.1

- Restore ordinary CPU/socket left-click settings; keep right-click settings and
  move application relationships to Shift-click (or keyboard `a` for the CPU scope).
- Block workspace mouse navigation during placement editing, matching keyboard
  behavior; hide those buttons while the selector is active.
- Route wide Applications wheel scrolling to the pane under the pointer and
  let a detail-pane click focus detail scrolling.
- Prioritize contextual footer controls over general workspace shortcuts.
- Add interaction-routing regression tests across application tabs, control menus,
  placement, filters, modals and mouse navigation.

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
