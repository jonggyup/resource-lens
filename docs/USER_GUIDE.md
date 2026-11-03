# Resource Lens: the everyday guide

Launch `python3 -m resourcelens` from the project directory, or `resource-lens`
after installation. No flags are needed. Press **G** (Shift+G) for the same kinds
of task walkthroughs inside the app.

## Your first minute

1. Look at **Overview**. Its cards summarize the whole machine.
2. Click a busy resource to see its CPU topology, devices or interfaces.
3. Open **Applications (8)** to see individual processes ranked by CPU usage.
4. Use **s** to find a particular process, service, CPU or device.
5. Press **?** if you need the actions available on the current screen.

Explore freely: the app starts in read-only mode. Opening a view, guide or preview
does not change system settings.

## Move around without memorizing keys

| Action | Mouse | Keyboard |
| --- | --- | --- |
| Open a view | Click a top tab or resource card | Number keys; Tab for the next view |
| Open the task guide | Click Start here on Overview | G |
| Find something | Click s Find when shown | s, type a name, Enter |
| Select a row | Click it | Up/Down |
| Open a selection | Click an action; group rows open on click | Enter |
| Scroll details | Mouse wheel | Arrows or Page Up/Down |
| Return or cancel | Click the displayed Back/Cancel button | Esc |
| Exit from a main view | Click q Quit | q |

Uppercase keys use **Shift**. No function keys are required. A shortcut can have a
different meaning inside an editor; the bottom buttons and ? help describe the
current context. Esc backs out of an overlay but exits from a main view.

## “Which application is using resources?”

