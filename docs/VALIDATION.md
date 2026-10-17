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
