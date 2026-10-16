# Roadmap

The proposed implementation sequence and acceptance criteria are in
[FEATURE_PLAN.md](FEATURE_PLAN.md). Next: group workloads, expose effective CPU
limits, then implement temporary CPU allocation controls with verification and
Undo. These are discussion/planning items, not implemented controls.

## 0.1: visualization prototype

CPU, memory, GPU, disk and network views; top processes; PSI; short live histories;
keyboard navigation; text/JSON exports. Rootless and read-only.

## Next: make monitoring trustworthy on more machines

- Real AMD, NVIDIA and Intel hardware validation; capability-specific backends.
- NUMA/SMT topology and CPU offline state visualization.
- Application/service grouping using existing cgroup membership.
- Visible inherited CPU/memory/I/O limits and throttling counters.
- Mount/device mapping and disk latency where it can be interpreted correctly.
- Measure sampler cost on hosts with thousands of processes.

## Later: interactive resource allocation

Start with an explicitly selected systemd service or scope. Show current and
inherited settings; apply a temporary CPU weight/quota or disk bandwidth
limit; verify the effective value; mark changes on the timeline; support undo.
Test that changing one workload does not accidentally modify another.

Hardware controls (CPU online state, governor/frequency limits, power settings)
are separate machine-wide operations. Detect shared frequency policies and
conflicts with other power managers. Expose only supported controls.

No release date or cross-device compatibility guarantee is implied by this
roadmap. The first release does not implement any resource modification.
