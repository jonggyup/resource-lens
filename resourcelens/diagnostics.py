"""Deterministic CPU explanations from recorded evidence. No live reads or UI."""

from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import PurePosixPath

from .model import TargetRef, source_quality


RULE_VERSION = 1


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def context_for(sample, request):
    """Use an exact captured request or a recorded ancestor with matching identity."""
    context = sample.get('target_context')
    if not isinstance(context, dict):
        return None
    if context.get('request') == list(request):
        return context
    if request[0] != 'cgroup':
        return None
    try:
        path = request[1]
        if len(request) > 4 and request[4] == 'visible':
            path = str(PurePosixPath(context['mount']['root']) / path.lstrip('/'))
        for index, node in enumerate(context['ancestors']):
            identity = TargetRef.from_dict(node['identity'])
            if (node['path'] == path and identity.identity.inode == request[2] and
                    (request[3] is None or identity.identity.device == request[3])):
                ancestors = context['ancestors'][index:]
                limits = dict(context.get('limits', {}))
                for filename,prefix,field in (('cpu.max','quota','quota_pct'),('memory.max','memory','memory_max'),
                                               ('memory.high','memory_high','memory_high'),('memory.swap.max','swap','swap_max')):
                    rows = [(n, n.get('limits', {}).get(filename, {})) for n in ancestors]
                    known = [(r.get('value'), n['path']) for n, r in rows if r.get('state') == 'known' and finite(r.get('value'))]
                    value, origin = min(known, default=(None, None))
                    complete = all(r.get('state') in ('known', 'unlimited') for _, r in rows)
                    limits[field] = value
                    limits[prefix+'_source'] = origin
                    limits[prefix+'_state'] = 'known' if known else 'unlimited' if complete and context.get('ancestry_visibility') == 'to_system_root' else 'unknown'
                service = context.get('service', {})
                service_path = service.get('path') or ''
                if path != service_path and not path.startswith(service_path.rstrip('/') + '/'):
                    name = PurePosixPath(path).name
                    service = dict(path=path, unit=name if name.endswith(('.service', '.scope')) else None, manager=None)
                return dict(context, request=list(request), target=node['identity'],
                            selected_group=node['identity'], exact_cgroup=path,
                            ancestors=ancestors, limits=limits, service=service)
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    return None


def request_current(sample, request):
    try:
        if request[0] == 'process':
            return any((p['pid'], p.get('starttime'), p.get('cgroup')) == tuple(request[1:4])
                       for p in sample.get('processes', []))
        field = 'cgroup_tree' if len(request) > 4 and request[4] == 'visible' else 'workloads'
        rows = sample.get('cgroup_tree', {}).get('nodes', []) if field == 'cgroup_tree' else sample.get('workloads', [])
        if any(row.get('path', row.get('id')) == request[1] and row.get('inode') == request[2] and
               (request[3] is None or row.get('device') == request[3]) for row in rows):
            return True
        return context_for(sample, request) is not None
    except (KeyError, TypeError, IndexError):
        return False


@dataclass(frozen=True)
class Finding:
    id: str
    rule_id: str
    rule_version: int
    target: dict | None
    title: str
    explanation: str
    relevance: str
    evidence: tuple
    window: dict
    limitations: tuple
    next_actions: tuple

    def to_dict(self):
        return asdict(self)


def finding(rule, target, title, explanation, relevance='observed', evidence=(),
            window=None, limitations=(), actions=('show_constraints', 'profile'),origin=None):
    identity = target.get('identity') if target else None
    digest = hashlib.sha256(json.dumps([rule, identity]+([origin] if origin else []), sort_keys=True).encode()).hexdigest()[:16]
    return Finding(digest, rule, RULE_VERSION, target, title, explanation, relevance,
                   tuple(evidence), window or {}, tuple(limitations), tuple(actions))


