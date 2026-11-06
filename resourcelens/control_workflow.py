"""Connect existing guarded control sessions to inspection, history and observation."""
import curses
import time
import uuid

from .diagnostics import context_for, request_current
from .model import TargetRef
from .readability import wrap_lines


KINDS = {'apply': ('cpu', 'apply'), 'undo': ('cpu', 'restore'),
         'hardware_apply': ('hardware', 'apply'), 'hardware_undo': ('hardware', 'restore'),
         'placement_apply': ('placement', 'apply'), 'placement_undo': ('placement', 'restore'),
         'utility_disk_apply': ('disk', 'apply'), 'utility_disk_undo': ('disk', 'restore'),
         'memory_apply':('memory','apply'),'memory_undo':('memory','restore')}
PREPARES = {'prepare': 'cpu', 'placement_open': 'placement', 'placement_prepare': 'placement',
            'hardware_prepare': 'hardware', 'utility_disk_prepare': 'disk',
            'memory_prepare':'memory','memory_restore_prepare':'memory'}


def service_target(sample, request):
    """A service association alone is insufficient: match its recorded lifetime."""
    if not request_current(sample, request):
        return None, 'The selected identity or membership changed; select it again.'
    context = context_for(sample, request) or {}
    service = context.get('service') or {}
    path = service.get('path')
    node = next((n for n in context.get('ancestors', []) if n.get('path') == path), None)
    try:
        identity = TargetRef.from_dict(node['identity']).identity
    except (KeyError, TypeError, ValueError, AttributeError):
        return None, 'No associated service with an established cgroup identity is available.'
    group = next((g for g in sample.get('workloads', []) if g.get('path', g.get('id')) == path
                  and g.get('inode') == identity.inode and g.get('unit') == service.get('unit')
                  and g.get('device', identity.device) == identity.device), None)
    if not group or not group.get('controllable') or group.get('manager') != service.get('manager'):
        return None, 'The associated service is not controllable through an accessible manager. H: measurement health.'
    return dict(group), None


def preview_note(domain):
    return 'Verification: ' + ('kernel read-back' if domain == 'hardware' else 'systemd + kernel read-back') + '; observe workload effects separately.'


def setting_value(domain, setting, value):
    if domain=='memory' and setting=='memory_high':
        from .memory_ui import byte_setting
        return byte_setting(value)
    if domain == 'cpu' and setting in ('quota','weight'):
        if value is None:
            return 'unlimited / runtime reset' if setting == 'quota' else 'manager default / runtime reset'
        return str(value)+('% (100% = one CPU)' if setting == 'quota' else ' relative weight')
    if domain == 'placement':
        from .placement import format_cpus
        return format_cpus(value) if value else 'inherit parent'
    if domain == 'disk' and not isinstance(value,dict):
        return 'unlimited / runtime reset' if value is None else str(value)+' bytes/sec'
    return str(value)


def workflow_buttons(line):
    for label, key in (('h MemoryHigh','h'),('w Weight','w'), ('l Quota','l'), ('p Placement','p'), ('b Disk bandwidth','b'),
                       ('g Hardware','g'), ('J Changes','J'), ('H Health','H'), ('Esc back','\x1b'),
                       ('C Controls','C'),
                       ('u Restore','u'), ('n/p Entries','n'), ('Enter: restore and verify','\n')):
        start = line.find(label)
        if start >= 0:
            yield start, start+len(label), ord(key)


