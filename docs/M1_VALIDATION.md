# M1 target context and collection validation

Delivered 2026-10-27. M1 adds exact-target context and source-aware collection.
The unified inspector and diagnostic findings remain M2 work.

## Delivered behavior

- Explicit bounded read outcomes preserve permission denial, missing interfaces,
  disappearing targets, invalid data and exceeded budgets.
- Exact process membership remains separate from service association. The selected
  process/group gets a bounded asynchronous ancestor inspection with local and
  inherited limits, CPU/memory counters, pressure data and event scope.
- Process identities/membership and all observed group/mount identities are
  rechecked before publishing detail or counter baselines. Changed targets discard
  the inspected ancestry. Counter resets remain unknown intervals.
- Cgroup2 mount mapping distinguishes process membership paths from browser paths
  relative to the visible mount. Hidden ancestry is never reported as unrestricted.
- CLI collection separates frequency and GPU reads into independent workers;
  a third worker handles the selected target. Each has at most one active and
  one latest pending request. Exit does not join blocked telemetry reads.
- Inventory caches retain observation timestamps and invalidate on observed
  CPU/device changes and UI control/restore outcomes. The hierarchy's rate interval
  follows its actual refresh time, independently of the main sampling interval.
- Health uses per-source timestamps where recorded. Optional-source errors can
  retain a previous value with its original age and degraded status. Recorded
  data remains offline; old recordings retain their earlier fallback behavior.

Process details include exact-group context when available. In the cgroup browser,
press **D** or click **D Constraints** to inspect the selected group's constraints;
Esc/Back returns to the browser. Service-group Limits also includes selected-target
context. Status shows source ages and the slowest main collection stages.

See [DATA_CONTRACTS.md](DATA_CONTRACTS.md) for shapes, scope rules and budgets.
Local versus subtree memory event interpretation follows the
[kernel cgroup v2 documentation](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html);
the resolver records the mount's memory-event mode and probes available files.

## Automated and live checks

All **291 tests pass**. The suite covers the original eight M0 resolver scenarios, mount coordinate
mapping/escaped mount paths, permission/read outcomes, malformed counters/pressure,
counter resets, process movement, PID reuse, cgroup replacement/recreation, bounded
detail/process scans, worker coalescing, old-result rejection, cache invalidation,
retained-value age and shutdown with blocked optional readers. Browser constraints
have keyboard/mouse/Back checks; background detail publication survives recording
and offline replay. Existing PTY navigation, CPU-tile right-click menus, resizing,
control verification guards and snapshot CLI checks also pass.

A live read-only inspection of the benchmark process resolved four visible ancestor
directories. Missing/not-exposed data produced partial context. No resource settings
were changed; this does not add hardware-control or enforcement validation.

## Collection measurements

Commands, run separately from tests and the input benchmark:

```bash
python3 scripts/benchmark_collection.py --samples 20 --warmup 2 --interval 1 --interval 0.2 --no-nvidia > docs/m1-collection-evidence.json
python3 scripts/benchmark_input.py > docs/m1-input-evidence.json
```

Evidence: [collection](m1-collection-evidence.json), [input](m1-input-evidence.json).
Method and exclusions: [BENCHMARKING.md](BENCHMARKING.md).

Linux 5.15.0-198-generic, Python 3.10.12, Intel Xeon E5-2660 v3, 40 visible logical
CPUs. Collection saw three processes, one workload, 47 cgroup nodes, three disks,
one network interface, one GPU and four energy domains. NVIDIA utility querying
was disabled. Domain detection does not validate sensor accuracy.

| Metric | M0, 1 s | M1, 1 s | M0, 0.2 s | M1, 0.2 s |
| --- | ---: | ---: | ---: | ---: |
| Median collection wall | 72.20 ms | 56.79 ms | 63.50 ms | 42.78 ms |
| p95 collection wall | 858.94 ms | 67.23 ms | 64.00 ms | 62.10 ms |
| Maximum collection wall | 897.26 ms | 101.28 ms | 64.30 ms | 65.89 ms |
| Median collector-thread CPU | 68.61 ms | 55.54 ms | 63.33 ms | 42.41 ms |
| Median JSON encoding | 0.97 ms | 2.40 ms | 0.91 ms | 2.78 ms |
| Monitor process CPU / one logical CPU | 7.31% | 6.40% | 32.18% | 24.54% |
| Main-pipeline interval overruns | 0 / 20 | 0 / 20 | 0 / 20 | 0 / 20 |

These are two short observations on the same declared hardware/kernel, taken at
different times without controlling host workload, CPU frequency or caching. M0
saw 46 hierarchy nodes; M1 saw 47. They are useful baseline comparisons, not a
controlled causal benchmark or cross-host guarantee. The asynchronous sources can
still be pending or stale while the fast sample meets its deadline.

Monitor CPU includes frequency/GPU worker threads in M1; main collector-thread CPU
does not. JSON carries more quality/activity metadata, which increases encoding
cost. Peak RSS reached approximately 17.5 MiB in the M1 process; this remains a
process-lifetime peak and does not establish long-duration memory behavior.

The one-second main collection p95 no longer included the M0 frequency-read tail
in this run. Separate blocked-reader tests establish that a blocked optional read
cannot prevent the main sampler from progressing. High-frequency sampling still
has substantial CPU cost. Thermal counter reads, temperatures, hierarchy refreshes,
and cache-copy costs remain visible in the evidence; no broader performance target
is claimed as achieved.

## Terminal input measurements

Three 120×35 local PTY trials observed navigation at 0.87–4.29 ms, 40-character
input bursts at 1.17–1.28 ms, Escape-to-frame at 2.22–2.32 ms and Escape-to-exit at
22.71–22.79 ms. This measures local key-to-curses-flush behavior, not remote SSH
or physical display latency. CPU tile positions and existing control menus remain
compatible with the PTY interaction checks.

## Remaining boundaries

- Live ancestry coverage stays conservative: a visible mount root does not prove
  initial-namespace access. Unreadable ancestors and unsupported controllers remain
  partial/unknown, and quotas are shared observed ceilings rather than entitlements.
- Thread placement is not measured; threaded hierarchies explicitly warn that
  leader membership cannot establish every thread's constraints.
- Source-quality producers cover the documented metrics, not every optional
  driver field. Cached NUMA/topology data has an explicit source age.
- Source refresh times are minimum cache periods, not hard deadlines. Checked
  work budgets cannot interrupt an individual kernel read.
- Large-host scale, sustained memory behavior, independent-user usability, broader
  kernel/manager coverage and real hardware transitions remain further validation.
