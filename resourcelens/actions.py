"""Named inspector actions shared by buttons, help and search."""

from dataclasses import dataclass

from .diagnostics import context_for, request_current
from .model import TargetRef
from .targets import inspection_request


@dataclass(frozen=True)
class ActionSpec:
    id: str
    label: str
    key: str
    help: str
    kinds: tuple = ('process', 'cgroup')
    live: bool = False
    mutation: bool = False


ACTIONS = (
    ActionSpec('inspect', 'Inspect selection', 'I', 'Open the selected target inspector.'),
    ActionSpec('profile', 'Investigate CPU / capture', 'P', 'Choose counters, stacks, waits or hardware energy and a duration.', live=True),
    ActionSpec('waits', 'Capture CPU + waits', 'W', 'Start the existing ten-second CPU + waits capture.', live=True),
    ActionSpec('memory', 'Inspect memory', 'V', 'Inspect process allocation, threads and shared mappings.', ('process',), live=True),
    ActionSpec('open_group', 'Open exact group', 'g', 'Inspect the exact group containing this process.', ('process',)),
    ActionSpec('open_service', 'Open associated service', 'o', 'Inspect the readable service ancestor; keep its scope distinct.'),
    ActionSpec('open_ancestor', 'Open limiting ancestor', 'a', 'Inspect the ancestor identified by the selected evidence or quota.'),
    ActionSpec('show_constraints', 'Show constraints', 'c', 'Read local and inherited settings and coverage.'),
    ActionSpec('show_evidence', 'Show evidence', 'e', 'Read finding sources, counter intervals and limitations.'),
    ActionSpec('group_memory','Explain group memory','m','Read cgroup memory usage, high/max/swap boundaries, categories and scoped events.'),
    ActionSpec('review_controls', 'Review service controls', 'r', 'Review the service scope before opening CPU, placement or disk editors.', live=True),
    ActionSpec('show_changes', 'Changes and observation', 'J', 'Inspect control results, coverage and session restoration.'),
    ActionSpec('sessions', 'Saved investigations', 'L', 'Reopen recordings, profiles and compatible run comparisons.'),
    ActionSpec('health', 'Measurement health', 'H', 'Inspect unavailable measurements and next steps.'),
)
BY_ID = {action.id: action for action in ACTIONS}


def related_request(sample, request, action, path_hint=None):
    context = context_for(sample, request)
    if not context or not context.get('ancestors'):
        return None
    try:
        nodes = context['ancestors']
        if action == 'open_group':
            node = nodes[0]
        elif action == 'open_service':
            path = context.get('service', {}).get('path')
            if not context.get('service', {}).get('unit') or path == nodes[0]['path']:
                return None
            node = next((n for n in nodes if n['path'] == path), None)
        else:
            candidates = [n for n in nodes[1:] if n.get('limits', {}).get('cpu.max', {}).get('state') == 'known']
            node = next((n for n in candidates if n['path'] == path_hint), None)
            node = node or min(candidates, key=lambda n: n['limits']['cpu.max']['value'], default=None)
        if node is None:
            return None
        identity = TargetRef.from_dict(node['identity']).identity
        return ('cgroup', identity.path, identity.inode, identity.device)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def availability(view, action, request=None):
    request = request if request is not None else inspection_request(view)
    if action.live and view.replay:
        return 'This action requires a live target and is disabled during replay.'
    if not request or request[0] not in action.kinds:
        return 'Select a process.' if action.kinds == ('process',) else 'Select a process or group in Applications.'
    if not request_current(view.snapshot or {}, request):
        return 'The selected identity or membership changed; return and select it again.'
    inspector = view.active_inspector() if hasattr(view, 'active_inspector') else None
    if inspector and inspector.request == tuple(request) and inspector.invalid_reason(view.snapshot):
        return inspector.invalid_reason(view.snapshot)
    if action.id in ('open_group', 'open_service', 'open_ancestor'):
        if related_request(view.snapshot, request, action.id) is None:
            return 'That group relationship is not established in the recorded readable ancestry.'
    if action.id == 'review_controls':
        from .control_workflow import service_target
        _, reason = service_target(view.snapshot or {}, request)
        return reason
    return None


def dispatch(view, action_id, request=None, scope_id=None):
    action = BY_ID.get(action_id)
    if action is None:
        return False
    bound = tuple(request) if request is not None else inspection_request(view)
    reason = availability(view, action, bound)
    current_scope = (view.snapshot or {}).get('collection_scope', {}).get('id')
    if scope_id is not None and current_scope != scope_id:
        reason = 'The collection scope changed; select the action again.'
    if reason:
        view.modal = dict(stage='result', message=reason)
        return True
    if action_id == 'inspect':
        view.inspect_request(bound)
    elif action_id == 'review_controls':
        view.open_control_review(bound)
    elif action_id == 'show_changes':
        view.open_changes()
    elif action_id == 'sessions':
        view.open_sessions()
    elif action_id in ('profile', 'waits'):
        view.open_profile(scheduler=action_id == 'waits', request=bound)
    elif action_id == 'memory':
        view.open_memory_sharing(request=bound)
    elif action_id == 'health':
        view.open_insights('health')
    else:
        inspector = view.active_inspector()
        if inspector is None:
            view.inspect_request(bound)
            inspector = view.active_inspector()
        if action_id=='group_memory':
            inspector.select_tab('Usage')
        elif action_id.startswith('show_'):
            inspector.select_tab('Constraints' if action_id == 'show_constraints' else 'Evidence')
        else:
            selected = inspector.selected_finding()
            hint = selected.evidence[0].get('path') if selected and selected.evidence else None
            related = related_request(view.snapshot, bound, action_id, hint)
            if related:
                inspector.navigate(related)
        view.browser.detail_scroll = inspector.scroll
    return True


def search_actions(view):
    request = inspection_request(view)
    if request is None:
        return []
    scope = (view.snapshot or {}).get('collection_scope', {}).get('id')
    return [('action', dict(id=a.id, request=list(request), scope_id=scope),
             'Action: ' + a.label + (' [unavailable]' if availability(view, a, request) else '')) for a in ACTIONS]
