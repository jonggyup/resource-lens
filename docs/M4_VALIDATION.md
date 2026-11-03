# M4 saved investigations and broader findings

Implemented on 2026-11-02. M4 adds a bounded Sessions browser, isolated offline
replay, saved profile/repeat comparison, measured-outcome sidecars, redacted exports
and scoped pressure/memory-event findings. It adds no resource-changing backend.

## Verification

`python3 -m unittest discover -s tests -v`: **368 passing tests**.

New artifact/UI checks cover bounded discovery and malformed indexes; explicit
imports without moving originals; regular-file/size/schema/nested-type validation;
NaN/depth/duplicate-key rejection; filesystem/content changes; outcome units/provenance/windows;
private atomic metadata; interrupted writes; incomplete captures; process/cgroup
scope incompatibility; repeat statistics; alias relationships and numeric/unit
preservation; byte-bounded replay; sparse legacy recordings; keyboard/mouse/small
layouts; playback/Back state; immediate cancellation and no live operation through
saved targets. Fully offline CLI startup forbids live collector construction.

Resource-rule checks cover local and ancestor CPU/memory/I/O pressure, distinct
consecutive observations, source staleness, missing reads, gaps, membership/scope/
cgroup lifetime changes, memory counter resets and local/subtree accounting. Memory
findings remain available when CPU quota evidence is unavailable. Incident capture
preserves original rule versions, deduplicates cached memory observations, respects
cooldown/file budgets and reproduces the same finding from the saved snapshots.

## Real terminal, synthetic evidence

Run `python3 scripts/verify_sessions_ui.py`. The script opens the actual curses UI
fully offline with synthetic profiles and an incident. It forbids live collection,
profiling, systemd inspection/commands and hardware access. The walkthrough:

1. Opens a saved incident, finds its process and reads the captured versioned I/O
   pressure finding in Changes.
2. Returns to Sessions and opens a baseline profile; adds a measured outcome in
   explicit units/provenance, separately from the raw capture.
3. Opens the candidate, adds its outcome and compares without CLI comparison commands.
4. Exports a redacted summary, resizes from 110x40 to 60x14 and exits cleanly.
5. Checks the exact synthetic CPU comparison, absence of raw paths/provenance in
   the export and unchanged original profile bytes.

[Stored terminal evidence](m4-sessions-ui-evidence.json) contains matching-output
timings and exit status. Full-frame redraw instrumentation avoids partial curses
paint matching; these are local checks, not performance benchmarks. The walkthrough
also runs in the terminal test suite. Existing M2/M3 terminal scripts remain regression
checks for inspector/control flows.

## Rule semantics and limits

- Pressure rules use selected cgroup `*.pressure` `some.avg10`, at least 20% in
  three connected source intervals spanning at least three seconds, each interval
  at most 2.5 seconds. Cached copies do not count as new observations. The exact
  group is observed evidence; the nearest ancestor is possible/shared evidence.
- PSI is a smoothed resource-pressure measure, not a per-process/request latency
  measure or a diagnosis of a specific limiting device/configuration.
- Memory rules report valid cumulative counter increases for `high`, `max`, `oom`
  and `oom_kill`, with the actual measured interval. They prefer `memory.events.local`
  and otherwise require known local/subtree accounting. A recorded increase does
  not establish ongoing pressure, a responsible PID or a future outcome.
- Incident thresholds use explicit units: host/selected PSI percent, quota throttle
  milliseconds per second and memory event counts. CLI defaults are 20%, 200 ms/s
  and one event. Quota-counter rates are not clamped to a wall-time percentage.
- Comparison is descriptive. Units, resource identities, capture status/coverage,
  repeat counts and environment differences are visible; raw numeric outcomes
  without structured units remain legacy evidence. No application speedup is inferred.

The tests and terminal evidence are synthetic or read-only. They do not establish
live enforcement, a broad hardware support matrix, independent-user usability or
measured application latency. M5 control expansion still needs its own scoped
authorization, read-back, restoration and disposable-workload validation.