def project(sample):
    """Retain only bounded CPU evidence, not raw memory maps or complete samples."""
    context = sample.get('target_context')
    if not isinstance(context, dict):
        return None
    try:
        target = TargetRef.from_dict(context['target'])
        group = TargetRef.from_dict(context['selected_group'])
        target, group = TargetRef(target.kind, target.identity), TargetRef(group.kind, group.identity)
        quality = source_quality(sample, 'target_context')
        scope = sample.get('collection_scope', {}).get('id', target.identity.scope_id)
        request = tuple(context['request'])
        if (scope != target.identity.scope_id or scope != group.identity.scope_id or quality.scope_id != scope or
                quality.status in ('error', 'unavailable', 'unknown') or not context.get('ancestors')):
            return None
        if request[0] == 'process' and (target.kind != 'process' or
                (target.identity.pid, target.identity.start_ticks, context['exact_cgroup']) != request[1:4]):
            return None
        if request[0] == 'cgroup' and (target != group or group.identity.inode != request[2] or
                request[3] is not None and group.identity.device != request[3]):
            return None
        stamp, wall = context['observed_at_monotonic'], context['observed_at_wall']
        if not finite(stamp) or not finite(wall) or not finite(quality.observed_at_wall):
            return None
        wall = min(wall, quality.observed_at_wall)
        nodes = []
        for node in context['ancestors'][:32]:
            ref = TargetRef.from_dict(node['identity'])
            ref = TargetRef(ref.kind, ref.identity)
            if ref.kind != 'cgroup' or ref.identity.scope_id != scope or ref.identity.path != node['path']:
                return None
            stat = node.get('counters', {}).get('cpu.stat')
            deltas = node.get('deltas', {}).get('cpu.stat', {})
            quota = node.get('limits', {}).get('cpu.max', {})
            if not isinstance(deltas, dict) or not isinstance(quota, dict):
                return None
            quota = {key: quota.get(key) for key in ('state', 'value', 'reason')}
            if quota['state'] not in ('known', 'unlimited', 'unavailable', 'unknown', None):
                return None
            if quota['value'] is not None and not finite(quota['value']):
                return None
            raw_quota = node.get('files', {}).get('cpu.max', {}).get('value')
            if raw_quota is not None and (not isinstance(raw_quota, str) or len(raw_quota) > 256):
                return None
            if quota['state'] in ('known', 'unlimited'):
                parts = (raw_quota or '').split()
                if len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) <= 0:
                    return None
                if quota['state'] == 'known':
                    if (not parts[0].isdigit() or not finite(quota['value']) or
                            not math.isclose(100 * int(parts[0])/int(parts[1]), quota['value'])):
                        return None
                elif parts[0] != 'max' or quota['value'] is not None:
                    return None
            counter_values = [stat.get(k) for k in ('throttled_usec', 'nr_throttled', 'nr_periods')] if isinstance(stat, dict) else []
            counter_values += [deltas.get(k) for k in ('throttled_usec', 'nr_throttled', 'nr_periods')]
            if any(value is not None and (type(value) is not int or value < 0) for value in counter_values):
                return None
            end = node.get('observed_at_monotonic', stamp)
            interval = node.get('interval_s')
            nodes.append(dict(identity=ref.to_dict(), path=node['path'], quota=deepcopy(quota),
                raw_quota=raw_quota,
                stat={k: stat.get(k) for k in ('throttled_usec', 'nr_throttled', 'nr_periods')} if isinstance(stat, dict) else {},
                delta={k: deltas.get(k) for k in ('throttled_usec', 'nr_throttled', 'nr_periods')},
                reason=node.get('files', {}).get('cpu.stat', {}).get('reason'),
                interval=interval, end=end))
        if not nodes or TargetRef.from_dict(nodes[0]['identity']) != group:
            return None
        return dict(request=request, target=target.to_dict(), group=group.to_dict(), scope=scope,
                    stamp=stamp, wall=wall, sequence=quality.sequence, nodes=nodes,
                    visibility=context.get('ancestry_visibility', 'unknown'))
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError):
        return None


