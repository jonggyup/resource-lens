"""Bounded, read-only exact-target inspection. Called by the detail worker only."""

from collections import OrderedDict
from pathlib import Path, PurePosixPath
import re
import math
import time

from .model import CgroupIdentity, ProcessIdentity, TargetRef, Coverage
from .sampling import Observation
from .sources import read_text, ReadResult, error_reason
from .workloads import attribution, membership


LIMIT_FILES = ('cpu.max', 'cpu.weight', 'cpuset.cpus.effective', 'memory.high',
               'memory.max', 'memory.swap.max', 'memory.low', 'memory.min', 'io.max')
COUNTER_FILES = ('cpu.stat', 'memory.current', 'memory.events', 'memory.events.local',
                 'memory.stat', 'memory.swap.current', 'memory.peak', 'cpu.pressure', 'memory.pressure', 'io.pressure', 'cgroup.type')


def _unescape(value):
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)


def mount_context(text, root):
    """Map namespace-visible membership to a configured cgroup2 mount/subtree.

    A mount root of '/' does not prove the initial cgroup namespace is visible.
    Do not guess a host hierarchy from PID 1's namespace inside a container.
    """
    matches = []
    for line in text.splitlines():
        left, sep, right = line.partition(' - ')
        fields, filesystem = left.split(), right.split()
        if not sep or len(fields) < 6 or len(filesystem) < 3 or filesystem[0] != 'cgroup2':
            continue
        point = Path(_unescape(fields[4]))
        try:
            relative = root.relative_to(point)
        except ValueError:
            continue
        visible = str(PurePosixPath(_unescape(fields[3])) / relative.as_posix())
        if not visible.startswith('/') or '..' in PurePosixPath(visible).parts:
            continue
        matches.append((len(point.parts), dict(root=visible, mountpoint=str(point),
            ancestry_visibility='to_visible_root',
            memory_events_scope='local' if 'memory_localevents' in filesystem[2].split(',') else 'subtree')))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def parse_limit(name, result):
    if result.reason:
        return dict(state='unavailable', value=None, reason=result.reason)
    value = result.text
    if name == 'cpu.max':
        parts = value.split()
        if len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) <= 0:
            return dict(state='unavailable', value=None, reason='parse_error')
        if parts[0] == 'max':
            return dict(state='unlimited', value=None, reason=None)
        if parts[0].isdigit():
            return dict(state='known', value=100 * int(parts[0]) / int(parts[1]), reason=None)
    elif name.startswith('memory.'):
        if value == 'max':
            return dict(state='unlimited', value=None, reason=None)
        if value.isdigit():
            return dict(state='known', value=int(value), reason=None)
    elif name == 'cpu.weight':
        if value.isdigit() and 1 <= int(value) <= 10000:
            return dict(state='known', value=int(value), reason=None)
    elif name == 'io.max':
        valid = True
        for line in value.splitlines():
            parts = line.split()
            valid &= bool(parts and re.fullmatch(r'\d+:\d+', parts[0]) and len(parts) > 1)
            for token in parts[1:]:
                key, sep, raw = token.partition('=')
                valid &= bool(sep and key in ('rbps', 'wbps', 'riops', 'wiops') and (raw == 'max' or raw.isdigit()))
        if valid:
            return dict(state='known' if value else 'unlimited', value=value or None, reason=None)
    elif name == 'cpuset.cpus.effective' and value and re.fullmatch(r'\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*', value):
        return dict(state='known', value=value, reason=None)
    return dict(state='unavailable', value=None, reason='parse_error')


def counters(result):
    if result.reason:
        return None
    pairs = [line.split() for line in result.text.splitlines()]
    if not pairs or any(len(pair) != 2 or not pair[1].isdigit() for pair in pairs):
        return None
    return {key: int(value) for key, value in pairs}


def pressure(result):
    if result.reason:
        return None
    parsed = {}
    try:
        for line in result.text.splitlines():
            fields = line.split()
            if not fields or fields[0] not in ('some', 'full'):
                return None
            values = dict(token.split('=', 1) for token in fields[1:])
            values = {key: int(value) if key == 'total' else float(value) for key, value in values.items()}
            if 'avg10' not in values or any(not math.isfinite(value) or value < 0 for value in values.values()):
                return None
            parsed[fields[0]] = values
        return parsed or None
    except (ValueError, OverflowError):
        return None


