# 0.9.1 interaction conflict audit

167 tests pass. Seven new interaction tests verify original left/right CPU and
socket menus, Shift-click application relationships, placement navigation isolation,
application pane wheel/focus behavior, every application's control shortcuts across
all five detail tabs, modal/filter precedence and visible contextual footer actions.

Live terminal checks verified mouse CPU menus, Shift-click relationships, service
placement navigation, and cancelled CPU offline/weight/quota/disk previews. No
resource settings were applied. See the [full audit](INTERACTION_AUDIT.md) and
[terminal evidence](interaction-audit-ui-evidence.json).

Three local PTY trials measured navigation at 1.48–3.88 ms and Escape-to-exit at
10.0–10.9 ms. [Raw measurements](interaction-audit-latency-evidence.json).
These are local observations, not remote display guarantees.

---

# 0.9.0 workspace validation

160 tests pass. New coverage checks workspace navigation and preserved overview
state, CPU tile clicks and allowed-set filtering, exact disk identity and active counters, missing
attribution, all detail tabs, cgroup replacement history reset, and clickable
split-pane tabs with list rows kept above the footer.

Live read-only tmux checks exercised Hardware, Applications, all five tabs,
CPU/memory relationships, clearing relationship filters, narrow detail navigation
and resizing between 140x45, 80x30 and 40x20.
[Captured terminal evidence](workspaces-ui-evidence.json).

Three real PTY trials measured existing navigation at 1.4–3.7 ms, 40-character
bursts at 0.91–0.98 ms, Escape-to-frame at 3.65–3.77 ms, and Escape-to-exit at
9.7–11.0 ms. [Raw measurements](workspaces-latency-evidence.json).
These are local observations, not guarantees or measurements of remote display
latency. The benchmark covers existing navigation and editors; it does not
establish application rendering costs on hosts with thousands of groups.

This release adds presentation and observed accounting relationships, not new
per-core execution, GPU/network application attribution or NUMA residency collection.
Disk relationships do not resolve storage layers. No live hardware settings were
changed for the workspace verification. An isolated package installation reports
version 0.9.0 and successfully collects an installed-command `--once` snapshot.

---

# 0.8.2 exit responsiveness validation

153 tests pass, including a tightened 250 ms real-terminal exit regression with
live/stalled collection, split Escape-prefixed keys under an explicit ESCDELAY
override, mouse input and unchanged control confirmation.

Three local PTY trials per version:

| Measurement | 0.8.1 | 0.8.2 |
| --- | --- | --- |
| Escape closing a filter to terminal flush | 12.8–13.1 ms | 3.79–3.86 ms |
| Escape to process exit, same benchmark | 18.6–19.0 ms | 9.8–10.9 ms |
| Stalled-collector exit, separate probe | 31–36 ms | about 12 ms |

[Raw evidence](exit-latency-082-evidence.json). Ordinary navigation remains roughly
1.5–3.8 ms and typing bursts remain under 1 ms; this change specifically removes
Escape/shutdown waits. Measurements are local observations, not hard deadlines.
The 1 ms recognition default prioritizes local responsiveness; increase ESCDELAY
for connections that split terminal sequences across longer delays. Resource-change
acknowledgement and recording finalization remain; slow storage can still delay exit.

---

# 0.8.1 input responsiveness validation

153 tests pass. Added coverage proves bounded input batches preserve every key,
stop at exit, defer mouse handling until the new layout is rendered, and preserve
the terminal text allowlist across all 65,536 BMP codepoints. Existing split-key,
mouse, confirmation, resize and live/stalled-collector exit tests still pass.

Three local PTY trials per version:

| Measurement | 0.8.0 | 0.8.1 |
| --- | --- | --- |
| Navigation key to curses flush | 1.7–4.7 ms | 1.4–3.8 ms |
| 40-character burst to final flush | 20.1–20.7 ms; 40 redraws | 0.88–0.93 ms; one redraw |
| Escape closing filter to flush | 38.6–38.8 ms | 12.6–13.1 ms |
| Escape to process exit, same probe | 43.9–44.3 ms | 19.0–19.4 ms |

A separate deliberately stalled-collector exit probe measured 31–32 ms (previous
release around 144–149 ms). [Raw evidence](input-latency-evidence.json).
Reproduce key-to-flush measurements with `python3 scripts/benchmark_input.py`.
The benchmark uses a real curses application and PTY; it measures terminal flush,
not physical display or network latency. Sampling is set to 60 seconds to isolate
input/render cost. Results are observations, not hard latency bounds.

