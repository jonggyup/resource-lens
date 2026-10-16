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


def main():
    parser = argparse.ArgumentParser(description="Live, read-only Linux CPU/GPU/memory/disk/network dashboard.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--interval", type=float, default=1, help="sample interval in seconds (0.2-60; default 1)")
    parser.add_argument("--once", action="store_true", help="print one measured text snapshot and exit")
    parser.add_argument("--json", action="store_true", help="stream one JSON object per measured sample")
    parser.add_argument("--count", type=int, help="number of JSON samples (default: stream until interrupted)")
    parser.add_argument("--no-nvidia", action="store_true", help="do not invoke nvidia-smi")
    parser.add_argument("--ascii", action="store_true", help="use ASCII charts and borders in the terminal")
    args = parser.parse_args()
    if not math.isfinite(args.interval) or not 0.2 <= args.interval <= 60:
        parser.error("--interval must be between 0.2 and 60 seconds")
    if args.count is not None and (not args.json or args.count < 1):
        parser.error("--count requires --json and a positive integer")
    if args.once and args.json:
        parser.error("choose --once or --json")
    if sys.platform != "linux":
        parser.error("Resource Lens requires Linux /proc and /sys")
    if not (args.once or args.json) and not (sys.stdin.isatty() and sys.stdout.isatty()):
        parser.error("interactive mode requires a terminal; use --once or --json --count 1")
    collector = Collector(nvidia=not args.no_nvidia)
    try:
        if args.once or args.json:
            collector.sample()  # Prime cumulative counters; do not report lifetime averages.
            count = 0
            while True:
                time.sleep(args.interval)
                sample = collector.sample()
                if args.json:
                    print(json.dumps(sample, allow_nan=False), flush=True)
                else:
                    view = View()
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
                if args.once or (args.count is not None and count >= args.count):
                    break
        else:
            run(collector, args.interval, ascii=args.ascii)
    except (KeyboardInterrupt, BrokenPipeError):
        return 0
    except curses.error as exc:
        print(f"Terminal unavailable: {exc}. Try TERM=xterm-256color or --once.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
