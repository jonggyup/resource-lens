#!/usr/bin/env python3
"""Opt-in live placement/Undo test on a newly created bounded disposable service."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from resourcelens.placement import PlacementSession
from resourcelens.collect import Collector


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if not args.run:
        parser.error('Pass --run to create and control a disposable service.')
    unit = 'rlplacement-' + uuid.uuid4().hex[:12] + '.service'
    def cmd(*args, check=True):
        return subprocess.run(args, text=True, capture_output=True, check=check, timeout=10)
    slice_name = unit.removesuffix('.service') + '.slice'
    output = {'unit': unit, 'slice': slice_name}
    try:
        cmd('systemd-run', '--collect', '--unit=' + unit, '--slice=' + slice_name, '--property=RuntimeMaxSec=90',
            '--property=TimeoutStopSec=3', sys.executable, '-c',
            'import subprocess,time; subprocess.Popen(["sleep","90"]); time.sleep(90)')
        collector = Collector(nvidia=False)
        target = None
        for _ in range(20):
            target = next((g for g in collector.sample()['workloads'] if g['unit'] == unit), None)
            if target and len(target['pids']) >= 2:
                break
            time.sleep(.1)
        assert target and len(target['pids']) >= 2
        session = PlacementSession()
        before = session.backend.snapshot(target)
        eligible = before['eligible']
        assert eligible
        output['before'] = before
        event = session.apply(session.prepare(target, [eligible[0]]))
        output['applied'] = event['after']
        affinities = {str(pid): sorted(os.sched_getaffinity(pid)) for pid in target['pids']}
        output['process_affinities'] = affinities
        assert all(value == [eligible[0]] for value in affinities.values())
        try:
            event = session.undo()
            output['undo'] = event.get('after')
            output['undo_status'] = event['status']
        except Exception as exc:
            output['undo_error'] = str(exc)
        output['restored_process_affinities'] = {str(pid): sorted(os.sched_getaffinity(pid)) for pid in target['pids']}
        if 'undo_error' not in output:
            assert all(value == eligible for value in output['restored_process_affinities'].values())
        if len(eligible) >= 3:
            cmd('systemctl', '--runtime', 'set-property', slice_name, f'AllowedCPUs={eligible[0]},{eligible[1]}')
            parent_info = session.backend.snapshot(target)
            assert parent_info['eligible'] == eligible[:2]
            from resourcelens.controls import ControlError
            try:
                session.prepare(target, [eligible[-1]])
            except ControlError:
                output['ancestor_exclusion_rejected'] = True
            else:
                raise AssertionError('CPU outside ancestor set was accepted')
            session.backend.set(target, eligible[:2])
            session.apply(session.prepare(target, [eligible[0]]))
            output['explicit_restore'] = session.undo()['after']
            assert output['explicit_restore']['allowed'] == eligible[:2]
        print(json.dumps(output, indent=2))
        if 'undo_error' in output:
            raise SystemExit(1)
    finally:
        cmd('systemctl', 'stop', unit, check=False)
        cmd('systemctl', 'reset-failed', unit, check=False)
        cmd('systemctl', 'stop', slice_name, check=False)
        # Remove only our own runtime drop-ins if the transient unit left any.
        import shutil
        for name in (unit, slice_name):
            path = Path('/run/systemd/system') / (name + '.d')
            if path.exists():
                shutil.rmtree(path)
        cmd('systemctl', 'daemon-reload', check=False)


if __name__ == '__main__':
    main()
