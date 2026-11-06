"""Time-weighted descriptive windows over recorded observations; no live reads."""
from .evidence import finite


def scope(sample):
    return sample.get('collection_scope', {}).get('id')


def compatible(sample, target, scope_id):
    if scope_id is not None and scope(sample) != scope_id:
        return False
    if not target:
        return True
    group = next((g for g in sample.get('workloads', []) if g.get('id') == target.get('id')), None)
    return bool(group and all(group.get(key) == target[key] for key in ('inode', 'device', 'generation')
                              if target.get(key) is not None))


def compare_windows(samples, event, metrics, seconds=30, events=()):
    """Rates describe the preceding elapsed_s interval; gauges use left hold.

    Both ends must have compatible identities. Holds stop at the next observation
    and at 2.5 seconds, so missing observations cannot extend a gauge indefinitely.
    Rate intervals are clipped to elapsed_s and never extend before the previous
    observation. Overlaps are counted once. Unknown durations provide no coverage.
    """
    if not finite(seconds) or seconds <= 0:
        raise ValueError('Comparison duration must be positive')
    start = event.get('started_at', event['timestamp'])
    start = start if finite(start) else event['timestamp']
    verified = event.get('verified_at', start if event.get('status') == 'marker' else None)
    verified = verified if finite(verified) and verified >= start else None
    settling = event.get('settling_s', 0)
    settling = max(0,settling) if finite(settling) else 0
    post = verified + settling if verified is not None else None
    windows = [(start-seconds, start), (post, post+seconds) if post is not None else (None, None)]
    # Other control attempts interrupt observation, even if they affect a wider
    # scope than this target. Keep requested duration unchanged in the denominator.
    bounds = [list(w) for w in windows]
    for other in events:
        if other is event or not other.get('domain'):
            continue
        stamp = other.get('started_at', other['timestamp'])
        end = other.get('completed_at', stamp)
        if end <= start and end > bounds[0][0]:
            bounds[0][0] = end
        elif stamp >= start and post is not None:
            bounds[1][1] = min(bounds[1][1], max(post, stamp))
    target, scope_id = event.get('target'), event.get('scope_id')
    samples = list(samples)
    clock_invalid = any(b['timestamp'] < a['timestamp'] for a,b in zip(samples,samples[1:]))
    results = []
    for index, (left, right) in enumerate(bounds):
        values = {}
        if left is not None and not clock_invalid:
            for previous, current in zip(samples, samples[1:]):
                t0, t1 = previous['timestamp'], current['timestamp']
                if t1 <= t0 or t1 < left or t0 > right or scope(previous) != scope(current):
                    continue
                if not all(compatible(s, target, scope_id) for s in (previous, current)):
                    continue
                elapsed = current.get('elapsed_s')
                previous_values = metrics(previous, target)
                for key, value in metrics(current, target).items():
                    if key not in previous_values:
                        continue  # A newly observed device/domain has no compatible interval start.
                    gauge = key.endswith(('bytes', 'PSI %'))
                    if gauge:
                        value = previous_values.get(key)
                        a, b = t0, min(t1, t0+2.5)
                    elif finite(elapsed) and elapsed > 0:
                        a, b = max(t0, t1-elapsed), t1
                    else:
                        continue
                    a, b = max(a, left), min(b, right)
                    if not finite(value) or b <= a:
                        continue
                    entry = values.setdefault(key, dict(total=0., covered_seconds=0., samples=0,min=value,max=value))
                    duration = b-a
                    entry['total'] += value*duration
                    entry['covered_seconds'] += duration
                    entry['samples'] += 1
                    entry['min']=min(entry['min'],value);entry['max']=max(entry['max'],value)
        for entry in values.values():
            entry['mean'] = entry.pop('total') / entry['covered_seconds']
            entry['coverage'] = min(1., entry['covered_seconds']/seconds)
            entry['complete'] = entry['coverage'] >= .8
        results.append(dict(start=windows[index][0], end=windows[index][1], metrics=values,
                            available_start=left, available_end=right, requested_seconds=seconds))
    return dict(before=results[0], after=results[1], transition_start=start,
                transition_end=verified, settling_s=settling,
                post_available=post is not None,
                limitations=(['Observation clock moved backwards; retained window coverage is unavailable.'] if clock_invalid else []) +
                            ['Rates assume a constant rate within each recorded interval; boundary splits are approximate.',
                             'Cgroup identity is checked where recorded; ongoing systemd InvocationID is not sampled.',
                             'Other control attempts interrupt windows; unrelated workload changes are not controlled.'],
                method='interval-overlap rates; left-held gauges (maximum 2.5 seconds)')


def comparison_lines(report):
    seconds = report['before']['requested_seconds']
    lines = [f'{seconds:g}-second windows; time-weighted values and per-metric coverage.',
             'Apply/verification and settling time are excluded. Under 80% coverage: incomplete.',
             'Resource changes describe observations; request latency and causality are not measured.']
    if not report['post_available']:
        lines.append('Post-change window unavailable: this attempt has no verified completion.')
    lines.extend(report['limitations'])
    before, after = report['before']['metrics'], report['after']['metrics']
    for key in sorted(set(before) | set(after)):
        lines.append('')
        lines.append(key)
        for label, item in (('Before', before.get(key)), ('After', after.get(key))):
            lines.append(f"  {label}: " + (f"{item['mean']:.2f}; {item['covered_seconds']:.1f}s / {seconds:g}s "
                         f"({100*item['coverage']:.0f}% coverage, " + ('complete' if item['complete'] else 'incomplete') +
                         f"); min/max {item['min']:.2f}/{item['max']:.2f}; {item['samples']} intervals"
                         if item else f'unknown; 0s / {seconds:g}s coverage'))
        if key in before and key in after:
            a, b = before[key]['mean'], after[key]['mean']
            relative = f'{100*(b-a)/abs(a):+.1f}%' if a else 'baseline is zero; relative change undefined'
            lines.append(f'  Change: {b-a:+.2f}; {relative}')
    if not before and not after:
        lines.append('No compatible measurements with known intervals in these windows.')
    return lines
