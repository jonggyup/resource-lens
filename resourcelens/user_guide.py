"""Task-oriented introduction. Opening a guide never performs an operation."""
TASKS = [
    ('Find what is busy', 0, [
        '1. Start with Overview: CPU, RAM, disk and network show the whole system.',
        '2. Click a resource card to see its devices or CPU topology.',
        '3. Open Applications (8) to see processes ranked by CPU usage.',
        'Read it correctly: 100% process CPU means one logical CPU. N/A means unavailable, not idle.']),
    ('Understand a slow process or service', 7, [
        '1. Select a process with arrows or find it with s.',
        '2. Press P and choose CPU usage or CPU + waits. Capture lasts 10 seconds.',
        '3. Read the summary; D shows details. Esc returns or cancels a capture.',
        'Services: g switches to service groups (cgroups); Enter opens a group.',
        'Waiting is not request latency. Missing tracing permissions are reported.']),
    ('Understand memory use and sharing', 7, [
        '1. Select a process in Applications (8).',
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
        '1. In Applications (8), press g to show service groups and select a service.',
        '2. Press C to enable controls; p chooses CPUs, w sets weight, l sets quota, b limits disk bandwidth.',
        '3. Review the preview and i impact details, then apply or cancel.',
        'Undo: u for weight/quota, U for placement, B for disk limits.',
        'Controls require a supported service and existing permission. Child groups are affected too.']),
    ('Compare a change or measure energy', 8, [
        '1. Collect a baseline, then press M around the change you want to examine.',
        '2. Keep collecting, then open Timeline (9). Up/Down chooses an event; Page Up/Down scrolls.',
        '3. Compare the Before/After values and sample counts. Windows use up to 30 seconds on each side.',
        'Energy capture: select a process in Applications, then P and Energy efficiency.',
        'Energy covers hardware domains, not a single process. Differences do not prove cause.']),
    ('Fix missing data or understand a number', 9, [
        '1. Press H (Shift+H), or click the health indicator, to inspect missing or stale measurements.',
        '2. Select a row for its source, reason and next step.',
        '3. Press E (Shift+E) for metric definitions and limitations.',
        'N/A is unavailable. Zero is a measured zero. Stale means the displayed sample is old.',
        'Some machines and containers do not expose all sensors. Monitoring does not need root.']),
]