class TargetResolver:
    def __init__(self, proc, root, scope_id, *, mount=None, max_depth=32,
                 max_bytes=1048576, budget=.15, clock=time.monotonic):
        self.proc, self.root = Path(proc), Path(root).resolve()
        self.scope_id, self.mount_override = scope_id, mount
        self.max_depth, self.max_bytes, self.budget = max_depth, max_bytes, budget
        self.clock = clock
        self.identities, self.previous = OrderedDict(), OrderedDict()
        self.next_generation = 0
        self.root_identity = None

    def _identity(self, path, directory):
        try:
            stat = directory.stat()
        except OSError:
            self.identities.pop(path, None)
            raise
        pair = (stat.st_dev, stat.st_ino)
        old = self.identities.get(path)
        if old is None or old[:2] != pair:
            old = (*pair, self.next_generation)
            self.next_generation += 1
        self.identities[path] = old
        self.identities.move_to_end(path)
        while len(self.identities) > 256:
            self.identities.popitem(last=False)
        return CgroupIdentity(self.scope_id, path, *old)

    def _process(self, pid):
        stat = read_text(self.proc / str(pid) / 'stat', missing='disappeared')
        group = read_text(self.proc / str(pid) / 'cgroup', missing='disappeared')
        if stat.reason or group.reason:
            return None, stat.reason or group.reason
        try:
            fields = stat.text[stat.text.rindex(')') + 2:].split()
            start = int(fields[19])
        except (ValueError, IndexError):
            return None, 'parse_error'
        path = membership(group.text)
        return ((start, path), None) if path else (None, 'not_exposed')

    def resolve(self, request):
        """Process: (kind, pid, start, membership). Group: (kind, path, inode, device[, 'visible'])."""
        result = dict(request=list(request), ancestors=[], limitations=[])
        def failed(reason, message):
            # Never leave old/partially verified ancestry attached to a failed identity.
            return Observation(dict(request=list(request), ancestors=[], status='unavailable',
                                    reason=reason, message=message), 'unavailable', reason)
        try:
            stat = self.root.stat()
            root_identity = (stat.st_dev, stat.st_ino)
            if self.root_identity is not None and root_identity != self.root_identity:
                return failed('identity_changed', 'The cgroup mount changed; restart collection to establish a new scope.')
            self.root_identity = root_identity
            mount = self.mount_override
            if mount is None:
                info = read_text(self.proc / 'self/mountinfo', limit=1048576)
                mount = mount_context(info.text or '', self.root)
                if mount is None:
                    return failed(info.reason or 'not_exposed', 'Cgroup mount mapping is unavailable; no path mapping was guessed.')
            result['mount'] = mount
            if request[0] == 'process':
                before, reason = self._process(request[1])
                if before is None:
                    return failed(reason, 'Process identity or cgroup membership is unreadable.')
                if before[0] != request[2]:
                    return failed('identity_changed', 'The selected PID was reused.')
                if before[1] != request[3]:
                    return failed('membership_changed', 'The process changed groups; wait for a fresh selection sample.')
                path = before[1]
                result['target'] = TargetRef('process', ProcessIdentity(self.scope_id, request[1], request[2])).to_dict()
                result['limitations'].append('Group counters include other members and descendants; per-thread placement is not measured.')
            elif request[0] == 'cgroup':
                path = request[1]
                before = None
            else:
                return failed('parse_error', 'Unsupported target kind.')
            base = PurePosixPath(mount['root'])
            # Reject traversal instead of allowing PurePosixPath to normalize it.
            if not path.startswith('/') or any(p in ('.', '..') for p in path.split('/')):
                return failed('parse_error', 'Invalid cgroup membership path.')
            if request[0] == 'cgroup' and len(request) > 4 and request[4] == 'visible':
                path = str(base / path.lstrip('/'))
            try:
                relative = PurePosixPath(path).relative_to(base)
            except ValueError:
                return failed('not_exposed', 'The target lies outside the visible cgroup mount.')
            directory = self.root.joinpath(*relative.parts)
            if not directory.resolve().is_relative_to(self.root):
                return failed('not_exposed', 'The target resolves outside the visible cgroup mount.')
            selected = self._identity(path, directory)
            if request[0] == 'cgroup':
                if selected.inode != request[2] or (request[3] is not None and selected.device != request[3]):
                    return failed('identity_changed', 'The selected cgroup was replaced.')
                result['target'] = TargetRef('cgroup', selected).to_dict()
            result.update(exact_cgroup=path, service=attribution(path), selected_group=TargetRef('cgroup', selected).to_dict())
            started = time.monotonic()
            used_bytes = 0
            truncated = False
            observed = []
            while True:
                if len(observed) >= self.max_depth or used_bytes >= self.max_bytes or time.monotonic() - started >= self.budget:
                    truncated = True
                    break
                current_path = str(base / directory.relative_to(self.root).as_posix())
                identity = self._identity(current_path, directory)
                values, limits = {}, {}
                for filename in LIMIT_FILES + COUNTER_FILES:
                    if used_bytes >= self.max_bytes or time.monotonic() - started >= self.budget:
                        read = ReadResult(None, 'budget_exceeded')
                        truncated = True
                    else:
                        read = read_text(directory / filename, limit=min(32768, self.max_bytes - used_bytes))
                        used_bytes += read.bytes_read
                        truncated |= read.reason == 'budget_exceeded'
                    values[filename] = dict(value=read.text, status=read.status, reason=read.reason)
                    if filename in LIMIT_FILES:
                        limits[filename] = parse_limit(filename, read)
                parsed = {}
                for filename in ('memory.current','memory.swap.current','memory.peak'):
                    row=values[filename]
                    parsed[filename]=int(row['value']) if not row['reason'] and isinstance(row['value'],str) and row['value'].isdigit() else None
                    if parsed[filename] is None and row['reason'] is None:row.update(status='unavailable',reason='parse_error')
                for filename in ('cpu.stat', 'memory.events', 'memory.events.local', 'memory.stat'):
                    row = values[filename]
                    parsed[filename] = counters(ReadResult(row['value'], row['reason']))
                    if parsed[filename] is None and row['reason'] is None:
                        row.update(status='unavailable', reason='parse_error')
                for filename in ('cpu.pressure', 'memory.pressure', 'io.pressure'):
                    row = values[filename]
                    parsed[filename] = pressure(ReadResult(row['value'], row['reason']))
                    if parsed[filename] is None and row['reason'] is None:
                        row.update(status='unavailable', reason='parse_error')
                stamp = self.clock()
                old = self.previous.get(identity)
                deltas = {}
                if old and stamp > old['stamp']:
                    for filename in ('cpu.stat', 'memory.events', 'memory.events.local'):
                        current, previous = parsed.get(filename), old['counters'].get(filename)
                        if current is not None and previous is not None:
                            deltas[filename] = {key: value - previous[key] if key in previous and value >= previous[key] else None
                                                for key, value in current.items()}
                observed.append(dict(path=current_path, identity=TargetRef('cgroup', identity).to_dict(),
                                     files=values, limits=limits, counters=parsed, deltas=deltas,
                                     observed_at_monotonic=stamp, observed_at_wall=time.time(),
                                     interval_s=stamp - old['stamp'] if old and stamp > old['stamp'] else None,
                                     memory_events_scope=mount['memory_events_scope'],
                                     memory_events_local_scope='local'))
                # Publish counter baselines only after all identities have been rechecked.
                observed[-1]['_baseline'] = dict(stamp=stamp, counters=parsed)
                if directory == self.root:
                    break
                directory = directory.parent
            if request[0] == 'process':
                after, reason = self._process(request[1])
                if after != before:
                    why = reason or ('identity_changed' if after[0] != before[0] else 'membership_changed')
                    return failed(why, 'Process identity or membership changed during inspection; evidence was discarded.')
            for node in observed:
                identity = TargetRef.from_dict(node['identity']).identity
                relative = PurePosixPath(node['path']).relative_to(base)
                try:
                    final = self.root.joinpath(*relative.parts).stat()
                except OSError:
                    self.identities.pop(node['path'], None)
                    raise
                if (final.st_dev, final.st_ino) != (identity.device, identity.inode):
                    self.identities.pop(node['path'], None)
                    return failed('identity_changed', 'A cgroup changed during inspection; evidence was discarded.')
            final_root = self.root.stat()
            if (final_root.st_dev, final_root.st_ino) != root_identity:
                return failed('identity_changed', 'The cgroup mount changed during inspection; evidence was discarded.')
            for node in observed:
                identity = TargetRef.from_dict(node['identity']).identity
                self.previous[identity] = node.pop('_baseline')
                self.previous.move_to_end(identity)
            while len(self.previous) > 256:
                self.previous.popitem(last=False)
            visibility = mount['ancestry_visibility']
            summary = {}
            incomplete = truncated
            for filename, name, field in (('cpu.max','quota','quota_pct'),('memory.max','memory','memory_max'),
                                          ('memory.high','memory_high','memory_high'),('memory.swap.max','swap','swap_max')):
                entries = [(node, node['limits'][filename]) for node in observed
                           if not (node['path'] == mount['root'] and visibility == 'to_system_root'
                                   and node['limits'][filename]['reason'] == 'not_exposed')]
                known = [(value['value'], node['path']) for node, value in entries if value['state'] == 'known']
                ceiling, origin = min(known, default=(None, None))
                complete = bool(entries) and not truncated and all(value['state'] in ('known', 'unlimited') for _, value in entries)
                incomplete |= not complete
                summary[field] = ceiling
                summary[name + '_source'] = origin
                summary[name + '_complete'] = complete and visibility == 'to_system_root'
                summary[name + '_state'] = 'known' if known else 'unlimited' if complete and visibility == 'to_system_root' else 'unknown'
            if incomplete:
                visibility = 'partial'
            result.update(ancestors=observed, limits=summary, ancestry_visibility=visibility,
                          observed_at_wall=time.time(), observed_at_monotonic=self.clock(),
                          bytes_read=used_bytes, truncated=truncated)
            if any('threaded' in (node['files']['cgroup.type']['value'] or '') for node in observed):
                result['limitations'].append('Threaded hierarchy: leader membership does not establish every thread\'s placement.')
            failures = [row['reason'] for node in observed for row in node['files'].values() if row['reason']]
            status = 'partial' if incomplete or failures or visibility != 'to_system_root' else 'ok'
            reason = 'budget_exceeded' if truncated else failures[0] if failures else 'unknown' if status == 'partial' else None
            result.update(status=status, reason=reason)
            return Observation(result, status, reason, Coverage(examined=len(observed),
                omitted=None if truncated else 0, truncation_reason='depth/time/byte budget' if truncated else None))
        except OSError as exc:
            return failed(error_reason(exc, 'disappeared'), 'The target or an ancestor is no longer readable.')
        except (ValueError, KeyError, IndexError, TypeError, OverflowError):
            return failed('parse_error', 'Target context could not be decoded safely.')


