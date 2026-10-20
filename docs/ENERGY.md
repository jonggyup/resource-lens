# Energy efficiency (0.15.0 preview)

This first energy release measures hardware domains during a workload window.
It does not measure energy owned by a PID/cgroup, predict savings, or produce a
whole-machine total. It never adds overlapping package/subdomain readings.

## Use the interface

In Applications (`8`), select a process or cgroup and press **P → 3 Energy
efficiency**. You can click the third choice instead. The ten-second background
capture saves hardware-reported counter samples, interval joules and average watts
for each exposed domain. The selected application's ordinary counters provide
workload context. CPU stack sampling and scheduler tracing are disabled in this
mode to avoid their extra measurement overhead. Escape cancels immediately.

The CPU page (`2`) shows up to three discovered domains and interval-average power.
Status (`0`) lists the full inventory, source, sysfs identifier, parent directory
and unavailable reasons. These domains can include non-CPU devices; labels come
from the driver. The display does not pretend to know their physical scope beyond
what the driver exposes. Record/replay retains the energy snapshot without reading
live sensors during replay.

## Discover and capture from the CLI

```bash
python3 -m resourcelens --energy-info
python3 -m resourcelens --profile ./baseline/run1 --pid 1234 \
  --duration 10 --energy --no-perf
```

Replace 1234 with your target PID. `--cgroup /sys/fs/cgroup/...` is also supported.
`--energy` adds measurement to the selected profiling mode; without `--no-perf`,
CPU stack sampling still runs. The UI energy choice disables it automatically.

For a known completed workload, attach its count and unit:

```bash
python3 -m resourcelens --profile ./candidate/run1 --pid 1234 \
  --duration 10 --energy --no-perf --work-units 1000 --work-unit request \
  --outcome p99_ms=12.5
python3 -m resourcelens --compare ./baseline ./candidate
```

Counts must describe work actually completed within the measurement window, not
submitted work or a target rate. Counts/outcomes are caller-supplied metadata;
Resource Lens does not run the workload or independently verify them. Use matched
fixed-work trials or an external benchmark harness to align the count with the
window. Interactive captures do not yet accept completed-work counts; they show
joules/work as unavailable. This release has no post-capture count editor.

Supply counts to both variants for per-work comparison. Repeat into new run
directories to show variability. Use the same domain, host, workload and work unit.
Joules/work is computed separately for each complete domain; partial data cannot
produce a normalized result. Different unit labels suppress normalized comparisons;
different host names suppress hardware-energy comparisons. Domain identifiers map
metric keys to sources/labels in the comparison's `energy_domains` field.

Host-name equality is a guard, not proof of identical hardware. Inspect the saved
hardware/configuration context and sensor identity before interpreting a comparison.
Compare throughput and latency outcomes alongside energy. A slower run can reduce
watts while consuming more joules per completed job. Different capture durations
also make raw joule totals unsuitable for direct efficiency comparisons unless they
represent the same completed work.

## Sources, bounds and interpretation

Supported sources in this release:

- Linux powercap `energy_uj` counters, with `max_energy_range_uj` when exposed.
- hwmon `energy*_input` counters (microjoules), with driver/sensor labels.

Canonical paths deduplicate sysfs aliases. Different interfaces exposing the same
physical domain can still overlap, so they remain separate. Parent paths preserve
the powercap hierarchy; there is no inferred per-core or per-DIMM allocation.

For a valid interval: `joules = counter_delta / 1,000,000` and
`average_watts = joules / elapsed_seconds`. The interval uses a monotonic timestamp
per sensor. These are differences in hardware-reported counters, not a calibrated
wall-power meter. No power is inferred from CPU utilization or TDP.

A counter decrease is ambiguous between rollover and reset: the interval becomes a
gap rather than assuming a wrap. Missing/invalid counters, changed file identity,
nonpositive elapsed time, and intervals longer than two seconds also become gaps.
Observed energy may still be displayed for valid intervals, but partial domains
have no joules/work metric. Counter resets that remain above the previous value,
or multiple wraps that appear monotonic, cannot be detected from these interfaces
alone. External resets should be avoided during a capture.

The sample loop is roughly 250 ms for captures; the dashboard uses its configured
interval. A dashboard interval over two seconds therefore cannot show valid energy
rates. Sensor update cadence and accuracy are hardware-dependent: polling more
frequently does not create finer physical measurement resolution. Shared package,
DRAM and GPU work from other applications can contribute throughout the window.

Discovery runs once per collector/capture. It is bounded to 256 powercap directories,
128 hwmon devices, 512 entries per directory and 256 energy domains. Restart the
monitor or begin another capture after hotplug/permission changes. Inventory
truncation/errors are reported. Reads never change counters, power caps, device
settings, or permissions. Root-only sensors remain unavailable to an unprivileged
collector; no privilege escalation happens automatically.

An empty/unreadable inventory means **unknown**, not zero. Missing energy does not
make normal process telemetry fail; inspect `energy.status` separately from the
capture status. The normal session JSON contains raw energy samples, domain
summaries, work metadata, host name and measurement scope. Files remain private
under the existing capture rules.

## Validation and next steps

The development host exposes CPU idle-state counters but no supported energy
counters. Live checks validate discovery, the unavailable path, capture/UI flow,
CLI comparison and responsiveness. Controlled sysfs fixtures validate arithmetic,
source deduplication, units, wraps/resets, gaps, missing sensors and normalized
comparisons. Real sensor accuracy and cross-hardware coverage are **not validated**.

Next steps are validation on an energy-capable host, workload-phase markers and
post-capture completed-work entry, GPU-native cumulative energy APIs, idle-state
correlation, and an interactive energy/performance comparison. Power-only sensors,
NVML energy, external meters, idle attribution, automatic tuning and power-cap
controls are not part of this release. Existing GPU power telemetry is unchanged.

Sources: [Linux powercap](https://www.kernel.org/doc/html/latest/power/powercap/powercap.html)
and [hwmon ABI](https://www.kernel.org/doc/html/latest/hwmon/sysfs-interface.html).
