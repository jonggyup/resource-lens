"""CLI entry point. Run directly with python3 -m resourcelens."""

import argparse
import curses
import json
import math
import sys
import time

from . import __version__
from .collect import Collector
from .ui import View, run
from .recording import Recorder, Replay, RecordingError


def main():
    parser = argparse.ArgumentParser(description="Live Linux resource dashboard with optional temporary systemd CPU controls.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--interval", type=float, default=1, help="sample interval in seconds (0.2-60; default 1)")
    parser.add_argument("--once", action="store_true", help="print one measured text snapshot and exit")
    parser.add_argument("--json", action="store_true", help="stream one JSON object per measured sample")
    parser.add_argument("--count", type=int, help="number of JSON samples (default: stream until interrupted)")
    parser.add_argument("--no-nvidia", action="store_true", help="do not invoke nvidia-smi")
    parser.add_argument("--ascii", action="store_true", help="use ASCII charts and borders in the terminal")
    parser.add_argument("--control", action="store_true", help="enable previewed CPU placement, governor/hotplug and systemd workload controls")
    parser.add_argument('--record', metavar='FILE', help='record samples and UI events to a new private JSONL file')
    parser.add_argument('--record-max-mb', type=int, default=256, help='recording size cap in MiB (default 256)')
    parser.add_argument('--replay', metavar='FILE', help='inspect a recording without accessing live telemetry or controls')
    args = parser.parse_args()
    if not math.isfinite(args.interval) or not 0.2 <= args.interval <= 60:
        parser.error("--interval must be between 0.2 and 60 seconds")
    if args.count is not None and (not args.json or args.count < 1):
        parser.error("--count requires --json and a positive integer")
    if args.once and args.json:
        parser.error("choose --once or --json")
    if args.control and (args.once or args.json):
        parser.error("--control is interactive only; text/JSON snapshots are read-only")
    if args.record_max_mb < 1:
        parser.error('--record-max-mb must be positive')
    if args.replay and (args.control or args.record):
        parser.error('--replay is read-only and cannot be combined with --control or --record')
    if sys.platform != "linux":
        parser.error("Resource Lens requires Linux /proc and /sys")
    if not (args.once or args.json) and not (sys.stdin.isatty() and sys.stdout.isatty()):
        parser.error("interactive mode requires a terminal; use --once or --json --count 1")
    recorder, replay = None, None
    try:
        replay = Replay(args.replay) if args.replay else None
        recorder = Recorder(args.record, args.record_max_mb * 1024 * 1024) if args.record else None
        collector = None if replay else Collector(nvidia=not args.no_nvidia)
        if args.once or args.json:
            if collector:
                collector.sample()  # Prime cumulative counters; do not report lifetime averages.
            count = 0
            while True:
                if replay:
                    sample = replay.sample_at(count)
                else:
                    time.sleep(args.interval)
                    sample = collector.sample()
                if recorder:
                    recorder.write('sample', sample)
                if args.json:
                    print(json.dumps(sample, allow_nan=False), flush=True)
                else:
                    view = View()
                    if replay:
                        view.investigation.events = list(replay.events)
                    view.update(sample)
                    print(f"Resource Lens {__version__} | {sample['hostname']} | read-only")
                    for page in range(1, len(view.names)):
                        view.page = page
                        print(f"\n{view.names[page]}")
                        lines = view.lines(100)
                        print("\n".join(lines[:23] if page == 5 else lines))
                    for warning in sample["warnings"]:
                        print(f"Warning: {warning}")
                count += 1
                if args.once or (args.count is not None and count >= args.count) or (replay and count >= len(replay.offsets)):
                    break
        else:
            run(collector, args.interval, ascii=args.ascii, control=args.control, recorder=recorder, replay=replay)
    except (KeyboardInterrupt, BrokenPipeError):
        return 0
    except (OSError, RecordingError, json.JSONDecodeError) as exc:
        print(f'Resource Lens: {exc}', file=sys.stderr)
        return 1
    except curses.error as exc:
        print(f"Terminal unavailable: {exc}. Try TERM=xterm-256color or --once.", file=sys.stderr)
        return 1
    finally:
        if recorder:
            if recorder.error:
                print(recorder.error, file=sys.stderr)
            recorder.close()
        if replay:
            for warning in replay.warnings:
                print(warning, file=sys.stderr)
            replay.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
