# 0.8.0

- Add a Capabilities page with collection wall/CPU time, peak RSS, permissions hints,
  source availability and collector errors.
- Add workload CPU/RAM/name/I/O sorting, stable selection with replacement notices,
  process trees and workload-to-disk navigation.
- Save presentation preferences explicitly; name markers and export redacted reports.
- Add verified per-service disk read/write limits with preview, conflict checks and Undo.
- Capture sustained PSI/quota incidents with bounded prehistory, posthistory and file budgets.
- Add an optional isolated privileged helper with exact-unit policy, root-owned
  receipts, restricted CPU/disk operations and interrupted-attempt recovery.
- Retain 35 ms Escape recognition and bounded read-only collector shutdown.

# 0.7.0

- Focus individual sockets, memory nodes, disks, GPUs and interfaces while keeping
  the overview summaries; add per-device disk and GPU histories.
- Pin a service to its effective CPU set with resource and pressure accounting.
- Add timeline markers, control outcomes and descriptive before/after comparisons.
- Record bounded private JSONL sessions and replay them without live controls.
- Show host/service pressure, quota throttling and exposed thermal counter events.

**0.5.0 visualization update:** the original overview remains the default;
`e` expands it into CPU topology and responsive resource panels. Memory NUMA,
block layering, GPU attachment and network link relationships use available
sysfs data. Resource controls remain explicit, separate actions.

# Functionality and implementation sequence

**0.4.0 status:** workload inspection and runtime CPU weight/quota with preview,
verification and session Undo are implemented. Real system-service kernel and
terminal-UI checks passed; see [VALIDATION.md](VALIDATION.md). Per-process
nice/affinity edits and memory changes remain planned. Disk bandwidth controls are implemented in 0.8.0. Persistent event recording is implemented in 0.7.0.
Service CPU placement now has a socket/NUMA/core selector, verified systemd/cpuset
application and Undo. CPU governors and logical CPU hotplug have mouse menus, bulk scopes, verified
sysfs writes and session Undo; hardware transition validation remains outstanding. Monitoring stays read-only unless `--control`
is explicitly enabled.

The goal is to help a user find
the workload competing for resources, inspect its actual resource allocation,
change that allocation deliberately, and observe the outcome. Implement the
following in order rather than adding unrelated tuning switches.

## 1. Application and service view

**Task:** “Which application or service is using these resources, and is its
own configuration limiting it?”

Group processes by existing cgroup/systemd membership. Display the group name,
contained processes, CPU/memory/I/O accounting, current and ancestor settings,
and available throttling/pressure counters. Show unknown attribution explicitly;
do not infer ownership solely from process names or claim all processes of an
application are always in one group. Distinguish desktop apps, user services,
system services and container groups when evidence is available.

Acceptance: a disposable service with several children is shown once as a
group; a tighter parent quota is visible; unavailable controller files remain
unavailable rather than unlimited. The read-only view works without elevating
privileges where filesystem/DBus permissions allow it.

## 2. Temporary CPU controls

**Task:** “Let my background build run without competing as strongly with the
service or application I am using.”

Begin with a selected, existing systemd service/scope. Offer CPU weight and
quota, with units and scope explained before applying. Weight changes relative
allocation during contention; quota imposes a ceiling. Show inherited
constraints and read the effective setting back after application. Add
per-process nice/affinity as separate controls, clearly distinguished from
group-level controls. Do not silently regroup arbitrary unmanaged processes.

Use existing systemd APIs rather than writing into systemd-owned cgroups
behind its back. Elevation belongs in a narrow authorization/helper boundary;
the entire UI should not need to run as root. Default to runtime changes;
persisting changes is an explicit separate action.

Acceptance: with two owned competing workloads, changing one group's weight
changes its observed allocation under contention; applying/removing a quota is
confirmed through settings and counters. Snapshot the previous setting, show
an event on the timeline, and provide Undo. Do not restore an old value over a
newer change made by another tool. User-visible success means the setting was
confirmed, not merely that an API request was sent.

## 3. Disk bandwidth controls

**Task:** “Keep a backup or indexing job from consuming all available disk I/O.”

Start with read/write bandwidth limits for a selected service on an explicitly
identified block device. Show observed rates, limits and I/O pressure together.
Support IOPS limits after the bandwidth workflow works. Map paths to backing
devices where reliable; mark unsupported storage layouts and scheduler-dependent
weight support. Preserve distinctions between buffered writes, block-device
I/O and application throughput.

Acceptance: an owned sustained workload is constrained on the chosen device;
another workload is unaffected by policy scope; Undo restores the previous
limit. Tests must exercise enforcement, not just inspect configuration values.

## 4. Memory budgets and observation history

**Task:** “See why this service is under memory pressure and keep it within a
deliberate budget.”

Show memory high/max, swap budgets, pressure and OOM events before enabling
changes. Explain that memory pressure throttling is different from a hard
ceiling and that lowering a hard ceiling can trigger allocation failures or
termination. Provide the same runtime/persistence, authorization and confirmed
application workflow as CPU controls. Undo changes settings; it cannot undo a
terminated process.

Add recording/replay of observations and control events so a user can compare
before and after. A changed metric is an observation, not proof that the
setting caused a performance improvement. Sampling overhead and missing data
must remain visible in the report.

## 5. Hardware controls

**Task:** “Explore the machine's power/performance trade-off while seeing what
the hardware actually supports.”

First show physical/logical topology, online CPUs, shared frequency policies,
governors, frequencies, temperatures and supported power interfaces. Then add
CPU affinity/placement visualization, supported frequency-policy controls and
CPU online/offline operations. These are machine-wide controls, distinct from
application allocation. Detect conflicts with active power managers and
never imply a shared frequency policy is a private per-process setting.

Validate hardware changes on representative Intel and AMD systems. GPU
monitoring needs real AMD/NVIDIA validation and a dedicated Intel backend;
GPU tuning should wait for explicit driver-specific capability support. No
universal cross-vendor per-application GPU quota is promised.

## Network controls: evaluate separately

Current network data is per-interface accounting, not per-application
attribution. Per-application traffic identification and rate limiting require
a separate backend and workflow; adding a slider to interface totals would
misrepresent what it controls. Compare existing traffic-control tools before
implementing shaping. Never replace unrelated qdisc/firewall rules as a side
effect of enabling a resource control.

## Delivered CPU milestone and recommended next milestone

The read-only workload view and temporary CPU quota/weight complete an initial
“inspect, adjust, observe, restore” workflow. Undo restores settings through
systemd, is session-only, and cannot atomically exclude concurrent external
writers; see README for lifetime and limitations. Timestamped control events
appear in workload details and the durable recording/marker workflow introduced in 0.7.0.

0.8.0 implements per-service **disk read/write bandwidth controls** using the
same preview/identity/verification/Undo workflow, with a real direct-I/O enforcement
test. Per-process nice/affinity can follow as distinct process-level controls.

Kernel references: [cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html),
[CPU frequency policies](https://docs.kernel.org/admin-guide/pm/cpufreq.html).
