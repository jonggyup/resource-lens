# Validation of 0.1.0

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
- No resource-changing controls exist in this release.

Reproduce the automated checks from the repository root:

```bash
python3 -m unittest discover -s tests -v
python3 -m resourcelens --once --interval 0.3
python3 -m resourcelens --json --count 2 --interval 0.3
python3 -m resourcelens
```
