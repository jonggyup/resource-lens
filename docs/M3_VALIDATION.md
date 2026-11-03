# M3 connected controls and observation validation

M3 connects existing guarded control backends to the target inspector. It adds
shared outcome/history presentation and time-weighted observation. It adds no
new control family and does not broaden the optional helper's policy.

## Implemented workflow

1. Open a process inspector and press **r** or click **Controls**. Review the exact
   inspected group, associated service, manager, lifetime and scope expansion.
2. Enable controls with **C** if needed. This returns to the bound review.
3. Choose weight, quota, placement or disk bandwidth. Each uses its existing fresh
   preview and explicit Apply confirmation. Shared hardware has a separate menu.
4. Inspect the result: blocked, unconfirmed or verified; the requested/previous
   value, read-back, timestamps and restoration receipt are visible. **H** opens
   measurement health from failures as well as successful results.
5. Open **J Changes**, use **n/p** for entries and scroll for observation. The
   inspector Summary also includes the latest related configuration event.
6. Press **u** to review restoration. Only the latest active apply receipt in that
   domain is eligible. Small terminals must scroll through the restoration preview
   before Enter can execute it. Backend identity/conflict checks remain authoritative.

The original numbered views, domain editors, Undo keys and exit acknowledgement
remain available. Disabling controls or exiting does not restore settings.

## Automated evidence

- `python3 -m unittest discover -s tests -v`: **333 passing tests**.
- `tests/test_control_workflow.py`: process-to-service handoff; movement, PID/group
  lifetime/scope binding; changing telemetry without retargeting; permission denial,
  stale preview, restart, timeout; blocked prepare; successful apply/read-back/restore;
  worker verification timestamps and busy-state Escape; external change refusal;
  per-domain LIFO; recording/replay receipt isolation;
  mouse choices and small-terminal restoration review.
- `tests/test_comparison.py`: unequal interval weighting; clipping across boundaries;
  transition exclusion; settling; missing/NaN values; unknown durations; bounded gauge
  holds; cgroup replacement/scope boundaries; new device metrics; duplicate samples;
  later control attempts; zero baseline; backwards verification/observation clocks.
- Existing CPU, disk, placement, hardware and helper tests continue to cover their
  backend-specific guards. All automated policy writes use fixtures or mocks.

## Terminal walkthrough

Run `python3 scripts/verify_controls_ui.py`. The script opens the real curses UI
in a PTY with synthetic telemetry and an in-memory CPU backend. Host systemd commands
and hardware writes are forbidden by guards. It inspects a process, reviews the
wider service scope, prepares quota without writing, applies/verifies, opens Changes,
reviews/restores, returns to the inspector, and resizes from 110x40 to 60x14.

[Stored terminal evidence](m3-controls-ui-evidence.json) records the exact two
fixture writes (`quota=25`, then reset), final reset state, matching-output timings
and exit status. Full-frame redraw instrumentation avoids matching partial curses
diff paints; these local timings are not performance benchmarks. The terminal
walkthrough also runs in `tests/test_terminal.py`.

## Observation semantics and limits

Baseline ends at apply start; post-change begins at verified completion. Transition
time is excluded. Unconfirmed outcomes have no post-change window. Each metric
reports covered seconds out of the full requested window, including the time still
to be collected. Under 80% is incomplete. Rates use interval overlap; RAM/PSI gauges
use left hold capped at 2.5 seconds. Other controls interrupt windows. Raw samples
remain bounded at 600; old unweighted report fields are retained only for compatibility.

The comparison checks recorded cgroup/scope identity. Systemd invocation is checked
during mutation, not sampled continuously afterward. A later restart retaining the
same cgroup, workload changes, interval boundary approximations and shared hardware
can confound comparisons. Resource metrics do not establish latency improvements.

No host resource policy was changed for this milestone's validation. The synthetic
walkthrough does not establish live enforcement, a supported hardware matrix or
independent-user usability. Disposable owned-service enforcement and hardware
transition validation remain separate environment-dependent release checks.
