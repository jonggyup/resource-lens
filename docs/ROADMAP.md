## Usability priorities

See [USABILITY.md](USABILITY.md) for ordered real-world tasks and acceptance checks.
0.7.1 addresses measured Escape/shutdown delays. Next: explain machine capabilities,
make workload navigation stable, then implement verified per-service disk limits.

# 0.7.0

- Focus individual sockets, memory nodes, disks, GPUs and interfaces while keeping
  the overview summaries; add per-device disk and GPU histories.
- Pin a service to its effective CPU set with resource and pressure accounting.
- Add timeline markers, control outcomes and descriptive before/after comparisons.
- Record bounded private JSONL sessions and replay them without live controls.
- Show host/service pressure, quota throttling and exposed thermal counter events.

## Implemented in 0.4.0

Socket/NUMA/core topology tiles and visual service CPU placement are available.
Workloads `p` opens the selector; `U` restores session placement changes.
Governor/hotplug controls from 0.3 remain available. See README for current
capabilities and VALIDATION.md for hardware coverage. Per-process affinity,
exclusive isolation and NUMA memory placement remain outside this release.

# Roadmap

The implementation sequence and acceptance criteria are in
[FEATURE_PLAN.md](FEATURE_PLAN.md).

## 0.1: visualization prototype — implemented

CPU, memory, GPU, disk and network views; top processes; PSI; live histories;
responsive resource cards; keyboard/mouse navigation; text/JSON exports.

## 0.2: workload inspection and temporary CPU controls — implemented preview

- Group visible processes using cgroup v2 and nearest systemd service/scope.
- Group CPU, memory, per-device I/O, pressure and throttling accounting.
- Local/ancestor CPU and memory ceilings, with incomplete-data indicators.
- Workload selection/filter/details and recent timestamped control events.
- Optional `--control`: temporary CPU weight/quota with preview/confirmation,
  matching unit/cgroup/invocation identity, systemd/kernel read-back and Undo.

Actual system-service enforcement and terminal edits were verified on Linux
5.15/systemd 249. User-manager operations need further live validation; this
host has no usable current-user manager. Undo is session-only, best effort for
concurrent writers, and restores values rather than exact runtime-file bytes.

## Next: disk bandwidth and process controls

Per-service, per-device read/write limits with explicit backing-device mapping,
enforcement observations and Undo. Add nice/affinity as clearly separate
per-process operations. Memory budgets follow once their failure/reclaim/OOM
behavior can be presented clearly.

## Monitoring and hardware coverage

- Real AMD/NVIDIA telemetry validation and a dedicated Intel GPU backend.
- NUMA/SMT topology, offline CPUs and shared frequency-policy visualization.
- Large-process-count and long-duration sampler-cost measurements.
- Mount/device mapping and disk latency with reliable interpretation.
- Durable observations and control events, plus before/after reports.

Hardware controls (CPU online state, governor/frequency limits, power settings)
are machine-wide operations. Detect supported interfaces and conflicts with
other power managers before implementing them. GPU/network controls need
separate capability and attribution work.

No release date or broad cross-device compatibility guarantee is implied.