---

# 0.8.0 usability and control validation

149 automated tests pass. New checks cover presentation-only preference validation,
private/atomic saving, redacted reports, missing/reordered/replaced workload identity,
process-tree cycles and depth, bounded sustained incident capture, missing-sample
resets, disk stale-preview/Undo conflicts and false-success handling, clipped
confirmation, helper policy/receipt ownership and recovery after interrupted apply.

Live checks:

- [Terminal evidence](usability-ui-evidence.json): capability view, process tree,
  selected-service disk preview/apply/Undo, named marker, report export and preference
  restoration on a second launch, plus live incident-to-Timeline delivery and replay after early exit. Temporary XDG directories kept user settings intact.
- [Disk enforcement](disk-enforcement-evidence.json): a disposable service wrote
  8 MiB to its own temporary file with direct I/O. Baseline 0.091 s; a 2 MiB/s
  limit took 3.981 s; after verified Undo, 0.064 s. `io.max` and `io.stat` agreed.
  No raw device writes occurred. This demonstrates this host/device workload,
  not every buffered or layered storage configuration.
- [Incident evidence](incident-live-evidence.json): a disposable service limited
  to 1% CPU produced sustained CPU PSI. The live CLI captured 52 samples, an alert
  and 10.04 seconds after the trigger, in a replayable 0600 file. Quota trigger
  logic and missing signals additionally have fixture coverage. The service was stopped.
- [Helper evidence](helper-live-evidence.json): real CPU/disk prepare/apply/Undo
  through the privileged dispatcher, exact-unit policy and foreign-receipt refusal.
  The isolated archive refused privileged execution as an unprivileged user.
  No persistent sudo policy was installed; administrator-specific authentication
  remains deployment validation.

External telemetry query cancellation is tested with a real sleeping subprocess.
The full suite retains local Escape latency, split-key/mouse and resize checks.
General recording, replay and installed-package checks remain part of release validation.
No collection-overhead guarantee for 1k/10k-process or 64/256-CPU machines is made.

---

# 0.7.1 responsiveness validation

Local PTY measurements, three trials per case, from bare Escape to process exit:

| Case | 0.7.0 | 0.7.1 |
| --- | --- | --- |
| Live collection | 1011–1014 ms (rounded) | 46–48 ms |
| Collector deliberately stalled for 5 seconds | 3012–3014 ms | 145–146 ms |

[Raw timings and method](exit-latency-evidence.json). These measure this host,
not remote-network latency. Recording was off; blocking storage writes can still
hold recording finalization. Resource-control confirmation remains in place.
131 tests pass, including an actual terminal regression for normal/stalled
collection exit under 750 ms, split arrow/F1 decoding with an ESCDELAY override, existing mouse menus, navigation and resizing.

---

# 0.7.0 investigation validation

129 tests pass, including bounded/exclusive recordings, interrupted-tail recovery,
replay without a collector, control rejection, per-device histories and identity
handling, service overlay identity guards, sampled comparisons, markers, failed
control events and thermal counter increments/reset/missing values.

A live tmux session exercised socket focus, disk focus/history, service overlay,
manual baseline, verified CPU placement and Undo on a disposable systemd service,
Timeline, recording and read-only replay. Replay was resized to 80x24, 40x12 and
120x40. See [captured evidence](investigation-ui-evidence.json). The test service
was stopped and its runtime settings cleaned up. No existing service was changed.
The overlay correctly reported unavailable effective cpuset information before
placement; known-set highlighting also has fixture coverage.

The installed 0.7.0 package was checked from outside the source directory:
version, two-sample live recording, identical replay JSON and replay text export.
The full automated suite includes real pseudo-terminal navigation checks.

Limitations: this host exposes no cpufreq governor policies or GPU utilization.
Thermal zero/missing behavior was observed; nonzero/reset transitions use fixtures.
Comparisons are sampled observations, not controlled performance experiments;
no latency improvement or causal effect is claimed. Replay reconstructs the last
120 samples at its cursor; live analysis retains up to 600 UI-observed samples.

---

# 0.6.0 navigation validation

114 tests pass. Context help, action-bar bounds and clicks, filter editing,
view aliases, typing isolation and exit acknowledgements have automated coverage.
A live read-only terminal session exercised help via mouse, the view switcher,
network/workload navigation, j/k and 40x12 / 80x24 / 120x35 resizing. See
[navigation captures](navigation-evidence.json) and [design review](TUI_REVIEW.md).

---

# 0.5.0 resource visualization validation

