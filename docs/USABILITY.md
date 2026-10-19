# Cgroup hierarchy browsing (0.10.0)

Applications (`8`) is a systemd/cgroup workload list. Select a group and press Enter
or Right to browse its actual children; `T` starts at the cgroup root. Click a child
or press Enter to enter it. PID rows open individual process details. Left/Backspace
or Parent goes up, and Esc returns to the previous level with selection restored. At the entry group,
Esc returns to Applications; `q` returns there directly from anywhere in the browser. Arrow keys, PgUp/PgDn and mouse wheel navigate longer lists.

Enter always opens the selected item. `D` focuses metric details; `[`/`]` opens
metric tabs directly on narrow terminals. Controls remain in Applications;
the hierarchy browser does not change CPU placement or limits. See README for
collection limits, exact membership semantics and older recording behavior.

---

# Hardware and application workflows (0.9.2)

Start with `python3 -m resourcelens`. Press `1`, then `e` to inspect the machine, or `8`
to investigate a workload. In Applications, use `/` to find a service, arrows to
select it and `[`/`]` to change the detail tab. `D` focuses details for scrolling;
Esc returns to the list. Wide terminals show both panes together.

For CPU placement, select a service and press `o`; CPU tiles show its allowed set.
Shift-click a CPU/core/socket to list groups allowed there. Press `x` in Applications
to clear that relationship. This does not measure which threads executed there.
For storage, focus a disk with `f`, then press `a` for matching active accounting.
Application `d` returns to a mapped whole disk when one is available.

Ordinary left/right clicks on CPU/socket tiles open the existing settings menu.
During CPU placement, tile clicks toggle the selected CPUs; page navigation
is disabled until you finish or cancel placement. In wide Applications, the mouse
wheel selects groups over the list and scrolls details over the detail pane.
Click the detail pane or press `D` to focus it; Esc returns to list selection.

The overview remains on `1`. See the README for full shortcuts, accounting limits,
and the opt-in preview/authorization workflow for resource controls.

---

# 0.8.2 exit responsiveness update

Default Escape recognition is now 1 ms; exit no longer joins the unfinished
read-only collector. Query cancellation and recording finalization remain.
Use ESCDELAY=150 for slower split terminal sequences. See VALIDATION.md for
measured exit and panel-close latency and its practical limits.

# 0.8.1 responsiveness update

Default Escape recognition and read-only worker shutdown waits are now 10 ms
each. Queued keystrokes are batched before redraw and text sanitization uses the
same allowlist with a faster implementation. ESCDELAY remains configurable for
slower split escape sequences. See VALIDATION.md and scripts/benchmark_input.py.

# 0.8.0 delivery status

All six areas below now have an implementation. Capabilities and overhead use
`0`; workload sorts use `c/m/n/i`, process trees `t`, disk control/Undo `b/B`,
preferences `S`, named markers `N`, and redacted reports `R`. Incident capture is
opt-in via `--incidents`. The optional helper requires an administrator's explicit
installation and exact-unit policy; no sudo grant is installed automatically.

Current limits: tree parents use visible PIDs; disk mapping cannot automatically
resolve every layered storage path; incident triggers cover PSI/quota, not application
latency/network errors. Saved settings cover presentation only. Helper operations
cover CPU weight/quota and disk bandwidth, not hotplug/governors/placement.
Per-process nice/affinity, memory limits, named filter collections and hardware
coverage beyond the current host remain future work. See README for exact usage.

# Everyday usability priorities

Resource Lens should make three tasks reliable: find the workload behind a
resource problem, make an understandable temporary change, and review its outcome.
The following are product recommendations, not claims of unique functionality.

| Priority | User task | What to add | Completion check |
| --- | --- | --- | --- |
| 1 | Know what works on this machine | Capability/status page showing supported metrics, missing permissions, inactive controllers, collector age/cost and collection errors | Every unavailable control has a reason and a relevant next step; no automatic elevation |
| 2 | Follow a busy application without losing it | Stable workload selection, configurable sorting, saved filters, process tree, navigation from workload to its devices | Selection survives reorder; restarted services are explicitly distinguished |
| 3 | Keep backups/builds from disrupting interactive work | Per-service disk read/write limits with device mapping, preview, verified application and Undo; process nice/affinity as separate controls | Disposable competing workloads show actual enforcement, with backing-device and inherited-limit explanations |
| 4 | Repeat an investigation | Persist view/filter/chart preferences; name markers; export a compact report with context and optional redaction | Restart restores presentation only, never silently reapplies resource changes; report retains missing-data and sample-count caveats |
| 5 | Catch short incidents | Configurable sustained-pressure/quota/error alerts and bounded recording around an event | Duration thresholds and cooldown prevent noise; missing samples never count as healthy |
| 6 | Tune without running the entire dashboard as root | Narrow authorized helper for supported actions and clear scope/conflict feedback | Monitor stays unprivileged; helper validates identity, allowable settings and stale previews |

Implement capabilities and workload navigation before adding many more tuning
switches. Disk controls are the next substantial resource-management feature.
Memory limits need separate reclaim/OOM design; a generic memory slider would
hide failure modes. Remote use should continue to work over SSH before adding
a fleet agent or server dependency.

## Responsiveness acceptance

- Bare Escape should exit an idle local session in under 250 ms on the reference
  host, including when a read-only collector is stalled. Regression tests allow
  750 ms for scheduling noise; these are local measurements, not network guarantees.
- Arrows, function keys, mouse input and resize must still work. ESCDELAY is
  configurable for split sequences on slower connections.
- Sampling frequency must not gate input. Avoid continuously repainting static
  screens or raising sample rate just to make interaction feel faster.
- Preserve resource-change acknowledgement and recording integrity. Never kill a
  control write halfway through merely to satisfy an exit benchmark.
- Next performance work: measure CPU/RSS, sample duration and input-to-render
  latency at 1k/10k processes and 64/256 CPUs, then cache slow-changing topology
  and separate expensive telemetry sources where measurements justify it.

## Implemented in 0.7.1

Set the default curses Escape wait to 35 ms (respect positive ESCDELAY overrides),
reduce read-only collection shutdown waiting from two seconds to 100 ms, and
skip publication/recording when a collection finishes after shutdown was requested.
Existing confirmation for active settings is retained. A real pseudo-terminal
regression checks both normal and intentionally stalled collection.

The Escape distinction is documented by
[Python curses](https://docs.python.org/3/library/curses.html#curses.set_escdelay).
