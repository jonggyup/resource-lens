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
from .usability import load_preferences, export_report
from .incidents import IncidentCapture
from .controls import ControlError


def main():
    parser = argparse.ArgumentParser(description="Live Linux resource dashboard with optional verified runtime resource controls.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--interval", type=float, default=1, help="sample interval in seconds (0.2-60; default 1)")
    parser.add_argument("--once", action="store_true", help="print one measured text snapshot and exit")
    parser.add_argument("--json", action="store_true", help="stream one JSON object per measured sample")
    parser.add_argument("--count", type=int, help="number of JSON samples (default: stream until interrupted)")
    parser.add_argument("--no-nvidia", action="store_true", help="do not invoke nvidia-smi")
    parser.add_argument("--ascii", action="store_true", help="use ASCII charts and borders in the terminal")
    parser.add_argument("--control", action="store_true", help="enable previewed CPU and per-service disk bandwidth controls")
    parser.add_argument('--record', metavar='FILE', help='record samples and UI events to a new private JSONL file')
    parser.add_argument('--record-max-mb', type=int, default=256, help='recording size cap in MiB (default 256)')
    parser.add_argument('--replay', metavar='FILE', help='inspect a recording without accessing live telemetry or controls')
    parser.add_argument('--no-config', action='store_true', help='ignore saved presentation preferences')
    parser.add_argument('--report', metavar='FILE', help='export a private JSON investigation report and exit')
    parser.add_argument('--report-identifiers', action='store_true', help='include host and event identifiers in --report')
    parser.add_argument('--incidents', metavar='DIR', help='opt-in bounded sustained-pressure capture directory')
    parser.add_argument('--alert-threshold', type=float, default=20, help='PSI percent or quota throttled time percent (default 20)')
    parser.add_argument('--alert-duration', type=float, default=10, help='sustained threshold seconds (default 10)')
    parser.add_argument('--alert-cooldown', type=float, default=60, help='minimum seconds between captures (default 60)')
    parser.add_argument('--helper', metavar='PATH', help='explicit root-owned helper zipapp; requires configured sudo and unit allowlist')
    parser.add_argument('--profile', metavar='DIR', help='capture a private investigation session in a new directory')
    parser.add_argument('--pid', type=int, help='process to profile (all current threads, no child processes)')
    parser.add_argument('--cgroup', help='absolute cgroup v2 directory to profile')
    parser.add_argument('--duration', type=float, default=10, help='profile duration, 0.2–300 seconds')
    parser.add_argument('--label', default='', help='workload/build description saved with a profile')
    parser.add_argument('--scheduler',action='store_true',help='opt in to filtered scheduler tracing and off-CPU stacks (requires perf permission)')
    parser.add_argument('--no-perf', action='store_true', help='capture counters without CPU call stacks')
    parser.add_argument('--compare', nargs=2, metavar=('BASELINE', 'CANDIDATE'), help='compare two saved session directories')
    parser.add_argument('--outcome',action='append',default=[],metavar='NAME=VALUE',help='externally measured workload result; include units in name, e.g. p99_ms=12.5')
    args = parser.parse_args()
    if args.profile or args.compare:
        if args.profile and args.compare:
            parser.error('choose --profile or --compare')
        if any((args.replay,args.record,args.control,args.once,args.json,args.report,args.incidents)):
            parser.error('profiling/comparison cannot be combined with dashboard, recording or snapshot modes')
        if args.compare and (args.pid is not None or args.cgroup or args.no_perf or args.label or args.outcome or args.duration != 10 or args.scheduler):
            parser.error('capture options require --profile, not --compare')
        from .profiling import capture, compare
        try:
            outcomes={}
            for item in args.outcome:
                name,value=item.split('=',1)
                if not name:raise ValueError('Outcome name is empty')
                outcomes[name]=float(value)
            result = (capture(args.profile,pid=args.pid,cgroup=args.cgroup,duration=args.duration,label=args.label,perf=not args.no_perf,outcomes=outcomes,scheduler=args.scheduler)
                      if args.profile else compare(*args.compare))
            print(json.dumps(result,indent=2,allow_nan=False))
            return 0 if not args.profile or result['status']=='complete' else 1
        except KeyboardInterrupt:
            return 130
        except (OSError,ValueError) as exc:
            print('Resource Lens: '+str(exc),file=sys.stderr)
            return 1
    if args.pid is not None or args.cgroup or args.no_perf or args.label or args.duration != 10 or args.outcome or args.scheduler:
        parser.error('--pid/--cgroup/--duration/--label/--no-perf/--scheduler require --profile')
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
    if args.report_identifiers and not args.report:
        parser.error("--report-identifiers requires --report")
    if args.helper and not args.control:
        parser.error("--helper requires --control")
    if args.incidents and args.replay:
        parser.error("--incidents is live-only")
    if args.report and (args.control or args.record or args.incidents or args.json or args.once):
        parser.error("--report cannot be combined with control/record/incidents/json/once")
    if args.replay and (args.control or args.record):
        parser.error('--replay is read-only and cannot be combined with --control or --record')
    if sys.platform != "linux":
        parser.error("Resource Lens requires Linux /proc and /sys")
    if not (args.once or args.json or args.report) and not (sys.stdin.isatty() and sys.stdout.isatty()):
        parser.error("interactive mode requires a terminal; use --once or --json --count 1")
    recorder, replay, incidents, collector = None, None, None, None
    try:
        replay = Replay(args.replay) if args.replay else None
        recorder = Recorder(args.record, args.record_max_mb * 1024 * 1024) if args.record else None
        collector = None if replay else Collector(nvidia=not args.no_nvidia)
        prefs = {}
        if not args.no_config:
            try:
                prefs = load_preferences()
            except (OSError,ValueError) as exc:
                print('Preferences ignored: '+str(exc),file=sys.stderr)
        if args.incidents:
            incidents = IncidentCapture(args.incidents,args.alert_threshold,args.alert_duration,args.alert_cooldown,interval=args.interval)
        if args.report:
            view = View()
            if replay:
                view.investigation.events = list(replay.events)
                for index in range(max(0,len(replay.offsets)-600),len(replay.offsets)):
                    view.update(replay.sample_at(index))
            else:
                collector.sample()
                time.sleep(args.interval)
                view.update(collector.sample())
            print(export_report(view,args.report,redact=not args.report_identifiers))
            return 0
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
                if incidents:
                    incidents.observe(sample)
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
            run(collector, args.interval, ascii=args.ascii, control=args.control, recorder=recorder, replay=replay, preferences=prefs, incidents=incidents, helper=args.helper)
    except (KeyboardInterrupt, BrokenPipeError):
        return 0
    except (OSError, ValueError, ControlError) as exc:
        print(f'Resource Lens: {exc}', file=sys.stderr)
        return 1
    except curses.error as exc:
        print(f"Terminal unavailable: {exc}. Try TERM=xterm-256color or --once.", file=sys.stderr)
        return 1
    finally:
        if collector:
            collector.close()
        if incidents and (args.once or args.json):
            incidents.close()
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
