# Source and interface cleanup

Reviewed on 2026-11-03. The review covered all 51 production Python modules,
their imports, function and method references, dynamic collector hooks, interface
strings, test callers and helper scripts. Static checks were followed by targeted
source review and terminal tests; a missing direct reference alone was not treated
as proof that code was unused.

## Review scope

| Area | Modules |
| --- | --- |
| Startup | `__init__`, `__main__` |
| Collection and hardware | `collect`, `sources`, `sampling`, `hardware`, `topology`, `resource_topology`, `workloads`, `targets` |
| Interpretation and comparison | `model`, `diagnostics`, `resource_findings`, `comparison`, `investigation`, `impact`, `memory_observation` |
| Resource controls | `controls`, `control_workflow`, `disk_controls`, `memory_controls`, `placement`, `helper` |
| Capture and saved data | `energy`, `profiling`, `scheduler_trace`, `memory_sharing`, `recording`, `evidence`, `redaction`, `sessions`, `incidents` |
| Interface | `ui`, `actions`, `application_processes`, `cgroup_browser`, `focus`, `hardware_ui`, `insights`, `inspector`, `memory_ui`, `navigation`, `placement_ui`, `presentation`, `readability`, `resource_panels`, `sessions_ui`, `usability`, `usability_ui`, `user_guide`, `workspaces` |

## Removed or consolidated

- Four resource-view wrappers and an obsolete network-summary branch. Resource
  pages now use the shared renderer directly; the overview uses its actual cards.
- The older cgroup process/limit renderer, which was unreachable after routing
  those views through the shared inspector.
- Duplicate CPU-set and I/O calculations in pinned-service overlays. These now
  use the same helpers as group views, preserving missing measurements as unknown.
- Repeated finite-number predicates. Comparison, diagnostics and presentation use
  the existing evidence validator, including rejection of booleans and numbers
  too large for finite calculations.
- Five unused imports, an unused action field, an unused focus field and a
  redundant export assignment.
- Repeated mode/shortcut text, unrelated pressure rows on resource pages, and
  healthy source-status messages that obscured measurements.
- Repeated README walkthroughs, an obsolete workspace path and outdated feature
  statements. The README now points to the detailed guides.

## Display rules

- Resource pages show measurements and relevant status. Help and Explain hold
  general definitions; System holds full source and collection details.
- Inline source notes retain errors, partial results, missing observations and
  old readings. Healthy notes are hidden only when their age is at most five
  seconds or twice the source's configured collection interval, whichever is longer.
  This is a display rule, not a change to recorded measurement quality.
- N/A remains distinct from measured zero. Scope, units, counter gaps and coverage
  remain available. Group totals still include children; energy counters remain
  separate when they can overlap.
- Inspector tabs consistently say **Limits**. The system-details view is
  **System (0)**; search still recognizes older/common names such as status,
  capabilities, RAM and tasks.
- **Energy use** describes the capture. Efficiency requires a separately measured
  completed-work count. **Runtime settings** replaces the misleading temporary
  label; previews still explain that settings remain active after exit.
- Process state letters also have plain descriptions. CPU scope labels use CPU
  and socket names rather than Python tuples or internal field names.
- Source labels stay out of CPU energy rows. The earlier multi-socket and
  temperature layout fixes remain in place.

## Preserved behavior

Collection, rate arithmetic, target identity checks, control permissions,
confirmation, kernel verification, conflict-aware Undo and recording formats
remain supported. The collector's dynamically called request/invalidation hooks
and the tested public data contracts were retained. Existing recordings and
preferences retain their machine-readable field names.

Verification uses the full unittest suite, real pseudo-terminal navigation and
resize checks, synthetic control/capture/replay workflows, read-only host previews
and `git diff --check`. Automated controls use fixtures and mock backends.

Results: all 408 tests passed. All ten main views rendered at 40x12, 80x24 and
120x35. A read-only preview retained package and DRAM energy readings for both
sockets on the development host. The import audit found no unused imports across
the 51 production modules and nine helper scripts; `git diff --check` passed.
