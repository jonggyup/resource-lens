"""Task-oriented introduction. Opening a guide never performs an operation."""
TASKS = [
    ('Find what is busy', 0, [
        '1. Start with Overview: CPU, RAM, disk and network show the whole system.',
        '2. Click a resource card to see its devices or CPU topology.',
        '3. Open Applications (8) to see processes ranked by CPU usage.',
        'Read it correctly: 100% process CPU means one logical CPU. N/A means unavailable, not idle.']),
    ('Understand a slow process or service', 7, [
        '1. Select a process with arrows or find it with s.',
        '2. Press Enter for the inspector: Summary, Usage, Limits, Evidence and Actions.',
        '3. Press e for evidence or P to choose a capture and 5/10/30 seconds; counters-only needs no perf.',
        '4. Esc returns one level or cancels a capture. Capture results use D for details.',
        'Services: g switches to groups; I inspects a selected group, Enter browses its children.',
        'Waiting is not request latency. Missing tracing permissions are reported.']),
    ('Understand memory use and sharing', 7, [
        '1. Select a process in Applications (8); m in its inspector explains cgroup current/swap/peak, categories and events.',
        '2. Press V to inspect allocation, threads and peer processes.',
        '3. Click a tab or use Tab. Press r for a fresh inspection.',
        'RSS counts resident mappings. PSS apportions shared pages; do not add thread RSS.']),
    ('Change CPU settings carefully', 1, [
        '1. Press C (Shift+C), then choose Enable controls.',
        '2. Press g on CPU or click a CPU tile to choose a setting.',
        '3. Read the preview; i explains observed impact. Esc returns without applying.',
        '4. Apply only the reviewed change. u on CPU undoes the latest hardware change.',
        'Enabling controls changes nothing by itself. Applied settings remain after exit.']),
    ('Limit a service or choose its CPUs', 7, [
        '1. In a process inspector, press r to review its service and the wider scope affected.',
        '2. C enables controls; p chooses CPUs, w sets weight, l sets quota, b limits disk bandwidth, h reviews MemoryHigh.',
        '3. Review the preview and i impact details, then apply or cancel.',
        'J opens Changes; n/p selects an entry; u reviews restoration of the latest active domain change.',
        'Controls require a supported service and existing permission. Child groups are affected too.']),
    ('Compare a change or measure energy', 8, [
        '1. Collect a baseline, then press M around the change you want to examine.',
        '2. Keep collecting, then open Timeline (9). Up/Down chooses an event; Page Up/Down scrolls.',
        '3. Compare time-weighted values and seconds of coverage; under 80% is incomplete.',
        'Controls exclude apply/verification time; the after window starts only after verified completion.',
        'Energy capture: select a process in Applications, then P and Energy use.',
        'Energy covers hardware domains, not a single process. Differences do not prove cause.']),
    ('Fix missing data or understand a number', 9, [
        '1. Press H (Shift+H), or click the health indicator, to inspect missing or stale measurements.',
        '2. Select a row for its source, reason and next step.',
        '3. Press E (Shift+E) for metric definitions and limitations.',
        'N/A is unavailable. Zero is a measured zero. Stale means the displayed sample is old.',
        'Some machines and containers do not expose all sensors. Monitoring does not need root.']),
    ('Reopen evidence and compare runs', 8, [
        '1. Press L for Sessions, or start fully offline with resource-lens --sessions.',
        '2. i imports a profile or recording path; Enter opens it. L returns from replay to Sessions.',
        '3. b selects a profile/repeat baseline; c compares the selected candidate, including coverage and spread.',
        '4. o in a saved profile adds a measured outcome: name | unit | value | measurement source. x exports a redacted summary.',
        'Partial captures remain incomplete. Raw originals are retained; recorded targets never enable live controls.']),
    ('Review a service MemoryHigh boundary', 7, [
        '1. Open a process inspector, r service review, then h MemoryHigh. C enables controls.',
        '2. Enter whole bytes, K/M/G/T or max. Scroll through fresh usage, scope and ancestor constraints before Enter applies.',
        '3. Below-usage boundaries can cause reclaim and stalls; usage can exceed MemoryHigh. Existing MemoryMax still applies.',
        '4. J Changes, select its apply entry, then u prepares a fresh restoration preview.',
        'Only runtime MemoryHigh changes. Existing manager permissions apply; the CPU/disk helper does not authorize memory.']),
]