Open **Applications (8)**. It starts with individual processes. Select one and
press Enter for the inspector, **V** for memory inspection, or **P** for an investigation.
Use **/** to filter the list. CPU sorting uses **c**; memory sorting uses **m**.

Press **g** for **service groups (cgroups)**. A cgroup is a kernel-managed group of
processes, often a service or container; it is not necessarily one application.
Enter opens a group so you can inspect child groups and direct processes. Left or
Backspace goes to its parent; q returns to Applications. Parent totals include
children, so adding rows can double-count usage.

Process details also show the exact group and its readable ancestor constraints
after background inspection completes. The service association is listed separately:
a child group can have a tighter limit than its service. In the cgroup browser,
press **D** or click **D Constraints** for inspection starting on Constraints;
**I** inspects a group directly from the Applications list. Esc/Back returns one
level. Partial ancestry means some restrictions remain unknown.

## “Why is this process slow?”

Select it and press Enter. Summary separates observed quota activity, configured
ceilings and missing evidence. Tab or section numbers 1–5 switch between Summary,
Usage, Constraints, Evidence and Actions. Click a finding or press **e** for sources
and intervals; **n/p** or the buttons select evidence. **g/o/a** opens its exact
group / associated service / limiting ancestor. Esc restores the previous view.

Press **P** for a capture. Choose CPU usage, CPU + waits, hardware energy, or
counters only, and 5/10/30 seconds using the buttons or **[ / ]**. **W** retains the
direct ten-second CPU + waits shortcut. Esc cancels and returns to the inspector.
Capture results use **D** for supporting details. A counters-only capture needs no perf.

Measurements can identify work and waiting, but do not directly measure your
application's request latency. Some tracing requires additional permissions. A
failed or unavailable measurement is shown explicitly.

## “What does this number mean?”

Press **E**, choose a metric, and read its meaning, source and limitations.
Press **H** or click the health indicator for missing/stale data and next steps.

| Display | Meaning |
| --- | --- |
| 0 | A measured zero, unless the field explicitly says otherwise |
| N/A / unavailable | No usable measurement; do not assume idle |
| Stale | A source/snapshot is old, or a failed source refresh retained an earlier value |
| Recorded | Historical replay data, not current machine state |
| RSS | Resident process memory; shared mappings can be counted in several processes |
| PSS | Shared resident pages apportioned among mappings |
| CPU 100% for a process | One logical CPU fully busy |

Health uses source observation times when available; older recordings fall back
to snapshot freshness. CPU frequency and GPU reads run separately from fast usage
counters. Their last value can remain visible with its original age after a failed
refresh. Status (**0**) shows source quality/age; CPU/GPU details show relevant
source notes. The source observation time cannot establish when a driver last
updated its internal sensor. Hardware and containers expose different data.

## “Can I adjust the resources?”

1. Press **C**, then choose **Enable controls**. This only unlocks menus.
2. For hardware, open CPU (**2**) and press **g** or click a CPU.
3. From a process inspector press **r**, or click **Controls**, to review its
   associated service. Read the scope expansion: the service, other members and
   descendants are affected. **p** chooses CPUs, **w** sets CPU weight, **l** sets
   quota, and **b** sets disk bandwidth. Existing service-group shortcuts still work.
   **h** opens the separate MemoryHigh editor.
4. Read the change preview. Press **i** or click Impact for observed relationships
   and limits. Return to the preview, then choose Apply or Cancel.

Existing system permissions still apply. The impact panel is evidence from visible
workloads, not a prediction of latency or a guarantee about all affected processes.
Settings stay active after exit. Undo keys depend on the change: **u** on CPU for
hardware; **u**, **U**, **B** on service groups for CPU limits, placement and disk
limits respectively. **J Changes** brings all control domains together; **n/p**
selects an entry and **u** reviews restoration. Only the latest active change in
each domain can be restored. Backend identity and conflict checks still apply.
Undo history is held only for the current session; a recording is not a live receipt.

For memory, press **m** in the inspector to read exact group usage, swap, peak,
memory.stat categories, boundaries and scoped events. **V** remains the live process
RSS/PSS/sharing inspector; group accounting includes descendants and overlaps among
memory.stat categories must not be added into a new total.

From service review, **h** edits a runtime MemoryHigh reclaim/throttling boundary.
Enter positive whole bytes, K/M/G/T (base 1024), or `max`. Read the fresh usage and
ancestor preview, then scroll to its end to enable Apply. Below-usage requests can
cause heavy reclaim and allocation stalls. Usage can exceed MemoryHigh; MemoryMax
and other constraints still apply. **J**, select its apply entry, then **u** opens a
fresh restoration preview. Restoring settings does not restore reclaimed cache or
lost work. See [MemoryHigh details](MEMORYHIGH.md) for support and limitations.

## “Did the change help?”

Collect data before applying a change and keep collecting afterward. **J Changes**
shows its outcome and observation; the inspector Summary also shows the latest
related change. **M** still marks a manual baseline, and **Timeline (9)** compares
events selected with Up/Down. Page Up/Down scrolls.

The baseline ends when apply starts; the post-change window starts after verified
completion. Apply/verification time is excluded. Values are time weighted and
coverage shows usable seconds out of the requested 30 seconds on each side.
Below 80% is labelled incomplete. An unconfirmed attempt has no post-change window.
Compare similar work and allow both windows to fill. A difference does not prove
cause, workload enforcement, or a latency improvement. **H** opens measurement health.
Use **[ / ]** in Timeline or Changes to choose 5/15/30/60-second windows.

## “Can I reopen an investigation or compare runs?”

Press **L** for Sessions, or start fully offline with `resource-lens --sessions`.
Profiles and recordings remain separate artifacts. **i** imports a file or profile/
repeat directory without moving it. Enter opens the selection. Recorded targets
cannot start live capture or controls; **L** returns from replay to Sessions.

Choose a saved profile or repeat set and press **b** for baseline. Select the candidate
and press **c**. The comparison shows matched metrics, repeat counts/spread, coverage
and configuration differences. Partial/cancelled captures stay readable but cannot
be compared as complete runs. One run has unknown spread.

For measured latency/throughput, open a complete profile and press **o**. Enter
`name | unit | value | provenance`, for example `p99 | ms | 12.5 | load generator log`.
Only enter measurements from the displayed capture window. **x** exports a private,
redacted summary; originals and outcome sidecars remain separate raw evidence.

CPU/memory/I/O pressure findings use the selected group's recorded observations.
Memory-event findings name the counter and local/subtree accounting. Neither
ancestor activity nor shared group counters identifies a delayed PID or request.
See [Sessions details](SESSIONS.md) and [M4 validation](M4_VALIDATION.md).

For energy, select a process in Applications and choose **P → Energy efficiency**.
Energy belongs to the exposed hardware domain and includes other work. It is not
energy attributed to that process. Unsupported sensors remain unavailable.

More detail: [measurement and impact guide](UNDERSTANDING.md),
[profiling guide](PROFILING.md), [energy guide](ENERGY.md).