106 automated tests pass. New tests cover NUMA memory parsing, backing/holder
and partition relationships, network master/lower links, missing topology,
responsive panel bounds and retained content, free-versus-available semantics,
overview toggle/click navigation and all expanded sections. The real PTY test
also expands and collapses the overview before navigating existing pages.

A separate live curses session verified mouse expansion/collapse, clicking the
CPU section to open its detail view, returning to the preserved expansion state,
and memory/disk/GPU/network panels. It exercised scrolling and resize at 80x24,
40x12 and 120x35. [Captured screens](resource-panels-evidence.json) record the
actual terminal output. All checks in this release were read-only.

Physical test coverage: one NUMA node, two SATA devices and a Matrox GPU with no
utilization counter. Layered block/network relationships have fixture coverage;
unknown attachment and usage are displayed explicitly. No new hardware support
for GPU utilization, DIMM topology or per-process network attribution is claimed.

---

# 0.4.0 topology and placement validation

101 automated tests pass. New coverage includes socket/SMT grouping, unknown
core identities, responsive tile bounds and click targets, CPU-list parsing,
parent/offline exclusions, stale units, external changes, false API success,
reset versus explicit restoration, small-terminal confirmation blocking and
selection controls. Existing real pseudo-terminal tests include right-clicking
a logical CPU in its new core tile.

Live integration: `python3 scripts/verify_placement.py --run` created only a new,
bounded disposable service and slice. Both service processes were constrained
to CPU 0, then regained CPUs 0-7 after Undo. A temporary parent restriction to
CPUs 0-1 rejected selection of CPU 7. A second change restored an explicit 0-1
set. See [kernel evidence](placement-evidence.json). Created units/drop-ins were
cleaned up. No existing service's configuration or hardware online state changed.

The actual curses UI was driven through service selection, topology editing,
preview, Apply and Undo, and resized to 80x24, 40x12 and 120x35. See
[UI evidence](placement-ui-evidence.json) and [CPU view](CPU_TOPOLOGY.txt).
Multiple sockets/NUMA nodes and unusual SMT layouts have fixture coverage;
the available physical host has one reported socket, one NUMA node and two
threads per core. This is not validation on every kernel/systemd version.

---

# 0.3.0 CPU hardware control validation

85 automated tests pass, including an actual curses pseudo-terminal session
opening the bulk menu and right-clicking CPU 0 using SGR mouse input. The full
existing telemetry/workload/controls suite also passes. New fixture tests cover
policy deduplication, shared-policy scope, socket scope, unavailable governors,
stale previews, partial writes and restoration, external conflicts, hotplug
switch absence, last-online-CPU protection, read-only gating and small previews.

A real JSON sample on the development host reports eight present/online logical
CPUs, socket 1, four physical cores and no cpufreq policies. Possible CPUs 8-31
are correctly excluded. No host governor changes were possible; no live CPU was
offlined on this working server. Actual hardware governor transitions and hotplug
remain unverified on hardware; fixture tests are not a substitute for that check.

Run: `python3 -m unittest discover -s tests -v` from the project directory.

---

# Validation

## 0.2.0: workload inspection and runtime CPU controls

Executed on Linux **5.15.0-187-generic**, systemd **249** and Python **3.10.12**.
The automated suite now contains **71 tests**, including synthetic cgroup
hierarchies, systemd backends and control-UI state transitions. It does not
mutate host policies. [Structured evidence](cpu-controls-evidence.json) records
the separate, explicitly executed host experiments.

### Actual kernel enforcement

`python3 scripts/verify_cpu_controls.py --run` created two unique transient
services in an owned slice, pinned to the same allowed logical CPU. The first
service had three visible processes and was grouped once. Both initially had
weight 100. Observations over short measured windows:

| Stage | Service A CPU | Service B CPU | A/B ratio |
| --- | --- | --- | --- |
| Baseline, weight 100/100 | 60.12% | 39.95% | 1.50 |
| A changed to weight 400 | 85.72% | 14.38% | 5.96 |
| A capped at 25% of one CPU | 25.76% | 74.34% | 0.35 |
| CPU settings undone | 59.81% | 40.33% | 1.48 |

The direction and reversal of CPU allocation were observed; the weight value
is not a promise of an exact measured ratio. Short windows can include quota
period/burst effects. The quota stage recorded 30 throttled periods and about
2.079 seconds of throttled time in kernel counters. Read-back verified the
requested local quota/weight, not merely a successful command exit.