class DiagnosticHistory:
    def __init__(self, max_frames=120, max_bytes=2 * 1024 * 1024):
        self.max_frames, self.max_bytes = max_frames, max_bytes
        self.frames, self.sizes = deque(), deque()
        self.bytes = 0
        self.signature = None
        from .resource_findings import ResourceHistory
        self.resources=ResourceHistory(max_frames,max_bytes)

    def clear(self):
        self.resources.clear()
        self.frames.clear(); self.sizes.clear()
        self.bytes = 0
        self.signature = None

    def observe(self, sample):
        self.resources.observe(sample)
        frame = project(sample)
        signature = (frame['scope'], frame['request'], frame['stamp'], frame['sequence']) if frame else ('missing',)
        if signature == self.signature:
            return
        self.signature = signature
        frame = frame or {'nodes': [], 'request': None}
        size = len(json.dumps(frame, allow_nan=False).encode())
        self.frames.append(frame); self.sizes.append(size); self.bytes += size
        while self.frames and (len(self.frames) > self.max_frames or self.bytes > self.max_bytes):
            self.frames.popleft(); self.bytes -= self.sizes.popleft()


def _run(frames, request, selected, origin, max_gap=2.5):
    run = []
    for frame in frames:
        if request[0] == 'process' and (frame.get('request') != tuple(request) or frame.get('group') != selected):
            run = []; continue
        node = next((n for n in frame['nodes'] if n['identity'] == origin), None)
        if node is None:
            run = []; continue
        delta, total, interval, end = node['delta'].get('throttled_usec'), node['stat'].get('throttled_usec'), node['interval'], node['end']
        if (node['reason'] or node['quota'].get('state') != 'known' or type(delta) is not int or delta <= 0 or type(total) is not int or total < delta or
                not finite(interval) or not 0 < interval <= max_gap or not finite(end)):
            run = []; continue
        if run and (not 0 < end - run[-1]['end'] <= max_gap or abs(end - interval - run[-1]['end']) > .05 or
                    total-delta != run[-1]['total'] or node['raw_quota'] != run[-1]['node']['raw_quota']):
            run = []
        run.append(dict(delta=delta, total=total, interval=interval, end=end, wall=frame['wall'], node=node))
    return run


