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
press Enter for details, **V** for memory inspection, or **P** for an investigation.
Use **/** to filter the list. CPU sorting uses **c**; memory sorting uses **m**.

Press **g** for **service groups (cgroups)**. A cgroup is a kernel-managed group of
processes, often a service or container; it is not necessarily one application.
Enter opens a group so you can inspect child groups and direct processes. Left or
Backspace goes to its parent; q returns to Applications. Parent totals include
children, so adding rows can double-count usage.

## “Why is this process slow?”

Select it and press **P**. Choose CPU usage to inspect CPU work, or CPU + waits to
also inspect thread waiting. Collection runs for 10 seconds in the background.
Esc cancels and returns. Read the summary first; **D** opens supporting details.

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
| Stale | The displayed snapshot is older than the freshness threshold |
| Recorded | Historical replay data, not current machine state |
| RSS | Resident process memory; shared mappings can be counted in several processes |
| PSS | Shared resident pages apportioned among mappings |
| CPU 100% for a process | One logical CPU fully busy |

The freshness indicator describes the snapshot; it cannot establish exactly when
a driver last updated every sensor. Hardware and containers expose different data.

## “Can I adjust the resources?”

1. Press **C**, then choose **Enable controls**. This only unlocks menus.
2. For hardware, open CPU (**2**) and press **g** or click a CPU.
3. For a service, open Applications, switch to service groups with **g**, and select
   the service. **p** chooses CPUs, **w** sets CPU weight, **l** sets quota, and
   **b** sets disk bandwidth.
4. Read the change preview. Press **i** or click Impact for observed relationships
   and limits. Return to the preview, then choose Apply or Cancel.

Existing system permissions still apply. The impact panel is evidence from visible
workloads, not a prediction of latency or a guarantee about all affected processes.
Settings stay active after exit. Undo keys depend on the change: **u** on CPU for
hardware; **u**, **U**, **B** on service groups for CPU limits, placement and disk
limits respectively. Undo history is held only for the current session.

## “Did the change help?”

Collect data before the change, press **M** around the change, and keep collecting.
Open **Timeline (9)**. Up/Down selects an event; Page Up/Down scrolls the comparison.
Bars show sample averages from up to 30 seconds before and after, with counts of
usable samples. Compare similar work and allow time for both windows to fill.
A difference alone does not prove cause.

For energy, select a process in Applications and choose **P → Energy efficiency**.
Energy belongs to the exposed hardware domain and includes other work. It is not
energy attributed to that process. Unsupported sensors remain unavailable.

More detail: [measurement and impact guide](UNDERSTANDING.md),
[profiling guide](PROFILING.md), [energy guide](ENERGY.md).