An additional 50% quota on the owned parent slice with an 80% local quota on
the child was correctly shown as a **50% tightest readable ancestor ceiling**,
with the parent's cgroup path identified. All three session changes were
undone, owned units stopped and their overrides reverted. No owned cgroups
remained. Root controller state was `memory pids` both before and after.
A second enforcement run against the final control guards also passed; its
measurements and complete cleanup result are retained in the structured evidence.

### Actual terminal control workflow and authorization

A dedicated tmux session ran the real `--control` UI. It filtered to one newly
created sleeping service, selected CPU weight 400, displayed/checked that
owned unit's preview, applied and verified it, then used `u` to restore 100.
The same keyboard workflow set quota 25% (`CPUQuotaPerSecUSec=250ms`) and
restored `infinity`. No existing service was used as a mutation target.

A real `nobody` request to change that owned service's weight was denied and
the weight remained 100. Default rootless JSON monitoring also passed with
27 visible workloads. Another owned service started with an unset CPU weight
and no active kernel weight file; applying 400 and Undo restored the original
unset manager setting and inactive kernel state. All temporary units and
runtime override paths were removed; root controller state returned to its
original value in each experiment.

### Failure handling and limits

Regression checks cover stale previews, direct kernel disagreement, an
existing untracked kernel configuration, denied requests, false success,
recoverable manager-only partial changes, PID/group identity boundaries,
unit restart/replaced cgroup rejection, conflict-aware Undo, input validation,
confirmation cancellation, too-small previews, and explicit exit with pending
settings. The PTY test visits Workloads and attempts a disabled control; real
mutation testing remains outside the automatic suite.

The current-user systemd bus is unavailable on this host. User-manager command
routing and ownership detection have fixture coverage, but actual user-service
edits are not yet live-validated. No cgroup v1 control support, per-process
nice/affinity editing, disk/memory mutation, persistent event recording,
durable Undo, or broad distribution validation is claimed. Conflict detection
is best effort rather than an atomic transaction with external writers. Undo
restores observed values rather than the exact runtime-file layout.

Twenty back-to-back complete collector samples (NVIDIA probing disabled)
averaged **68.31ms wall / 68.24ms CPU per sample**, with 181 visible processes
and 27 workloads on this older Xeon host. Ancestor metadata is cached within
each sample. At a one-second interval this measured collection cost is about
6.8% of one logical CPU; shorter intervals increase overhead. This supersedes
the earlier collector-only 0.1 measurement below and excludes UI rendering.

The historical visualization checks follow.

## Visualization redesign

All **40 tests passed** after introducing six responsive overview cards,
multi-row percentage charts, Unicode graphics with `--ascii` fallback,
highlighted/clickable tabs, a busiest-process summary, names-first process
tables and compact small-terminal summaries. The renderer now redraws when
data, input state, terminal size or stale/error status changes, rather than
redrawing unchanged content ten times a second. No new numerical overhead
claim is made for the complete redesigned UI.

New presentation checks cover fixed chart scales, missing samples versus
measured zero, network peak scaling, clipped history windows, card geometry
without overlap/footer collision, narrow process-name/value visibility,
terminal-safe graphics and mouse tab routing. Mouse routing is unit-tested;
the interactive PTY exercises keyboard input and resizing rather than physical
mouse input.

Actual dashboards were inspected through dedicated tmux sessions at 120x35
(Unicode), 80x24 (ASCII) and 40x12 (compact summary). The PTY integration test
now resizes through 80x24, 40x12 and 120x35 during navigation. The packaged
command was reinstalled in the isolated validation environment; from `/tmp`,
its help exposed `--ascii` and its JSON mode returned network/interface data.
This establishes functionality and layout checks, not usability validation
with independent users. Controls were unimplemented in this 0.1 visualization baseline.

## Network monitoring addition

All 33 tests passed after adding network collection, per-interface histories,
an overview strip and the `7` Network page. New fixture checks cover byte-rate
units, metadata, unavailable speed, real zero traffic, counter resets,
interface replacement/removal, malformed/missing counters, independent RX/TX
histories and peak-based scaling. The real PTY navigation test visits the
network page, and JSON output includes the additive `network` field.

An actual 1 MiB TCP transfer to an owned temporary localhost listener delivered
1,048,576 payload bytes. The loopback counters increased by 1,051,452 bytes in
each direction, including protocol overhead; measured RX/TX rates were about
8,078,640 bytes/s during that short interval. The listener and connection were
closed after verification. No external traffic or host network configuration
changes were needed. This verifies interface accounting, not per-process
attribution or physical NIC throughput.