def context_lines(snapshot, request):
    """Render recorded data only; never resolve a live target from a view."""
    try:
        return _context_lines(snapshot, request)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return ['Recorded target context is incomplete or invalid.']


def _context_lines(snapshot, request):
    context = snapshot.get('target_context')
    if not context or context.get('request') != list(request):
        return ['Detailed constraints are not available for this target in this sample.']
    if not context.get('ancestors'):
        return [context.get('message', 'Target detail unavailable.'), 'Reason: ' + str(context.get('reason', 'unknown'))]
    limits = context['limits']
    quota = limits['quota_pct']
    memory = limits['memory_max']
    age = max(0, snapshot['timestamp'] - context['observed_at_wall'])
    lines = ['-- Exact group context --', 'Membership: ' + context['exact_cgroup'],
             'Service association: ' + (context['service'].get('unit') or 'none observed'),
             'Ancestry: ' + context['ancestry_visibility'].replace('_', ' '),
             f'Context age at sample: {age:.1f}s',
             'Tightest observed CPU quota: ' + (f'{quota:g}% (100% = one CPU)' if quota is not None else limits['quota_state']),
             'CPU quota source: ' + (limits['quota_source'] or 'not established'),
             'Tightest observed memory max: ' + (str(memory) + ' bytes' if memory is not None else limits['memory_state']),
             'Tightest observed memory.high: '+(str(limits['memory_high'])+' bytes' if limits.get('memory_high') is not None else limits.get('memory_high_state','not_recorded')),
             'MemoryHigh source: '+(limits.get('memory_high_source') or 'not established'),
             'Tightest observed swap max: '+(str(limits['swap_max'])+' bytes' if limits.get('swap_max') is not None else limits.get('swap_state','not_recorded')),
             'Ancestor ceilings are shared with other descendants; they are not reserved capacity.']
    for node in context['ancestors']:
        lines.append(node['path'])
        for name in ('cpu.max', 'memory.high', 'memory.max', 'io.max'):
            row = node['files'][name]
            displayed = row['value'] if row['value'] is not None else row['reason'].replace('_', ' ')
            lines.append('  ' + name + ': ' + (displayed or 'unlimited (explicit)'))
    return lines + context.get('limitations', [])


