# M0 baseline and completion evidence

Measured 2026-10-27. Milestone M0 adds instrumentation and contracts; it does not
yet change collection cadence, resolve exact target ancestry, or diagnose resource
problems. The [blueprint](BLUEPRINT.md) defines those subsequent milestones.

## Delivered

- Wall/thread-CPU timing for 17 collection stages and visible entity counts.
- The five slowest stages on Status (`0`), with costs explicitly separated from
  measurement availability/freshness.
- A reproducible collection/JSON benchmark, plus the existing separate input benchmark.
- Immutable process/cgroup identity, source-quality, coverage and constraint contracts.
- Additive live collection-scope metadata; schema-1 scalar meanings and recording
  envelopes retained.
- Legacy recording compatibility tests and eight acceptance scenarios for the M1
  target resolver, including child limits, hidden ancestry and identity changes.

Details: [benchmark method](BENCHMARKING.md), [data contracts](DATA_CONTRACTS.md).

## Environment and method

Linux 5.15.0-198-generic, x86_64, Python 3.10.12, Intel Xeon E5-2660 v3 at 2.60 GHz.
The collector saw 40 logical CPUs, three processes, one service/workload group,
46 cgroup nodes, three disks, one network interface, one GPU and four energy
domains. These are visible entities, not a claim of complete host attribution or
validated sensor accuracy.

Both collection cases used 20 measured samples after two warmup samples. Each case
used a new collector. NVIDIA utility queries were disabled. No test suite or input
benchmark was run concurrently with collection. Results remain subject to other
host activity; there was no controlled workload or CPU pinning.

```bash
python3 scripts/benchmark_collection.py --samples 20 --warmup 2 --interval 1 --interval 0.2 --no-nvidia > docs/m0-collection-baseline.json
python3 scripts/benchmark_input.py > docs/m0-input-baseline.json
```

Structured results: [collection](m0-collection-baseline.json),
[input](m0-input-baseline.json). The collection report includes exact environment,
parameters, count ranges, initialization costs, stage distributions and exclusions.

## Collection results

| Metric | Requested 1 s interval | Requested 0.2 s interval |
| --- | ---: | ---: |
| Median sample wall time | 72.20 ms | 63.50 ms |
| p95 sample wall time | 858.94 ms | 64.00 ms |
| Maximum sample wall time | 897.26 ms | 64.30 ms |
| Median collector-thread CPU time | 68.61 ms | 63.33 ms |
| Median JSON encoding wall time | 0.97 ms | 0.91 ms |
| Median actual start interval | 1001.00 ms | 200.21 ms |
| Monitor CPU as a fraction of one logical CPU | 7.31% | 32.18% |
| Samples exceeding requested interval | 0 / 20 | 0 / 20 |

The CPU fraction includes complete sampling cycles and JSON encoding, but excludes
terminal rendering, recording writes, profiling, control work and child utilities'
CPU. Peak monitor RSS was approximately 16.7 MiB; this is a process-lifetime peak,
not a current-memory or leak measurement. Both cases reported no skipped processes,
hierarchy errors, hierarchy truncation or top-level warnings. That does not establish
availability of every individual metric.

Selected stages:

| Stage | Median wall, 1 s case | p95 wall, 1 s case | Median wall, 0.2 s case |
| --- | ---: | ---: | ---: |
| CPU hardware inventory | 26.08 ms | 48.12 ms | 25.98 ms |
| Cgroup hierarchy | 9.62 ms | 14.75 ms | 9.55 ms |
| CPU readings | 6.12 ms | 799.03 ms | 5.86 ms |
| Resource topology | 5.87 ms | 8.15 ms | 5.85 ms |
| Thermal throttle counters | 5.84 ms | 7.48 ms | 5.88 ms |

The long tail appeared in the CPU stage, which combines `/proc/stat` and per-CPU
frequency reads. This measurement does **not** identify which individual read
stalled. The short second case did not reproduce the tail; it does not establish
that faster sampling removes the issue. The cases ran sequentially, and neither
the host workload nor kernel caching was controlled.

## Terminal input results

Three 120×35 local PTY trials measured:

- Navigation: 1.66–5.74 ms across 15 key-to-curses-flush observations.
- Forty-character input burst: 1.34–1.39 ms, one frame per trial.
- Escape-to-frame: 2.38–2.42 ms.
- Escape-to-exit: 25.15–28.43 ms.

These establish local responsiveness in this environment, not remote SSH/display
latency, terminal accessibility, or independent-user usability.

## Verification and next work

All **268 automated tests passed**, including metric regressions, real PTY
navigation/resize/exit checks, legacy recording round-trips, identity boundaries,
metadata validation and deterministic benchmark accounting. The new benchmark
also ran directly against live read-only counters. No host resource policy was changed.

M1 priorities supported by this baseline:

1. Distinguish aggregate CPU-counter cost from per-CPU frequency reads before
   choosing how to isolate slow frequency sampling.
2. Cache slower-changing CPU hardware/resource inventory with explicit freshness
   and invalidation; keep control previews on fresh authoritative reads.
3. Separate lightweight hierarchy navigation from selected-target detail work.
4. Track actual source cadence and coverage under load. Do not assume the 200 ms
   option is cheap simply because this short case met its wall-time deadline.
5. Implement exact membership and ancestor resolution against the prepared fixtures.

Large-host scaling, long-duration memory behavior, live mutation enforcement and
broad hardware/manager coverage remain subsequent validation work.