class ControlWorkflow:
    def workflow_init(self):
        self.change_links = {}

    def control_sessions(self):
        return dict(cpu=self.session, disk=self.disk_session, placement=self.placement_session, hardware=self.hardware,memory=self.memory_session)

    def control_job_context(self, kind, args):
        if kind not in KINDS and kind not in PREPARES:
            return None
        domain, action = KINDS.get(kind, (PREPARES.get(kind), 'review'))
        session = self.control_sessions()[domain]
        latest = next((e for e in reversed(session.events) if e['status'] != 'undone'), None)
        plan = args[0] if args and isinstance(args[0], dict) else {}
        if kind=='memory_restore_prepare' and latest:plan=dict(latest['plan'],value=latest['before']['memory_high'])
        if kind=='memory_prepare':plan=dict(target=plan,property='memory_high',value=args[1] if len(args)>1 else '')
        if action == 'restore' and latest:
            plan = latest.get('plan', latest)
        if action == 'review' and 'target' not in plan and domain != 'hardware':
            plan = dict(target=plan)
        return dict(domain=domain, action=action, plan=plan, started_at=time.time(),
                    scope_id=(self.snapshot or {}).get('collection_scope', {}).get('id'),
                    last_requested=(latest.get('requested', latest.get('plan',{}).get('value')) if latest else None),
                    previous_count=len(session.events), backend_event=latest if action == 'restore' else None)

    def control_finished(self, kind, result, error, context, completed_at):
        if context is None or (context['action'] == 'review' and not error):
            return None
        domain, action = context['domain'], context['action']
        session = self.control_sessions()[domain]
        backend = context['backend_event']
        if action == 'apply' and len(session.events) > context['previous_count']:
            backend = session.events[-1]
        detail = result or backend or context['plan']
        plan = detail.get('plan', detail)
        target = plan.get('target') or detail.get('target') or context['plan'].get('target')
        identity = ({'id': target['path'], 'inode': target.get('inode'),
                     **{k: target[k] for k in ('device', 'generation') if k in target}}
                    if target and target.get('path') else None)
        # Local sessions append a receipt before attempting a write. A failed
        # restore is conservatively uncertain: some hardware batches are partial.
        status = ('unconfirmed' if backend is not None else 'blocked') if error else 'verified'
        label = f"{domain} {action}: " + ((target or {}).get('unit') or str(plan.get('scope', 'hardware')))
        setting = plan.get('property', plan.get('setting', plan.get('direction', 'AllowedCPUs' if domain == 'placement' else 'setting')))
        before = detail.get('before', plan.get('before', {}))
        previous_value = ({path:old for path,old,_ in plan.get('changes', [])} if domain == 'hardware'
                          else before.get(setting, before.get('allowed')))
        if domain == 'disk' and isinstance(previous_value,dict):
            previous_value = previous_value.get(plan.get('device',{}).get('path'))
        requested = plan.get('value', detail.get('requested'))
        if action == 'restore':
            requested = previous_value
            previous_value = context['last_requested']
        event = self.investigation.mark(label, context['started_at'], identity, status)
        event.update(id=uuid.uuid4().hex, domain=domain, action=action, setting=setting,
                     started_at=context['started_at'], completed_at=completed_at,
                     verified_at=completed_at if not error and action != 'review' else None,
                     scope_id=context['scope_id'], before=before, requested=requested,
                     previous_value=previous_value,
                     device=plan.get('device'),
                     after=detail.get('restored', detail.get('after')), error=error,
                     verification='kernel read-back' if domain == 'hardware' else 'systemd + kernel read-back')
        if action == 'restore' and backend is not None:
            event['restores_id'] = next((key for key,link in self.change_links.items()
                                        if link[1] is backend and any(e.get('id') == key and e.get('action') == 'apply'
                                                                    for e in self.investigation.events)), None)
        if backend is not None:
            self.change_links[event['id']] = (domain, backend)
        retained = {e.get('id') for e in self.investigation.events}
        self.change_links = {key: value for key, value in self.change_links.items() if key in retained}
        if self.recorder:
            self.recorder.write('event', event)
        self.modal = dict(stage='workflow_result', event=event, scroll=0)
        return event

    def open_control_review(self, request):
        group, reason = service_target(self.snapshot or {}, request)
        if reason or self.replay:
            self.modal = dict(stage='result', message=reason or 'Replay is read-only; controls require a live target.')
            return
        inspector = self.active_inspector()
        if inspector and inspector.invalid_reason(self.snapshot):
            self.modal = dict(stage='result', message=inspector.invalid_reason(self.snapshot))
            return
        context = context_for(self.snapshot, request)
        service_node = next(n for n in context['ancestors'] if n['path'] == group['path'])
        self.modal = dict(stage='workflow_review', request=tuple(request), target=group, scroll=0,
                          scope_id=self.snapshot.get('collection_scope', {}).get('id'),
                          service_ref=TargetRef.from_dict(service_node['identity']).to_dict(),
                          group_ref=TargetRef.from_dict(context['selected_group']).to_dict())

    def open_changes(self):
        self.modal = dict(stage='workflow_changes', selected=len(self.investigation.events)-1, scroll=0)

    def workflow_event(self):
        if self.modal['stage'] in ('workflow_result', 'workflow_restore'):
            return self.modal['event']
        events = self.investigation.events
        if not events:
            return None
        self.modal['selected'] = max(0, min(len(events)-1, self.modal.get('selected', 0)))
        return events[self.modal['selected']]

    def restore_reason(self, event):
        if self.replay:
            return 'Recorded events are read-only; their Undo receipts cannot be used live.'
        link = self.change_links.get(event.get('id'))
        if not link or event.get('action') != 'apply':
            return 'This entry has no restorable apply receipt from this UI session.'
        domain, backend = link
        latest = next((e for e in reversed(self.control_sessions()[domain].events) if e['status'] != 'undone'), None)
        if backend is not latest:
            return 'Only the latest active change in this control domain can be restored. Newer changes must be restored first.'
        if event.get('scope_id') != (self.snapshot or {}).get('collection_scope', {}).get('id'):
            return 'Collection scope changed; restoration is disabled.'
        return None

    def workflow_content(self):
        modal = self.modal
        if modal['stage'] == 'workflow_review':
            group = modal['target']
            context = context_for(self.snapshot, modal['request']) or {}
            request = modal['request']
            selected = ('PID ' if request[0] == 'process' else 'Group ') + str(request[1])
            quota = context.get('limits', {}).get('quota_pct')
            return ['REVIEW SERVICE CONTROLS', 'Selected: ' + selected,
                    'Selected group: ' + context.get('exact_cgroup', 'unknown'),
                    'Control target: '+group['unit'], 'Manager: '+group['manager'], 'Cgroup: '+group['path'],
                    'Applies to this entire service and child groups, including other processes.',
                    'Weight changes relative sibling priority; quota changes CPU time capacity.',
                    'Observed CPU quota: '+(f'{quota:g}% (100% = one CPU)' if quota is not None else 'unknown'),
                    'Quota source: '+str(context.get('limits', {}).get('quota_source', 'unknown')),
                    'Tighter ancestors and child settings can still restrict the selected process.',
                    'Existing permissions apply; each preview reads fresh settings.',
                    'Runtime settings remain after exit; restoration requires this session and unchanged identity/settings.',
                    'w Weight | l Quota | p Placement | b Disk bandwidth',
                    'h MemoryHigh: review the memory reclaim threshold.',
                    'g Hardware: machine-wide CPU settings.',
                    'Choose a setting to edit and preview. Nothing changes here.',
                    'C Controls | J Changes | H Health | Esc back']
        event = self.workflow_event()
        if event is None:
            return ['CHANGES', 'No changes or markers in this session.', 'H Health | Esc back']
        if modal['stage'] == 'workflow_restore':
            return ['REVIEW RESTORATION', event['label'],
                    'Restore the previous configuration from this session receipt.',
                    'Target: '+str(event.get('target') or 'shared hardware'),
                    'Setting: '+str(event.get('setting')),
                    'Previous: '+setting_value(event['domain'],event['setting'],event.get('previous_value')),
                    'Current requested value: '+setting_value(event['domain'],event['setting'],event.get('requested')),
                    'Checks that the target and its settings still match this session.',
                    'Restoration can be refused or remain unconfirmed. It does not restore workload performance.',
                    'Enter: restore and verify | Esc: cancel']
        lines = ['CHANGES' if modal['stage'] == 'workflow_changes' else 'CONTROL RESULT',
                 event['label']+' ['+event['status']+']']
        if modal['stage'] == 'workflow_changes':
            index = modal['selected']
            lines += [f'Entry {index+1}/{len(self.investigation.events)} - n/p selects an entry']
            for i in range(max(0,index-2),min(len(self.investigation.events),index+3)):
                item = self.investigation.events[i]
                lines.append(('> ' if i == index else '  ')+f"[{item['status']}] {item['label']}")
        if event.get('domain'):
            link = self.change_links.get(event.get('id'))
            restored = any(e.get('restores_id') == event.get('id') and e.get('status') == 'verified'
                           for e in self.investigation.events)
            receipt_status = 'restored' if restored else link[1]['status'] if link else 'not available in this session'
            readback = event.get('after')
            readback_value = ((readback or {}).get(event.get('setting'), (readback or {}).get('allowed'))
                              if isinstance(readback,dict) else event.get('requested') if event.get('verified_at') else 'not confirmed')
            display = lambda value: setting_value(event['domain'],event['setting'],value)
            lines += ['Target: '+str(event.get('target') or 'shared hardware'),
                      'Apply/review start: '+time.strftime('%H:%M:%S',time.localtime(event['started_at'])),
                      'Verified completion: '+(time.strftime('%H:%M:%S',time.localtime(event['verified_at'])) if event.get('verified_at') else 'not confirmed'),
                      'Setting: '+str(event.get('setting')), 'Requested: '+display(event.get('requested')),
                      'Previous setting: '+('not read' if event.get('action')=='review' and not event.get('before') else display(event.get('previous_value'))),
                      'Device: '+str(event.get('device') or 'not device-specific'),
                      'Read-back setting: '+(display(readback_value) if event.get('verified_at') else 'not confirmed'),
                      'Verification: '+event.get('verification', 'unknown') + (' complete.' if event.get('verified_at') else ' incomplete.'),
                      'Restoration receipt: '+receipt_status,
                      'Ancestor and child restrictions still apply; read-back alone does not establish enforcement.',
                      'Result: '+str(event.get('error') or 'Requested configuration was read back; workload outcome remains to be observed.')]
        if event.get('findings'):
            lines+=['','-- Captured findings (original rules) --']
            for captured in event['findings']:
                lines += [captured['title'],f"Rule: {captured['rule_id']} / version {captured['rule_version']}",
                          captured.get('explanation',''),*captured.get('limitations',[])]
        lines += ['', *self.investigation.observation_lines(event), '',
                  'n/p: next/previous entry | arrows/PgDn: scroll', 'u: review restoration | H: health | Esc: back']
        return lines

    def workflow_lines(self):
        lines = wrap_lines(self.workflow_content(), max(20, min(76, self.screen_size[1]-8)))
        count = max(1, self.screen_size[0]-8)
        self.modal['scroll'] = min(self.modal.get('scroll', 0), max(0, len(lines)-count))
        offset = self.modal['scroll']
        footer = 'H Health | Esc back' if self.modal['stage'] in ('workflow_review','workflow_restore') else 'n/p Entries | u Restore | H Health | Esc back'
        return lines[offset:offset+count] + [f'[{offset+1}-{min(offset+count,len(lines))}/{len(lines)}] Arrows: scroll', footer]

    def workflow_key(self, key):
        modal = self.modal
        if key == 27:
            self.modal = None
        elif key == ord('H'):
            self.open_insights('health')
        elif key == ord('J'):
            self.open_changes()
        elif key in (ord('['),ord(']')) and modal['stage'] in ('workflow_changes','workflow_result'):
            durations=(5,15,30,60);index=durations.index(self.investigation.window_seconds)
            self.investigation.window_seconds=durations[(index+(1 if key==ord(']') else -1))%len(durations)]
            modal['seconds']=self.investigation.window_seconds
        elif key == ord('C') and modal['stage'] == 'workflow_review':
            self.open_control_mode()
            if self.modal['stage'] == 'control_mode':
                self.modal['return_to'] = modal
        elif key in (curses.KEY_DOWN, curses.KEY_UP, curses.KEY_NPAGE, curses.KEY_PPAGE):
            step = max(1, self.screen_size[0]-8) if key in (curses.KEY_NPAGE,curses.KEY_PPAGE) else 1
            modal['scroll'] = max(0, modal.get('scroll',0)+(-step if key in (curses.KEY_UP,curses.KEY_PPAGE) else step))
        elif modal['stage'] == 'workflow_review' and key in map(ord, 'wlpbgh'):
            group, reason = service_target(self.snapshot, modal['request'])
            context = context_for(self.snapshot, modal['request']) or {}
            service_ref = next((n.get('identity') for n in context.get('ancestors',[]) if n['path'] == modal['target']['path']), None)
            same_target = group and all(group.get(k) == modal['target'].get(k)
                                        for k in ('path','inode','device','unit','manager','controllable'))
            if reason or self.snapshot.get('collection_scope',{}).get('id') != modal['scope_id'] or context.get('selected_group') != modal['group_ref'] or service_ref != modal['service_ref'] or not same_target:
                self.modal = dict(stage='result', message=reason or 'Target or scope changed since review; open a fresh review.')
            elif not self.control or self.replay:
                self.open_control_mode()
                if self.modal['stage'] == 'control_mode':
                    self.modal['return_to'] = modal
            elif key in (ord('w'), ord('l')):
                self.modal = dict(stage='input', target=group, property='weight' if key == ord('w') else 'quota', value='')
            elif key == ord('b'):
                self.modal = dict(stage='utility_disk', target=group, value='')
            elif key==ord('h'):
                self.open_memory_control(group)
            elif key == ord('p'):
                def inspect():
                    return dict(target=group, info=self.placement_session.backend.snapshot(group))
                self.job('placement_open', inspect)
            else:
                self.hardware_menu()
        elif modal['stage'] in ('workflow_changes','workflow_result') and key in (ord('n'),ord('p')):
            current = self.workflow_event()
            index = next((i for i,e in enumerate(self.investigation.events) if e is current), 0)
            self.modal = dict(stage='workflow_changes', selected=(index+(1 if key == ord('n') else -1)) % max(1,len(self.investigation.events)), scroll=0)
        elif modal['stage'] in ('workflow_changes','workflow_result') and key == ord('u'):
            event = self.workflow_event()
            reason = self.restore_reason(event) if event else 'No change selected.'
            if not reason and event.get('domain')=='memory':self.review_memory_restore(event)
            else:self.modal = dict(stage='result', message=reason) if reason else dict(stage='workflow_restore', event=event, scroll=0)
        elif modal['stage'] == 'workflow_restore' and key in (10,13):
            event = modal['event']
            reason = self.restore_reason(event)
            lines = wrap_lines(self.workflow_content(), max(20,min(76,self.screen_size[1]-8)))
            if modal.get('scroll',0)+max(1,self.screen_size[0]-8) < len(lines):
                return True
            if reason:
                self.modal = dict(stage='result', message=reason)
            elif not self.control:
                self.open_control_mode()
            else:
                domain = event['domain']
                kind = dict(cpu='undo', hardware='hardware_undo', placement='placement_undo', disk='utility_disk_undo')[domain]
                self.job(kind, self.control_sessions()[domain].undo)
        return True
