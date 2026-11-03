# M2 inspector and evidence validation

Implemented 2026-10-30. M2 connects inspection, initial CPU explanations and capture.
Control previews/observation integration and saved-session browsing remain M3/M4.

## Using the inspector

Open Applications (`8`), select a process and press Enter. Search (`s`) opens the
same inspector for a process. For a service/group, press `I` from the group list;
Enter retains hierarchy browsing, and `D` inside the browser opens its inspector
on Constraints. Group search also opens inspection.

The five sections are Summary, Usage, Constraints, Evidence and Actions. Use
Tab / `[` / `]`, section numbers 1–5, or wide-layout tab clicks. Narrow terminals
show the active section and scrollable content. `?` shows registry-based help.

- Summary separates process use, group quota observations, configuration and gaps.
  Click a finding title or press `e` to inspect evidence.
- Usage labels process and group accounting separately; group counters include
  other members and descendants.
- Constraints shows exact membership, readable limits, their origin and coverage.
- Evidence shows rule/version, counter before/after values, interval bounds,
  collection scope, coverage and interpretation limits. `n`/`p` or clickable
  Next/Previous changes findings.
- Actions lists named operations with disabled reasons. The same definitions feed
  inspector help, footer actions and scoped search results.

`g` opens a process's exact group, `o` its recorded service association, and `a`
the selected evidence's/readable limiting ancestor. These are explicit group
selections, not conversions of process usage into group usage. Esc/Back restores
the previous target, section, selected evidence and scroll position. Exited/moved
or replaced targets require reselection; display labels are not identities.

## Initial explanation rules

Rules consume recorded data and perform no `/proc`, sysfs, systemd, perf or network
reads. Each finding has a stable identity, target, rule/version, evidence references,
window, relevance, limitations and named next actions. There is no healthy/slow score.

| Rule | Required evidence | Interpretation |
| --- | --- | --- |
| Configured quota | Validated readable `cpu.max` and matching parsed quota | Reports the smallest observed ceiling and its source; configuration is not enforcement evidence |
| Local quota activity | Three valid connected positive intervals spanning at least three seconds, stable group lifetime/configuration | Throttling was observed for the exact group, including other members/descendants |
| Ancestor quota activity | Equivalent evidence on a recorded ancestor | Shared activity may constrain a child; it does not attribute delay to the child/process |
| No local increase | A valid latest local interval with zero counter change | Reports only that interval; does not establish absence of other waits or inherited restrictions |
| Insufficient evidence | Missing/stale/invalid/changed identity, warmup/reset or inadequate interval evidence | Explains what cannot yet be concluded |
| Ancestry coverage | Visible-root/partial/unknown ancestry | Hidden or unreadable settings remain unknown |

Duplicate cached observations do not count as extra intervals. Counter resets,
scope/generation changes, configuration changes, inconsistent counters, overlapping
intervals and gaps interrupt persistence. Individual intervals must be positive
and no longer than 2.5 seconds; connected bounds tolerate 50 ms of timestamp
rounding/read timing. These are conservative initial rule settings, not a latency
model. Positive interval counts do not imply continuous throttling throughout
every interval. Kernel file reads are sequential, not an atomic system snapshot.

Counter rates use ms/s and can exceed 1,000 ms/s; they are never clamped into a
wall-time percentage. Host CPU usage or host PSI alone cannot establish a selected
process's throttling. The evidence does not measure request latency or prove cause.

Compact diagnostic history holds at most 120 distinct frames and 2 MiB of encoded
CPU evidence. Missing-context boundaries reset continuity. Replay seeks rebuild
history from the recorded window and use recorded time, producing the same rule
results without consulting the current host. Recorded ancestors can be inspected
from their captured evidence; child limits are excluded when navigating upward.

## Capture workflow

`P` offers CPU usage, CPU + waits, hardware energy, and counters only. Choose
5/10/30 seconds with `[` / `]` or clickable duration buttons. `W` retains the
direct ten-second CPU + waits shortcut. No chooser capture starts before a choice.
Counters-only explicitly disables perf and scheduler tracing. Missing optional
stacks/tracing remain visible; result coverage and counter observations are shown.

Capture and memory inspection return to the same inspector. Snapshot identity and
scope are checked before dispatch; capture backends check live PID/start identity
or cgroup device/inode and recheck during capture. Group paths use the observed
mount mapping rather than concatenating namespace membership blindly. Replay
disables live capture/memory operations with an explanation. Inspector help blocks
capture shortcuts while it is open.

When capture starts from an inspector, `capture-context.json` preserves findings,
request/scope and observation time beside the existing capture artifacts. It is a
private 0600 file describing evidence at capture selection; it is not a continuously
updated diagnosis during the capture. Context-write failures are reported separately
from capture results. This evidence contains identifiers, like the raw capture.

## Verification evidence

All **311 tests pass**. Automated checks cover rules' positive/negative cases, warmup/reset/gaps, duplicate
observations, bounded history, changed scopes/generations, malformed metadata,
multi-CPU throttle-rate units, deterministic replay, ancestor-only context,
keyboard/mouse sections and evidence, stable Back state, bound search actions,
replay capture gating, disabled actions, narrow layouts, duration/mode selection
and private capture-context output. Existing navigation, metrics, controls and PTY
regressions are retained.

A live read-only smoke check resolved the monitoring process and completed a
0.2-second counters-only capture with two counter observations, disabled stack
capture and a private saved context file. Warmup/coverage produced insufficient
evidence rather than an invented restriction verdict. Structured result:
[own-process capture check](m2-live-evidence.json).

The real terminal walkthrough uses synthetic prerecorded counters:

```bash
python3 scripts/verify_inspector_ui.py
python3 -m resourcelens --replay docs/m2-throttling-demo.jsonl --no-config
```

For the manual replay, press `)` to reach the final recorded sample, then `s`,
search `worker`, and Enter. Use `e`, `n`, `g`, and Esc to inspect evidence and
relationships. The recording is deliberately synthetic and contains no live
enforcement claim: [demo recording](m2-throttling-demo.jsonl).

The scripted 100×24 PTY opens Applications from Overview, finds the process,
opens configuration and counter evidence, follows the exact group, returns to
the same evidence, rejects live capture during replay, and exits cleanly. A guard
makes construction of a live collector fail. Results:
[terminal workflow evidence](m2-inspector-ui-evidence.json). Timing is local
key-to-matching-terminal-output, not SSH or physical display latency.

This validates functionality and local interaction with fixtures/own-process reads.
It does not add independent-user usability results, broad hardware coverage,
live quota-enforcement measurements or an application performance verdict.
