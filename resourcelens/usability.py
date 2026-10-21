"""Presentation preferences, capability explanations and portable investigation reports."""
import json
import os
from pathlib import Path
import tempfile
import time


def config_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'resource-lens/preferences.json'


def state_dir():
    return Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'resource-lens'


PREFERENCES = {'application_mode': ('processes','cgroups'), 'page': range(10), 'sort': ('cpu', 'memory', 'pid', 'name'),
               'workload_sort': ('cpu', 'memory', 'name', 'io'),
               'filter': str, 'workload_filter': str, 'overview_expanded': bool, 'process_tree': bool, 'app_tab': range(5)}


def preferences(values):
    if not isinstance(values, dict):
        raise ValueError('Preferences must be an object')
    # Migrate the removed duplicate Hardware page to expanded Overview.
    if values.get('page') == 10:
        values = {**values, 'page': 0, 'overview_expanded': True}
    result = {}
    for key, allowed in PREFERENCES.items():
        value = values.get(key)
        if allowed in (str, bool):
            if type(value) is allowed:
                result[key] = value[:80] if allowed is str else value
        elif value in allowed:
            result[key] = value
    return result


def load_preferences(path=None):
    path = Path(path) if path else config_path()
    if not path.exists():
        return {}
    if path.stat().st_size > 65536:
        raise ValueError('Preferences file exceeds 64 KiB')
    return preferences(json.loads(path.read_text()))


def save_preferences(view, path=None):
    path = Path(path) if path else config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.preferences-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(preferences({key: getattr(view, key) for key in PREFERENCES}), file, indent=2)
            file.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(path)


def capability_lines(sample, error=None, replay=False):
    meta = sample.get('collector', {})
    caps = sample.get('capabilities', {})
    ms = meta.get('duration_ms')
    cpu = meta.get('cpu_ms')
    rss = meta.get('rss_bytes')
    lines = ['CAPABILITIES / COLLECTION HEALTH',
             'Recorded capabilities; not current permissions.' if replay else 'Permission checks describe this process; systemd authorization is checked on apply.',
             f"Snapshot age: {max(0, time.time()-sample['timestamp']):.1f}s" if not replay else 'Historical snapshot (replay)',
             f"Collector wall: {ms:.1f} ms | CPU: {cpu:.1f} ms | monitor peak RSS: {rss/1048576:.1f} MiB" if all(v is not None for v in (ms,cpu,rss)) else 'Collector overhead: unavailable in this sample',
             'Collector error: ' + (error or 'none reported'), '', '-- Available measurements --']
    for title, available, reason in [
        ('CPU usage', bool(sample.get('cpu')), 'Needs readable /proc/stat'),
        ('Memory', sample.get('memory', {}).get('total') is not None, 'Needs readable /proc/meminfo'),
        ('GPU utilization', any(g.get('busy_pct') is not None for g in sample.get('gpus', [])), 'Driver may not expose utilization; NVIDIA needs nvidia-smi'),
        ('CPU governors', bool(sample.get('cpu_hardware', {}).get('policies')), 'Requires kernel cpufreq driver/policies'),
        ('Thermal events', bool(sample.get('thermal_throttling')), 'Requires exposed thermal_throttle counters'),
        ('cgroup v2', caps.get('cgroup_v2'), 'Requires a unified cgroup hierarchy'),
        ('systemd controls', caps.get('systemctl'), 'Requires systemctl and an accessible owning manager')]:
        lines.append(f"{title}: {'available' if available else 'unavailable/unknown'}" + ('' if available else ' — ' + reason))
    lines += ['', '-- Resource controls --', 'Controllers exposed: ' + (caps.get('controllers') or 'unknown'),
              'Writable governor files: ' + str(caps.get('writable_governors', 'unknown')),
              'Writable CPU online files: ' + str(caps.get('writable_online', 'unknown')),
              f"Skipped/exited/unreadable processes: {sample.get('processes_skipped', 'unknown')}",
              'Workload files may be inactive despite controller availability at the root.',
              'Press C to enable control menus. Monitoring needs no elevated permissions.',
              'If a change is denied, use manager authorization or an explicitly installed helper.']
    energy=sample.get('energy')
    if energy:
        lines += ['', 'HARDWARE ENERGY: '+energy['status'],energy['scope']]
        for domain in energy.get('domains',[]):
            lines += [domain['label']+' ['+domain['source']+']: '+domain['status'],
                      '  '+domain['id'], '  Parent: '+domain['parent']]
            if domain.get('reason'):lines.append('  '+domain['reason'])
        lines += energy.get('warnings',[])
    return lines + ['Warning: ' + item for item in sample.get('warnings', [])]


def export_report(view, path=None, redact=True):
    directory = state_dir() / 'reports'
    if path is None:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / ('report-' + str(time.time_ns()) + '.json')
    from .investigation import metrics
    sample = view.snapshot or {}
    events = []
    for event in view.investigation.events:
        means, counts = view.investigation.compare(event)
        events.append(dict(timestamp=event['timestamp'], status=event['status'],
                           label='Event' if redact else event['label'],
                           target=None if redact else event.get('target'),
                           sampled_means=means, available_samples=counts))
    report = dict(format='resource-lens-report', version=1, redacted=redact,
                  hostname=None if redact else sample.get('hostname'), timestamp=sample.get('timestamp'),
                  events=events, collector=sample.get('collector'),
                  current_metrics=metrics(sample) if sample else {},
                  retained_samples=len(view.investigation.samples),
                  notes=['30-second before/after sample means, not causal evidence.',
                         'Only retained available samples contribute; counts accompany each metric.',
                         'Redacted export omits host names, paths, process names and marker labels.'])
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as file:
        json.dump(report, file, indent=2, allow_nan=False)
        file.write('\n')
    return str(path)


def tree_rows(rows):
    """Iterative forest walk; tolerate exited parents and malformed cycles."""
    by_pid = {p['pid']: p for p in rows}
    children = {}
    roots = []
    for row in rows:
        parent = row.get('ppid')
        if parent in by_pid and parent != row['pid']:
            children.setdefault(parent, []).append(row)
        else:
            roots.append(row)
    result, seen = [], set()
    for root in roots + rows:
        stack = [(root, 0)]
        while stack:
            row, depth = stack.pop()
            if row['pid'] in seen:
                continue
            seen.add(row['pid'])
            result.append((row, depth))
            stack.extend((child, depth+1) for child in reversed(children.get(row['pid'], [])))
    return result