The dashboard was also launched in a dedicated tmux server to inspect the
network overview and detail page on real host interfaces.

## History display correction

Reproduced the reported question-mark history on this host: GPU history
contained only unavailable samples because the Matrox driver has no readable
utilization counter. CPU, memory and disk histories contained actual measured
values. The renderer now displays `N/A (no measured data)` for an entirely
unavailable history, with gaps for occasional missing samples in a measured
history. Added regression checks for missing histories, gaps versus real zero,
visible-window clipping, and measured CPU/memory/disk versus unsupported GPU.
The updated suite contains 27 tests. The original baseline validation follows.

Executed on 2026-10-16 on Linux, Python 3.10.12, Intel Xeon E5530
(8 online logical CPUs), two visible disks and a Matrox mgag200 adapter.

## Iteration 1: calculations and terminal behavior

All **24 automated tests passed**. The suite checks CPU guest-tick accounting, elapsed-time-based
disk rates, 512-byte sectors, counter resets, memory/cache interpretation,
PID reuse and unusual process names, missing metrics, AMD sysfs fixtures,
NVIDIA CSV/timeout/error fixtures, finite JSON, process filtering and freezing.

CLI checks launch a real pseudo-terminal at 120x35, navigate all six pages,
filter/sort processes, toggle help/freeze, resize to 45x12 and quit. Other
checks exercise JSON streaming, invalid intervals and non-terminal errors.

## Iteration 2: actual host and disposable workload

Ran the text snapshot, JSON CLI, and interactive dashboard on actual host
counters. Also ran JSON collection as UID `nobody`, without root privileges.
The unprivileged run saw 8 logical CPUs, 2 disks and 184 processes at the
time of measurement. Process counts naturally vary.

Created an owned child that allocated 64 MiB, wrote and fsynced a disposable
32 MiB file, then performed CPU work. After establishing the process baseline,
the collector measured approximately:

- Child CPU: **100.92%** (one logical CPU), over **0.8224 seconds**.
- Child RSS: **75,542,528 bytes**, including interpreter overhead.
- Initial sda write rate: **74,745,961 bytes/s** over the surrounding sampling
  window. This is a host block-device rate, not isolated child attribution.

The first version of the verification script incorrectly expected a CPU rate
for a newly discovered PID. It was corrected to prime that PID before checking
its rate. The collector correctly returned unavailable on the first sample.
The child was terminated and the temporary file/directory removed.

CPU temperature sensors were readable (roughly 24-27 C during the snapshot).
Frequency counters were unavailable on this host. The Matrox device was
detected with `driver exposes no GPU utilization counter`, rather than
reporting zero utilization.

## Iteration 3: presentation and sampler cost

Launched the actual dashboard in a dedicated tmux server and captured its
rendered 120x35 overview: all four panels were present, with live histories,
CPU/memory figures, disk rates and the unsupported GPU explanation. Also
verified detail navigation and narrow terminal behavior with the PTY suite.

Fifty back-to-back collector samples with NVIDIA probing disabled averaged
**21.05 ms wall time / 21.03 ms CPU time per sample** on this host, with about
184 processes. This measures collection only, excludes TUI rendering, and is
not a guarantee on other systems or at larger process counts. At the default
one-second interval it corresponds to roughly 2.1% of one CPU for collection
under these measured conditions; shorter intervals increase overhead.

## Remaining verification

- AMD/NVIDIA utilization/VRAM/power: parser/backend fixture coverage only;
  actual hardware still needed. Intel GPU utilization has no dedicated backend.
- Only this Linux/Python combination was executed locally; the included CI
  matrix is configuration, not a claim that remote CI has run.
- Source launch and packaged installation both passed. The host has no pip or
  wheel build tooling globally: an isolated virtual environment under the
  workspace's ignored `.eval/resource-lens/` directory was created, a pip wheel
  was fetched from PyPI and checked against its published SHA-256, and pip
  built/installed this project into that environment. The installed
  `resource-lens --json --count 1 --interval 0.2 --no-nvidia` command ran from
  `/tmp`, outside the source tree, and returned valid schema-version-1 data.
- No large-process-count, long-duration leak or broad distribution testing.
- No resource-changing controls existed in the 0.1 baseline.

Reproduce the automated checks from the repository root:

```bash
python3 -m unittest discover -s tests -v
python3 -m resourcelens --once --interval 0.3
python3 -m resourcelens --json --count 2 --interval 0.3
python3 -m resourcelens
```
