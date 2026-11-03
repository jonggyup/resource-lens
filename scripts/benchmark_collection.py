#!/usr/bin/env python3
"""Read-only collection/JSON benchmark; no terminal rendering or policy changes.

Run from any directory: python3 /path/to/resource-lens/scripts/benchmark_collection.py
Output contains costs and entity counts, not hostnames, PIDs or workload names.
"""

import argparse
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from resourcelens import __version__
from resourcelens.collect import Collector


def distribution(values):
    """Nearest-rank p95, including zero; missing observations remain excluded."""
    values = sorted(v for v in values if v is not None)
    if not values:
        return dict(count=0, min=None, median=None, p95=None, max=None, mean=None)
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError('Benchmark observations must be finite numbers')
    return dict(count=len(values), **{key: round(value, 6) for key, value in {
        'min': values[0], 'median': statistics.median(values),
        'p95': values[math.ceil(.95 * len(values)) - 1], 'max': values[-1],
        'mean': statistics.mean(values),
    }.items()})


def run_case(collector, *, interval, samples, warmup=2, mode='json',
             wall_clock=None, cpu_clock=None, process_cpu_clock=None, sleep=None):
    """Match the TUI's start-relative cadence and 50 ms minimum collector rest.

    Initialization and warmup are excluded. The measured window includes the
    rest after the final sample, so CPU share covers complete sampling cycles.
    There is no queue: start delay describes the prior cycle's overrun/sleep jitter.
    """
    if not math.isfinite(interval) or not .2 <= interval <= 60:
        raise ValueError('Interval must be between 0.2 and 60 seconds')
    if type(samples) is not int or samples < 1 or type(warmup) is not int or warmup < 1:
        raise ValueError('Samples and warmup must be positive integers')
    if mode not in ('collect', 'json'):
        raise ValueError('Mode must be collect or json')
    clock = wall_clock or time.monotonic
    thread_cpu = cpu_clock or time.thread_time
    process_cpu = process_cpu_clock or time.process_time
    rest = sleep or time.sleep
    previous_start = None
    for _ in range(warmup):
        previous_start = clock()
        snapshot = collector.sample()
        if mode == 'json':
            (json.dumps(snapshot, allow_nan=False) + '\n').encode('utf-8')
        rest(max(.05, interval - (clock() - previous_start)))

    rows = []
    window_start, cpu_start = clock(), process_cpu()
    for _ in range(samples):
        start = clock()
        snapshot = collector.sample()
        json_start, json_cpu_start = clock(), thread_cpu()
        encoded_bytes = None
        if mode == 'json':
            encoded_bytes = len((json.dumps(snapshot, allow_nan=False) + '\n').encode('utf-8'))
        json_wall, json_cpu = (clock() - json_start) * 1000, (thread_cpu() - json_cpu_start) * 1000
        end = clock()
        meta = snapshot['collector']
        rows.append(dict(
            collection_ms=meta['duration_ms'], collection_cpu_ms=meta['cpu_ms'],
            pipeline_ms=(end - start) * 1000,
            json_ms=json_wall if mode == 'json' else None,
            json_cpu_ms=json_cpu if mode == 'json' else None,
            json_bytes=encoded_bytes, start_interval_ms=(start - previous_start) * 1000,
            start_delay_ms=max(0, start - previous_start - interval) * 1000,
            stages=meta['stages'], counts=meta['counts'], peak_rss_bytes=meta['rss_bytes'],
            hierarchy_truncated=bool(snapshot.get('cgroup_tree', {}).get('truncated')),
            hierarchy_errors=snapshot.get('cgroup_tree', {}).get('errors', 0),
            warning_count=len(snapshot.get('warnings', []))))
        previous_start = start
        rest(max(.05, interval - (clock() - start)))
    elapsed, cpu_elapsed = clock() - window_start, process_cpu() - cpu_start

    stage_names = sorted({name for row in rows for name in row['stages']})
    count_names = sorted({name for row in rows for name in row['counts']})
    stages = {name: {metric: distribution(row['stages'].get(name, {}).get(metric) for row in rows)
                     for metric in ('duration_ms', 'cpu_ms')} for name in stage_names}
    return dict(
        requested_interval_s=interval, samples=samples, warmup_samples=warmup, mode=mode,
        measured_window_s=elapsed, monitor_process_cpu_ms=cpu_elapsed * 1000,
        monitor_cpu_one_core_pct=100 * cpu_elapsed / elapsed if elapsed > 0 else None,
        collection_overruns=sum(row['collection_ms'] > interval * 1000 for row in rows),
        pipeline_overruns=sum(row['pipeline_ms'] > interval * 1000 for row in rows),
        metrics={key: distribution(row[key] for row in rows) for key in (
            'collection_ms', 'collection_cpu_ms', 'pipeline_ms', 'json_ms', 'json_cpu_ms',
            'json_bytes', 'start_interval_ms', 'start_delay_ms', 'peak_rss_bytes')},
        stages=stages,
        counts={name: distribution(row['counts'].get(name) for row in rows) for name in count_names},
        coverage=dict(hierarchy_truncated_samples=sum(row['hierarchy_truncated'] for row in rows),
                      hierarchy_errors=sum(row['hierarchy_errors'] for row in rows),
                      warning_count=sum(row['warning_count'] for row in rows)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interval', type=float, action='append',
                        help='seconds between starts; repeatable (default: 1 and 0.2)')
    parser.add_argument('--samples', type=int, default=20, help='measured samples per interval (default: 20)')
    parser.add_argument('--warmup', type=int, default=2, help='unmeasured priming samples per interval (default: 2)')
    parser.add_argument('--mode', choices=('collect', 'json'), default='json',
                        help='collect only, or additionally encode JSON without writing snapshots')
    parser.add_argument('--no-nvidia', action='store_true', help='disable optional nvidia-smi querying')
    args = parser.parse_args(argv)
    intervals = args.interval or [1, .2]
    if any(not math.isfinite(value) or not .2 <= value <= 60 for value in intervals):
        parser.error('--interval must be between 0.2 and 60 seconds')
    if not 1 <= args.samples <= 10000 or not 1 <= args.warmup <= 1000:
        parser.error('--samples must be 1–10000 and --warmup must be 1–1000')
    try:
        affinity_count = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity_count = None
    report = dict(
        schema=1, resource_lens_version=__version__, timestamp=time.time(),
        environment=dict(system=platform.system(), kernel=platform.release(),
                         architecture=platform.machine(), python=platform.python_version(),
                         logical_cpus=os.cpu_count(), eligible_cpus=affinity_count),
        method=dict(rendering='none', percentile='nearest-rank', minimum_rest_s=.05,
                    initialization_and_warmup='excluded from measured windows',
                    final_sample_rest='included in measured window',
                    cpu_scope='monitor process only; excludes child utility CPU',
                    exclusions=['terminal rendering', 'recording/disk writes', 'profiling',
                                'control jobs', 'benchmark report encoding/output'],
                    input_latency_command='python3 scripts/benchmark_input.py'),
        cases=[])
    try:
        for interval in intervals:
            init_wall, init_cpu = time.monotonic(), time.process_time()
            collector = Collector(nvidia=not args.no_nvidia, background=True)
            try:
                initialization = dict(duration_ms=(time.monotonic() - init_wall) * 1000,
                                      cpu_ms=(time.process_time() - init_cpu) * 1000)
                report['environment']['cpu_model'] = collector.model
                case = run_case(collector, interval=interval, samples=args.samples,
                                warmup=args.warmup, mode=args.mode)
                case['initialization'] = initialization
                case['collection_mode'] = 'background'
                case['nvidia_query'] = 'enabled' if collector.nvidia else 'disabled_or_not_installed'
                report['cases'].append(case)
            finally:
                collector.close()
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError) as exc:
        print('Benchmark failed: ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