def _cpu_explain(history, sample, request, *, now=None, stale_after=5):
    """No host usage/PSI heuristic can establish a selected target's throttling."""
    target = None
    def insufficient(message, evidence=()):
        return finding('insufficient_evidence', target, 'Evidence is incomplete', message,
                       'insufficient_evidence', evidence=evidence,
                       actions=('show_evidence', 'health', 'profile'))
    context = context_for(sample, request)
    if not request_current(sample, request) or context is None:
        return [insufficient('Exact target context is not recorded or the selected identity/membership changed.')]
    adapted = dict(sample, target_context=context)
    frame = project(adapted)
    if frame is None:
        return [insufficient(context.get('message', 'Required identity, source quality or CPU evidence is unavailable.'))]
    target = frame['target']
    reference = sample['timestamp'] if now is None else now
    age = reference - frame['wall']
    if not finite(age) or age < -.1 or age > stale_after:
        return [insufficient('The recorded context is stale or its clock is inconsistent. Refresh evidence before interpreting restriction counters.')]
    results = []
    finite_quotas = [n for n in frame['nodes'] if n['quota'].get('state') == 'known' and finite(n['quota'].get('value'))]
    if finite_quotas:
        limit = min(finite_quotas, key=lambda n: n['quota']['value'])
        local = limit['identity'] == frame['group']
        results.append(finding('configured_quota', target,
            ('CPU ceiling configured: ' if local else 'Inherited CPU ceiling: ') + f"{limit['quota']['value']:g}%",
            'The smallest readable quota is at ' + limit['path'] + '. 100% equals one logical CPU.',
            evidence=({'path': limit['path'], 'file': 'cpu.max', 'value': limit['raw_quota'], 'quota_pct': limit['quota']['value']},),
            limitations=('A configured quota alone does not establish throttling or request delay.',
                         'Ancestor capacity is shared with other descendants; hidden restrictions may be tighter.')))
    for node in finite_quotas:
        run = _run(history.frames, request, frame['group'], node['identity'])
        seconds = sum(item['interval'] for item in run)
        if len(run) < 3 or seconds < 3 or run[-1]['end'] != node['end']:
            continue
        local = node['identity'] == frame['group']
        events = tuple(dict(path=node['path'], file='cpu.stat', counter='throttled_usec',
                            before=item['total']-item['delta'], after=item['total'], delta=item['delta'],
                            interval_s=item['interval'], interval_start=item['end']-item['interval'],
                            interval_end=item['end'], observed_at_wall=item['wall'], scope=frame['scope']) for item in run)
        covered = sum(e['interval_s'] for e in events)
        results.append(finding('local_throttling' if local else 'ancestor_throttling:' + node['path'], target,
            'Group quota throttling observed' if local else 'Ancestor quota activity observed',
            node['path'] + f": throttling increased in {len(events)} consecutive measured intervals ({covered:.1f}s covered).",
            'observed' if local else 'possible', evidence=events,
            window=dict(start=events[0]['interval_start'], end=events[-1]['interval_end'], covered_seconds=covered,
                        intervals=len(events), throttled_ms_per_second=sum(e['delta'] for e in events)/1000/covered),
            limitations=('Group counters include other processes and descendants; this is not per-process wait time.',
                         'Ancestor activity is shared and may constrain a child; it does not assign delay to that child.',
                         'Counter time is reported in ms/s and is not a universally bounded wall-time percentage.'),
            actions=('show_evidence', 'show_constraints', 'waits', 'open_ancestor')))
        if len(results) >= 4:
            break
    if not any(f.rule_id == 'local_throttling' for f in results):
        local = frame['nodes'][0]
        if (not local['reason'] and local['delta'].get('throttled_usec') == 0 and
                type(local['stat'].get('throttled_usec')) is int and local['stat']['throttled_usec'] >= 0 and
                finite(local['interval']) and local['interval'] > 0):
            results.append(finding('no_local_increase', target, 'No local quota increase in the latest interval',
                'The local throttle counter did not increase during the latest measured interval.',
                evidence=({'path': local['path'], 'file': 'cpu.stat', 'value': 'throttled_usec delta = 0'},),
                limitations=('This does not rule out earlier events, ancestor restrictions or other kinds of waiting.',),
                actions=('show_evidence', 'waits')))
            message = None
        elif local['reason'] or type(local['delta'].get('throttled_usec')) is not int or type(local['stat'].get('throttled_usec')) is not int:
            message = 'Valid quota-counter interval evidence is not available yet (warmup, reset, missing counters or read failure).'
        else:
            message = 'No persistent quota increase is established: require three valid consecutive intervals spanning at least three seconds.'
        if message:
            results.append(insufficient(message))
    if frame['visibility'] != 'to_system_root':
        results.append(finding('ancestry_coverage', target, 'Ancestor coverage is limited',
            'Only the visible/readable ancestry was inspected: ' + frame['visibility'].replace('_', ' ') + '.',
            'insufficient_evidence', actions=('show_constraints', 'health'),
            limitations=('Missing or hidden limits are not unlimited settings.',)))
    return results


def explain(history,sample,request,*,now=None,stale_after=5):
    from .resource_findings import explain as resource_explain
    return (_cpu_explain(history,sample,request,now=now,stale_after=stale_after)+
            resource_explain(history.resources,sample,request,now=now,stale_after=stale_after))
