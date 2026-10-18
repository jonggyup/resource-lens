# Interaction audit — 0.9.1

The 0.9.0 workspace addition changed an existing mouse gesture and introduced
routing conflicts. This audit covers input dispatch and control access, backed by
the full automated suite and live terminal checks. It is not a claim that every
hardware driver, terminal emulator, or privileged operation has been tested.

## Confirmed regressions and fixes

| Conflict | Correction |
| --- | --- |
| CPU left-click jumped to Applications instead of opening settings | Restore ordinary left/right CPU and socket clicks. Shift-click CPU/core/socket opens related applications; keyboard `a` remains available. |
| Workspace buttons bypassed the placement editor's keyboard navigation lock | Hide and ignore workspace buttons during placement. Tile clicks still toggle CPUs; Esc returns to Applications. |
| Wide application list/detail layout reset mouse-wheel scroll | Wheel over the list changes selection; wheel over details focuses and scrolls details. Clicking details also focuses them. |
| General footer shortcuts crowded out existing controls | Render contextual actions before general workspace shortcuts. |

## Routing and feature review

| Area | Checked behavior and evidence |
| --- | --- |
| CPU settings | Ordinary click/right-click opens the correct logical CPU/socket menu. Keyboard `g` and the all-CPU settings row remain available. Live terminal checks open and cancel an offline preview; fixture tests cover governor/online settings, verification and Undo. |
| CPU placement | `p` opens the selected service's selector; CPU/core/socket clicks toggle eligible CPUs. `a` selects all while editing, rather than opening Applications. Workspace keys and clicks cannot leave an active selector. Esc cancels. |
| Application controls | Weight `w`, quota `l`, placement `p`, disk bandwidth `b`, and Undo `u`/`U`/`B` retain their handlers in all five detail tabs. Tests intercept submission without modifying the machine. |
| Modal precedence | Control dialogs consume input before workspaces. Busy dialogs ignore duplicate apply clicks. Preview clipping and read-only/replay guards remain covered by existing tests. |
| Search and help | Workspace letters and brackets remain literal text inside search. Help blocks controls and restores the previous scroll position. |
| Applications | List selection, detail focus, wheel routing, clickable tabs, filtering, sorting, identity replacement checks and hardware relationship clearing have regression coverage. |
| Resource navigation | Overview expansion, resource focus, hardware topology, numeric tabs, mouse tabs and view switcher retain existing tests. CPU relationships are allowed placement, not execution attribution. |
| Investigation | Markers, history, comparison, recording/replay, reports, capabilities and saved preferences remain covered by the full suite. No collector or recording format changes in this fix. |
| Responsiveness | Input batching, modal sequencing, mouse redraw boundaries and real-terminal Escape exit tests remain in the full suite. No new input waits were added. |

167 automated tests pass, including seven new routing tests and the revised
Shift-click relationship test. Live tmux evidence is saved in
[interaction-audit-ui-evidence.json](interaction-audit-ui-evidence.json).
The live audit uses a disposable sleep service and cancels CPU offline,
CPU weight/quota and disk bandwidth previews without applying resource changes.
The test service is stopped afterward. This host exposes no frequency governors;
governor behavior is covered by the existing simulated sysfs tests.

After filtering a previously selected group out, the UI deliberately requires
explicit selection of another row before controls act on it. This prevents a
filter or refreshed list from silently changing the control target.
