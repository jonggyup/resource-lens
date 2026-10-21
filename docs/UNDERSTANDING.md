# Find, understand and compare

Start normally: `python3 -m resourcelens` or the installed `resource-lens`.
All discovery features are read-only and use already collected snapshots. No
additional profiling, root access or external service is needed to open them.

## Find anything: s

Search view names, hardware resources, logical CPUs, interface names, process
names/PIDs and cgroup paths. Search terms are case-insensitive; multiple words
must all match. Arrows move between results; click or Enter opens one. Esc closes.
The existing `:` view-only switcher and `/` list filter remain available.

A process result is checked against both PID and start time; a cgroup is checked
against path and inode. An exited or replaced result is rejected. The result list
is a snapshot: close and reopen s to refresh it. Search covers visible collected
entities, not processes or devices hidden by permissions or namespaces.

## Explain a metric: E

Choose CPU usage, PSI CPU waiting, RSS/PSS, available RAM, disk activity/queue,
network throughput, hardware energy, quota throttling or GPU utilization.
Each explanation contains a meaning, measurement source and interpretation limits.
Enter opens a related resource view; Esc returns to the list. Use arrows or the
mouse wheel if an explanation does not fit.

Relevant kernel references:
- PSI: https://docs.kernel.org/accounting/psi.html
- Process and memory fields: https://docs.kernel.org/filesystems/proc.html
- Disk counters: https://docs.kernel.org/admin-guide/iostats.html
- Cgroup controls: https://docs.kernel.org/admin-guide/cgroup-v2.html
- Energy domains: https://docs.kernel.org/power/powercap/powercap.html

## Measurement health: H

The bottom indicator says Live, Stale, Recorded or No sample. Click it or press H
for individual CPU, memory, pressure, disk, network, GPU and energy measurements.
Select a row to see its source, reason and suggested next step.

A live value can be zero. A missing value is unavailable, never assumed idle.
Live/stale freshness uses the snapshot timestamp and a threshold of max(5 seconds,
three collection intervals); it does not prove when an individual driver updated
its sensor. Replay values are historical, not current permission checks. Explicit
permission errors are identified only when the collector supplies that reason.
Generic unreadable/unsupported counters remain unavailable rather than guessing
why they failed. The panel captures a snapshot; reopen it to refresh.

## Review a change before applying it

Enable controls with C, choose a setting, then read its existing preview.
The Impact summary identifies affected scope. Click Impact or press i for details:

- CPU governor/hotplug: groups whose exposed effective CPU sets overlap affected
  CPUs, unknown sets, and remaining sampled online CPUs for an offline operation.
  A group with no remaining CPUs in its sampled set is explicitly flagged.
- Placement: effective CPU counts before/after, parent/online eligibility and
  observed quota restrictions.
- Service CPU/disk limits: affected membership when reported, effective placement,
  CPU quota ancestry and reported disk limits.

Details are scrollable. Enter or Esc returns to the original preview; it does not
apply anything. Only the existing Apply action performs a change, with existing
permission checks, verification and Undo handling. A preview that does not fit
still cannot be applied until the terminal is enlarged.

Relationships come from collected cgroup data. Only visible groups are included;
unknown placement is counted. Affinity, child limits, CPU hotplug fallback and
changes since collection can alter actual scheduling. No exclusive-core reservation,
latency forecast, exhaustive impact analysis or upgrade recommendation is inferred.
The sample age is shown. Preview evidence does not replace backend revalidation.

## Compare before and after: M, then 9

Mark M immediately around the change of interest. Automatic control events also
appear in Timeline. Collect up to 30 seconds on each side; Up/Down selects an event.
Page Up/Down scrolls results, and Home/End jumps to the ends.

Each metric has Before/After bars on the same per-metric scale, numeric sample
means, available sample counts and absolute/relative changes. Missing values are
excluded; a missing side has no change estimate. A zero baseline has no defined
relative percentage. These are sample averages, not time-weighted integration.
Uneven sampling and short windows can change comparisons. The exact event timestamp
is excluded from both windows, and collection retains at most 600 snapshots.

Host comparisons include CPU, RAM, pressure, disk and interface rates, and exposed
energy-domain watts. Device/interface identities keep replacement histories apart
when the collector supplies them. Devices and energy domains are never summed.
Pinned-service comparisons include service counters and separately labelled hardware
energy; that energy includes other workloads and is not attributed to the service.

A measured difference is not proof that a setting caused it. Neither request latency
nor application throughput is inferred from hardware counters. For controlled runs
with externally supplied work/latency outcomes, use the existing profiling workflow
in [PROFILING.md](PROFILING.md) and [ENERGY.md](ENERGY.md).
