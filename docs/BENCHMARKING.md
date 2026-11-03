# Collection and input benchmarks

The benchmark measures the current CLI collection pipeline. M0 captured the serial
baseline; M1 uses cached inventory and background frequency/GPU readers. It does
not change system resource policies. Run it while other Resource
Lens tests/benchmarks are stopped; record competing workloads when interpreting
results. Counters are still subject to the machine's visibility and permissions.

## Reproduce collection costs

From the repository root:

```bash
python3 scripts/benchmark_collection.py --samples 20 --warmup 2 --interval 1 --interval 0.2 --no-nvidia > /tmp/resource-lens-collection.json
```

The default mode also encodes each snapshot to JSON without writing the snapshot.
Use `--mode collect` to exclude JSON encoding. Omit `--no-nvidia` to include
optional NVIDIA queries if the utility is installed. The report records whether
querying was enabled. The script also runs from outside the source directory when
invoked by its absolute path.

Each requested interval gets a fresh collector. Construction and priming are
measured/excluded separately from steady-state windows. Warmup performs the same
collection/encoding path as the selected mode. The sample count includes only
the subsequent measured samples.

## Report interpretation

- `stages`: per-stage wall/collector-thread CPU distributions. Background-source
  stages measure request/poll cost; their I/O/worker cost is separate. p95 is nearest-rank;
  small runs provide only a rough estimate of tail behavior. Timings are per sample,
  not cumulative process-lifetime counters.
- `collection_ms` / `collection_cpu_ms`: complete sample cost. Their totals include
  assembly/instrumentation overhead outside named stages; asynchronous worker CPU
  is excluded from collector-thread CPU. Do not expect exact
  equality with the sum of stage costs.
- `pipeline_ms`: sample call plus optional JSON encoding. It excludes terminal
  rendering, recording/file output, profile jobs, control jobs and final benchmark
  report generation.
- `json_ms`, `json_cpu_ms`, `json_bytes`: encoding cost/size; null distributions in
  collect-only mode. Real JSON stream output and flush costs are not measured.
- `start_interval_ms`: actual start-to-start spacing. `start_delay_ms` is excess
  over the requested interval, including the previous cycle's overrun and sleep
  jitter. It is not a measured job queue delay; M0 has no job queue.
- `collection_overruns` / `pipeline_overruns`: number of samples whose work exceeds
  the requested interval. A requested 200 ms refresh does not prove 200 ms data.
- `monitor_cpu_one_core_pct`: monitor process CPU / complete measured wall window.
  The window includes the rest after the final sample. Child utilities' CPU time
  is excluded; same-process background worker CPU is included. This is not total system impact.
- `peak_rss_bytes`: process lifetime peak RSS, not per-sample current RSS. In a run
  with multiple interval cases the peak can include the previous case. It cannot
  prove absence of a leak.
- `counts` / `coverage`: observed entity counts, skipped processes, hierarchy
  truncation/errors and warning counts. Empty warnings do not establish complete
  measurement coverage. M1 source-quality fields provide additional availability
  information in snapshots but are not full per-metric coverage guarantees.

The main cadence matches the current TUI collector: wait until the requested interval
from sample start, with a minimum 50 ms rest even on overrun. This differs from
the snapshot CLI's sleep-then-sample loop. The benchmark is a baseline for the
interactive collector, not an assertion about every CLI mode.

The report records `collection_mode: background` for M1 cases. Cache warmup and
background reads can span window boundaries, especially with very short runs.
Source minimum refresh times do not guarantee a completed read at that cadence.
Do not compare individual stage CPU to the entire process CPU as if they included
the same threads. Initialization/warmup code is outside the measured main window;
an outstanding worker may still finish during it.

No hostnames, process names, PIDs, unit names, full snapshots, boot IDs or namespace
IDs are exported. Hardware model, kernel, Python version, entity counts, and
performance results remain visible for reproducibility.

## Measure terminal input separately

```bash
python3 scripts/benchmark_input.py > /tmp/resource-lens-input.json
```

This existing benchmark uses a 120×35 local PTY and reports navigation, input burst,
Escape-to-frame, and Escape-to-exit costs. It measures local curses flush behavior,
not SSH or physical display latency. Do not run it concurrently with the collection
benchmark when recording a reference result.

Open Status (`0`) to see the five slowest collection stages for the displayed
sample. Recorded samples show their recorded costs. Costs do not establish
availability or freshness of the underlying measurements.

## Current evidence

See [M0_BASELINE.md](M0_BASELINE.md) for the original baseline and
[M1_VALIDATION.md](M1_VALIDATION.md) for the current measurements and comparison
limits. Neither result is a guarantee for other workloads or hardware.