def selection_request(view):
    """Snapshot-only selection adapter; safe on the UI thread and in replay."""
    if view.replay or getattr(view,'sessions_view',None) or view.paused or not view.snapshot or view.page != 7:
        return None
    return inspection_request(view)


def inspection_request(view):
    """Selected recorded identity, including while frozen/replaying; no live reads."""
    if not view.snapshot or view.page != 7:
        return None
    if view.browser:
        inspector = getattr(view.browser, 'inspector', None)
        if inspector is not None:
            return inspector.request
    sample = view.snapshot
    process = view.browser.process if view.browser else view.application_process if view.application_mode == 'processes' else None
    if process:
        row = next((p for p in sample.get('processes', []) if (p['pid'], p.get('starttime')) == process), None)
        if row and row.get('cgroup') is not None:
            return ('process', row['pid'], row['starttime'], row['cgroup'])
        return None
    if view.browser:
        row = view.browser.node(sample)
        return ('cgroup', row['path'], row['inode'], row.get('device'), 'visible') if row else None
    if view.application_mode == 'cgroups':
        row = next((g for g in sample.get('workloads', []) if g['id'] == view.workload_id), None)
        if row and row.get('inode') is not None:
            return ('cgroup', row['path'], row['inode'], row.get('device'))
    return None
