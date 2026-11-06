"""Terminal presentation, separate from Linux telemetry."""

from collections import defaultdict, deque
import curses
import os
import queue
import re
import sys
import textwrap
import threading
import time

from .insights import InsightsView
from .impact import impact_lines, impact_summary
from .readability import draw_text
from .presentation import GLYPHS, card_layout, chart, compact_overview, draw_card, overview_cards, palette
from .controls import ControlSession
from .hardware_ui import HardwareView
from .placement_ui import PlacementView
from .topology import topology_map
from .placement import format_cpus
from .resource_panels import resource_lines, expanded_overview
from .navigation import NavigationView, actions, context_help
from .focus import FocusView
from .investigation import Investigation, indicators
from .control_workflow import ControlWorkflow, preview_note, workflow_buttons
from .memory_ui import MemoryControlsView
from .usability_ui import UsabilityView
from .usability import tree_rows
from .workspaces import Workspaces, related_groups
from .cgroup_browser import CgroupBrowser
from .application_processes import ApplicationProcesses
from .memory_sharing import MemorySharingView
from .profiling import ProfileView


UNSAFE_TEXT = re.compile('[^\x20-\x7e' + re.escape(''.join(GLYPHS)) + ']')


def clean(value):
    # Keep the same allowlist, with the per-character scan in the regex engine.
    return UNSAFE_TEXT.sub('?', str(value))


def size(value):
    if value is None:
        return "N/A"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(value) < 1024 or unit == "PiB":
            return f"{value:.1f}{unit}"
        value /= 1024


def pct(value):
    return "N/A" if value is None else f"{value:.1f}%"


def ratio(used, total):
    return used / total * 100 if used is not None and total else None


def cpu_setting(property, value):
    if value is None:
        return "default (100)" if property == "weight" else "no local quota"
    return f"{value:g}" if property == "weight" else f"{value:g}% of one CPU"


def bar(value, width=22):
    if value is None:
        return "[" + " no data ".center(width, ".") + "] N/A"
    fill = round(max(0, min(100, value)) / 100 * width)
    return "[" + "#" * fill + "." * (width - fill) + "] " + pct(value)


def spark(values, width=40, maximum=100, unicode=False):
    values = list(values)[-width:]
    if not values or all(v is None for v in values):
        return "N/A (no measured data)"[:width]
    levels = "·▁▂▃▄▅▆▇█" if unicode else "._-:=+*#%@"
    return "".join(" " if v is None else levels[min(len(levels) - 1, max(0,
                    int(v / max(1, maximum) * (len(levels) - 1))))] for v in values)


def pressure(snapshot, kind):
    value = snapshot["pressure"].get(kind, {}).get("some", {}).get("avg10")
    return f"{kind.upper()} pressure (10s): {pct(value)}"


class View(MemoryControlsView, ControlWorkflow, InsightsView, HardwareView, PlacementView, NavigationView, FocusView, UsabilityView, Workspaces, ApplicationProcesses):
    names = ("Overview", "CPU", "Memory", "Disks", "GPU", "Processes", "Network", "Applications", "Timeline", "System")

    def __init__(self, unicode=False, control=False, session=None):
        from .diagnostics import DiagnosticHistory
        self.diagnostic_history = DiagnosticHistory()
        self.inventory_revision = 0
        self.memory_sharing = None
        self.profile_view = None
        self.sessions_view = None
        self.sessions_only = False
        self.profile_jobs = []
        self.proc_root = '/proc'
        self.cgroup_root = '/sys/fs/cgroup'
        self.application_process_init()
        self.browser = None
        self.browser_button = None
        self.workspace_init()
        self.usability_init()
        self.hardware_init()
        self.placement_init()
        self.memory_control_init()
        self.overview_expanded = False
        self.overview_links = {}
        self.overview_cards_regions = []
        self.focus = None
        self.overlay = None
        self.investigation = Investigation()
        self.sample_times = deque(maxlen=120)
        self.recorder = None
        self.replay = None
        self.replay_delta = 0
        self.replay_playing = False
        self.pending_action = {}
        self.workflow_init()
        self.page = 0
        self.scroll = 0
        self.sort = "cpu"
        self.filter = ""
        self.workload_filter = ""
        self.filter_context = "processes"
        self.editing_filter = False
        self.paused = False
        self.help = False
        self.help_return_scroll = 0
        self.action_regions = []
        self.history = defaultdict(lambda: deque(maxlen=120))
        self.snapshot = None
        self.unicode = unicode
        self.colors = {key: 0 for key in ("accent", "cpu", "memory", "disk", "gpu", "network", "muted", "warning")}
        self.tab_regions = []
        self.control = control
        self.session = session or ControlSession()
        self.workload_id = None
        self.workload_detail = False
        self.modal = None
        self.results = queue.SimpleQueue()
        self.screen_size = (24, 80)

    def trend(self, values, width=40, maximum=100):
        return spark(values, width, maximum, unicode=self.unicode)

    def update(self, snapshot):
        self.investigation.observe(snapshot)
        if self.paused:
            return
        self.diagnostic_history.observe(snapshot)
        self.snapshot = snapshot
        self.sample_times.append(snapshot['timestamp'])
        active_devices = set()
        for kind, items, fields in [('disk', snapshot['disks'], ('active_pct', 'read_bps', 'write_bps')),
                                    ('gpu', snapshot['gpus'], ('busy_pct', 'memory_used', 'power_w'))]:
            for item in items:
                identity = str(item.get('identity', item.get('id', item.get('name'))))
                for field in fields:
                    key = f'device:{kind}:{identity}:{field}'
                    active_devices.add(key)
                    self.history[key].append(item.get(field))
        for key in list(self.history):
            if isinstance(key, str) and key.startswith('device:') and key not in active_devices:
                del self.history[key]
        aggregate = next((c for c in snapshot["cpu"] if c["id"] == "cpu"), {})
        self.history["cpu"].append(aggregate.get("busy_pct"))
        memory = snapshot["memory"]
        self.history["memory"].append(ratio(memory["used"], memory["total"]))
        self.history["disk"].append(max((d["active_pct"] for d in snapshot["disks"]
                                          if d["active_pct"] is not None), default=None))
        self.history["gpu"].append(max((g["busy_pct"] for g in snapshot["gpus"]
                                         if g["busy_pct"] is not None), default=None))
        identities = {(entry["name"], entry["ifindex"]) for entry in snapshot["network"]}
        for key in list(self.history):
            if isinstance(key, tuple) and key[:2] not in identities:
                del self.history[key]
        for entry in snapshot["network"]:
            for direction in ("rx", "tx"):
                self.history[(entry["name"], entry["ifindex"], direction)].append(entry[f"{direction}_bps"])
        active = {"workload:" + group["id"] for group in snapshot.get("workloads", [])}
        for key in list(self.history):
            if isinstance(key, str) and key.startswith("workload:") and key not in active:
                del self.history[key]
        for group in snapshot.get("workloads", []):
            if self.app_history_identity.get(group['id']) != group.get('inode'):
                self.history['workload:'+group['id']].clear()
            self.history["workload:" + group["id"]].append(group["cpu_pct"])
        self.app_history_identity = {g['id']:g.get('inode') for g in snapshot.get('workloads',[])}

    def device_history(self, kind, item, field):
        identity = str(item.get('identity', item.get('id', item.get('name'))))
        return self.history[f'device:{kind}:{identity}:{field}']

    def marker_line(self, width):
        times = list(self.sample_times)[-width:]
        marks = [' '] * len(times)
        if times:
            for event in self.investigation.events:
                if times[0] <= event['timestamp'] <= times[-1]:
                    index = min(range(len(times)), key=lambda i: abs(times[i] - event['timestamp']))
                    marks[index] = '|'
        return 'Marks ' + ''.join(marks).rjust(width)

    def mark_event(self, label, status='marker', target=None, timestamp=None, **metadata):
        event = self.investigation.mark(label, timestamp, target, status)
        event.update(metadata)
        if self.recorder:
            self.recorder.write('event', event)
        return event

    def active_inspector(self):
        return self.browser.ensure_inspector(self.snapshot) if self.browser and self.snapshot else None

    def open_sessions(self, path=None):
        from .sessions_ui import SessionBrowser
        from .usability import state_dir
        self.sessions_view=SessionBrowser(state_dir(),(path,) if path else ())

    def inspect_request(self, request):
        from .diagnostics import request_current
        from .inspector import Inspector
        if not request or not request_current(self.snapshot or {}, request):
            self.modal = dict(stage='result', message='The target identity changed; select it again.')
            return
        current = self.active_inspector()
        if current:
            if current.request == tuple(request):
                current.select_tab('Summary')
            else:
                current.navigate(request)
            self.browser.detail_scroll = current.scroll
            return
        self.page = 7
        if request[0] == 'process':
            self.browser = CgroupBrowser(self.snapshot, request[3] or '/')
            self.browser.process, self.browser.process_entry = tuple(request[1:3]), True
        else:
            self.browser = CgroupBrowser(self.snapshot, request[1])
            self.browser.inode, self.browser.context_detail = request[2], True
        self.browser.inspector = Inspector(request)

    def browser_action(self):
        if self.browser and self.browser.pending_action:
            from .actions import dispatch
            action, self.browser.pending_action = self.browser.pending_action, None
            dispatch(self, action)

    def open_profile(self, scheduler=False, request=None):
        if self.replay:
            self.modal=dict(stage='result',message='Profiling requires a live target; disabled during replay.')
            return
        target=None
        from .targets import inspection_request
        from .diagnostics import context_for, request_current
        request = tuple(request) if request is not None else inspection_request(self)
        if request is not None:
            inspector = self.active_inspector()
            if not request_current(self.snapshot or {}, request) or inspector and inspector.invalid_reason(self.snapshot):
                self.modal = dict(stage='result', message='The target identity or membership changed; return and reselect.')
                return
            if request[0] == 'process':
                target = dict(pid=request[1], expected_start=request[2])
            else:
                from pathlib import PurePosixPath
                context = context_for(self.snapshot, request)
                if len(request) > 4 and request[4] == 'visible':
                    relative = request[1].lstrip('/')
                elif context:
                    try:
                        relative = str(PurePosixPath(context['exact_cgroup']).relative_to(context['mount']['root']))
                    except (ValueError, KeyError):
                        relative = None
                else:
                    relative = None
                if relative is None:
                    self.modal = dict(stage='result', message='Establish the readable cgroup mount mapping in the inspector before capture.')
                    return
                target = dict(cgroup=self.cgroup_root + '/' + relative, expected_inode=request[2], expected_device=request[3])
        elif self.browser and self.browser.process:
            pid,start=self.browser.process
            target=dict(pid=pid,expected_start=start)
        elif self.browser:
            target=dict(cgroup='/sys/fs/cgroup/'+self.browser.path.lstrip('/'),expected_inode=self.browser.inode)
        elif self.page==7 and self.application_mode=='processes':
            rows=self.application_process_rows()
            selected=self.selected_application_process(rows)
            if selected is not None:
                row=rows[selected]
                target=dict(pid=row['pid'],expected_start=row.get('starttime'))
        elif self.page==7:
            group=self.selected_workload()
            if group and group.get('path'):
                target=dict(cgroup='/sys/fs/cgroup/'+group['path'].lstrip('/'),expected_inode=group.get('inode'))
        if target:
            if target.get('pid') and self.browser is None:
                self.application_process = (target['pid'], target.get('expected_start'))
                self.application_process_manual = True
            if scheduler:target['scheduler']=True
            inspector=self.active_inspector()
            context=None
            if inspector:
                inspector.refresh(self)
                context=dict(schema=1,scope_id=self.snapshot.get('collection_scope',{}).get('id'),
                    observed_at=self.snapshot['timestamp'],request=list(inspector.request),
                    interpretation='Inspector evidence at capture selection; not a continuously sampled capture diagnosis.',
                    findings=[f.to_dict() for f in inspector.findings])
            self.profile_view=ProfileView(target,choose=not scheduler,context=context)
            self.profile_jobs=[job for job in self.profile_jobs if job.result is None and job.error is None and not job.stop.is_set()]
            self.profile_jobs.append(self.profile_view)
        else:
            self.modal=dict(stage='result',message='Select a process or cgroup in Applications, then press P to profile for 10 seconds.')

    def open_memory_sharing(self, request=None):
        if self.replay:
            self.modal = dict(stage='result',message='Memory sharing inspection requires live /proc data; it is disabled during replay.')
            return
        target = None
        from .targets import inspection_request
        from .diagnostics import request_current
        request = tuple(request) if request is not None else inspection_request(self)
        if request is not None:
            inspector = self.active_inspector()
            if request[0] != 'process' or not request_current(self.snapshot or {}, request) or inspector and inspector.invalid_reason(self.snapshot):
                self.modal = dict(stage='result', message='Select the same live process identity and membership before inspecting memory.')
                return
            target = next((p for p in self.snapshot.get('processes', []) if (p['pid'], p.get('starttime')) == request[1:3]), None)
        elif self.browser and self.browser.process:
            target = next((p for p in self.snapshot.get('processes',[]) if (p['pid'],p.get('starttime')) == self.browser.process),None)
        elif self.page == 7 and self.application_mode == 'processes':
            rows = self.application_process_rows()
            selected = self.selected_application_process(rows)
            if selected is not None:
                target = rows[selected]
                self.application_process_manual = True
        if target:
            self.memory_sharing = MemorySharingView(target,self.snapshot.get('processes',[]),self.proc_root)
        else:
            self.modal = dict(stage='result',message='Select a process in Applications, or open a PID inside a cgroup, then press V.')

    def open_cgroup_browser(self, root=False):
        group = self.selected_workload()
        if self.snapshot and (root or group):
            self.browser = CgroupBrowser(self.snapshot, '/' if root else group.get('path') or '/')

    def workload_rows(self):
        groups = related_groups(self.snapshot, self.relation) if self.snapshot else []
        groups = [group for group in groups if self.workload_filter.casefold() in
                  f"{group.get('unit') or ''} {group['id']}".casefold()]
        def order(group):
            if self.workload_sort == 'name':
                return (group.get('unit') or group['id']).casefold()
            if self.workload_sort == 'io':
                return sum((d.get('read_bps') or 0)+(d.get('write_bps') or 0) for d in group.get('io_devices', []))
            value = group.get('memory_current' if self.workload_sort == 'memory' else 'cpu_pct')
            return value if value is not None else -1
        rows = sorted(groups, key=order, reverse=self.workload_sort != 'name')
        if self.workload_id is None:
            self.workload_id = rows[0]['id'] if rows else None
        elif self.workload_id not in {group['id'] for group in rows}:
            self.workload_notice = 'Selection exited or is filtered out; use arrows to select another workload.'
            self.workload_detail = False
        group = next((g for g in rows if g['id'] == self.workload_id), None)
        identity = (group['id'], group.get('inode')) if group else None
        if self.workload_identity and identity and identity[0] == self.workload_identity[0] and identity != self.workload_identity:
            self.workload_notice = 'Selected service restarted/replaced; review its new identity before controlling it.'
            self.workload_detail = False
        if identity is not None:
            self.workload_identity = identity
        return rows

    def selected_workload(self):
        return next((group for group in self.workload_rows() if group["id"] == self.workload_id), None)

    def workload_lines(self, width=100):
        rows = self.workload_rows()
        group = self.selected_workload()
        if not self.workload_detail or group is None:
            name_width = max(12, min(45, width - 32))
            lines = [f"{len(rows)} groups | Filter: {clean(self.workload_filter) or '(none)'}",
                     'Enter: open | I: inspect | /: filter',
                     self.workload_notice or self.relation_label or f"Sort: {self.workload_sort} | c CPU / m RAM / n name / i I/O",
                     f"  {'UNIT / GROUP':<{name_width}} {'CPU':>7} {'MEMORY':>10} {'PIDS':>5}"]
            for row in rows:
                name = clean(row["unit"] or row["id"])
                lines.append(f"{'>' if row['id'] == self.workload_id else ' '} {name[:name_width]:<{name_width}} "
                             f"{pct(row['cpu_pct']):>7} {size(row['memory_current']):>10} {len(row['pids']):>5}")
            if not rows:
                lines.append("No matching groups. Clear search with / and relationship with x; cgroup v2 is required.")
            return lines
        return self.application_lines(width)

    def job(self, kind, function, *args, allow_replay=False):
        if self.replay and not allow_replay:
            self.modal = {'stage': 'result', 'message': 'Replay is read-only; live control operations are disabled.'}
            return
        self.pending_action = {'plan': args[0] if args and isinstance(args[0], dict) else {}, 'timestamp': time.time(), 'target': args[0].get('target') if args and isinstance(args[0], dict) else None}
        context = self.control_job_context(kind, args)
        self.modal = {"stage": "busy", "kind": kind}
        def execute():
            try:
                result, error = function(*args), None
            except Exception as exc:
                result, error = None, str(exc)
            self.results.put((kind, result, error, context, time.time()))
        threading.Thread(target=execute, daemon=True).start()

    def poll_actions(self):
        if self.sessions_view:
            self.sessions_view.poll()
            if self.sessions_view.record_view:self.sessions_view.record_view.colors=self.colors
        while not self.results.empty():
            entry = self.results.get()
            kind, result, error = entry[:3]
            context, completed_at = entry[3:] if len(entry) == 5 else (None, time.time())
            if kind in ('apply', 'undo') or kind.endswith(('_apply', '_undo')):
                # Failed/uncertain writes can also have changed the observed state.
                self.inventory_revision += 1
            change = self.control_finished(kind, result, error, context, completed_at)
            if change:
                if kind.startswith('placement') and not error:
                    self.placement = None
                continue
            if kind.startswith("utility_"):
                self.utility_result(kind, result, error)
                continue
            if kind in ('apply', 'undo', 'hardware_apply', 'hardware_undo', 'placement_apply', 'placement_undo'):
                target = (result or {}).get('target') or self.pending_action.get('target')
                identity = {'id': target['path'], 'inode': target.get('inode')} if target and target.get('path') else None
                label = kind.replace('_', ' ') + (': ' + target['unit'] if target and target.get('unit') else '')
                detail = result or self.pending_action.get('plan', {})
                plan = detail.get('plan', detail)
                if 'setting' in plan:
                    label += f" {plan['setting']}={plan.get('value')} scope={plan.get('scope')}"
                elif 'property' in detail:
                    value = detail.get('before', {}).get(detail['property']) if kind == 'undo' else detail.get('requested', detail.get('value'))
                    label += f" {detail['property']}={value}"
                elif kind.startswith('placement'):
                    value = detail.get('before', {}).get('allowed') if kind.endswith('undo') else detail.get('requested', detail.get('value'))
                    label += ' CPUs=' + (format_cpus(value) if value else 'inherit')
                self.mark_event(label, 'unconfirmed' if error else 'verified', identity, self.pending_action.get('timestamp'))
            if error:
                self.modal = {"stage": "result", "message": error}
            elif kind == 'placement_open':
                self.placement_ready(result)
            elif kind == 'placement_prepare':
                self.modal = dict(stage='placement_confirm', plan=result)
            elif kind in ('memory_prepare','memory_restore_prepare'):
                self.modal=dict(stage='memory_confirm',plan=result,scroll=0)
            elif kind in ('placement_apply', 'placement_undo'):
                self.placement = None
                self.page, self.workload_detail, self.scroll = 7, True, 0
                self.modal = dict(stage='result', message='CPU placement restored and verified.' if kind == 'placement_undo' else 'CPU placement verified in systemd and the kernel. U on Applications: undo placement.')
            elif kind == "hardware_prepare":
                self.modal = {"stage": "hardware_confirm", "plan": result}
            elif kind.startswith("hardware_"):
                self.modal = {"stage": "result", "message": "Hardware change restored and verified." if kind == "hardware_undo" else "Hardware change verified. CPU page: u to Undo."}
            elif kind == "prepare":
                self.modal = {"stage": "confirm", "plan": result}
            else:
                self.modal = {"stage": "result", "message": (
                    f"{result['target']['unit']}: previous CPU {result['property']} restored and verified." if kind == "undo" else
                    f"{result['target']['unit']}: CPU {result['property']} confirmed in systemd and the kernel. u: Undo latest change.")}

    def control_label(self):
        return '[Controls C]' if self.control else '[Read-only C]'

    def open_control_mode(self):
        if self.replay:
            self.modal = dict(stage='result', message='Replay is read-only; resource controls are disabled.')
        else:
            self.modal = dict(stage='control_mode')

    def modal_lines(self):
        modal = self.modal
        if modal['stage'].startswith('workflow_'):
            return self.workflow_lines()
        if modal['stage'].startswith('memory_'):
            return self.memory_control_lines()
        if modal['stage'] == 'impact':
            lines = [part for line in modal['lines'] for part in (textwrap.wrap(line, max(20,min(76,self.screen_size[1]-8))) or [''])]
            count = max(1, self.screen_size[0]-9)
            modal['scroll'] = min(modal['scroll'],max(0,len(lines)-count))
            return ['CONTROL IMPACT', *lines[modal['scroll']:modal['scroll']+count], 'Arrows: scroll | Enter or Esc: return to preview']
        if modal['stage'] == 'insights':
            return self.insight_lines()
        if modal['stage'] == 'control_mode':
            return ['RESOURCE CONTROLS',
                    'Each change requires a preview and confirmation.',
                    'Existing permissions apply. Settings remain after exit.',
                    'Disabling menus does not undo applied changes.',
                    '[ Disable controls ]' if self.control else '[ Enable controls ]',
                    '[ Cancel ]', 'Enter: switch mode | Esc: cancel']
        if modal['stage'].startswith('utility_'):
            return self.utility_lines()
        if modal['stage'] == 'focus':
            return self.focus_lines()
        if modal['stage'] == 'navigate':
            return self.navigation_lines()
        if modal['stage'] == 'placement_confirm':
            return self.placement_lines()
        if modal["stage"].startswith("hardware_"):
            return self.hardware_lines()
        if modal["stage"] == "busy":
            return ["Working...", "Measurements continue in the background."]
        if modal["stage"] == "input":
            property = modal["property"]
            return [f"Set CPU {property} for {modal['target']['unit']}",
                    "Weight: 1-10000, relative priority among sibling groups." if property == "weight" else
                    "Quota: whole percent; 100% = one CPU, 200% = two CPUs.",
                    "Enter 'default' to reset weight or 'max' to remove quota.",
                    "The next screen previews the runtime setting.",
                    "Value: " + modal["value"] + "_", "Enter: preview | Esc: cancel"]
        if modal["stage"] == "confirm":
            plan = modal["plan"]
            property = plan["property"]
            return ["Confirm runtime CPU setting", f"Unit: {plan['target']['unit']}",
                    f"Manager: {plan['target']['manager']}", "Cgroup: " + plan["target"]["path"],
                    f"{property.title()}: {cpu_setting(property, plan['before'][property])} -> {cpu_setting(property, plan['value'])}",
                    preview_note('cpu'),
                    "Applies to this entire unit and its descendants.",
                    "Remains active after UI exit, until changed or reboot.",
                    "Undo restores the previous value using systemd.",
                    *impact_summary(self.snapshot, plan, "service"),
                    "Enter: apply and verify | Esc: cancel"]
        if modal["stage"] == "quit":
            return ["Resource settings may still be active.", "Leaving the dashboard does not restore previous settings.",
                    "Undo history is available only until you exit.",
                    "Enter: exit and keep settings | Esc: go back",
                    "u: review/undo remaining memory, disk, placement, hardware or CPU settings"]
        return [modal["message"], "", "Enter / Esc: close | H: health | J: changes"]

    def modal_key(self, key):
        stage = self.modal["stage"]
        if stage.startswith('workflow_'):
            return self.workflow_key(key)
        if stage.startswith('memory_'):
            return self.memory_control_key(key)
        if stage == 'result' and key in (ord('H'),ord('J')):
            self.open_changes() if key == ord('J') else self.open_insights('health')
            return True
        if stage == 'impact':
            if key in (10,13,27):self.modal = self.modal['preview']
            elif key == curses.KEY_DOWN:self.modal['scroll'] += 1
            elif key == curses.KEY_UP:self.modal['scroll'] = max(0,self.modal['scroll']-1)
            return True
        if key == ord('i') and stage in ('confirm','hardware_confirm','placement_confirm','utility_disk_confirm'):
            kind = {'confirm':'service','hardware_confirm':'hardware','placement_confirm':'placement','utility_disk_confirm':'disk'}[stage]
            self.modal = dict(stage='impact', preview=self.modal, scroll=0,
                              lines=impact_lines(self.snapshot,self.modal['plan'],kind))
            return True
        if stage == 'insights':
            return self.insight_key(key)
        if stage == 'control_mode':
            if key in (10, 13):
                if self.replay:
                    self.open_control_mode()
                else:
                    self.control = not self.control
                    self.modal = self.modal.get('return_to')
            elif key in (27, ord('q')):
                self.modal = self.modal.get('return_to')
            return True
        if stage.startswith("utility_"):
            return self.utility_modal_key(key)
        if stage == 'focus':
            return self.focus_key(key)
        if stage == 'navigate':
            return self.navigation_key(key)
        if stage == 'hardware_menu':
            key = {ord('j'): curses.KEY_DOWN, ord('k'): curses.KEY_UP}.get(key, key)
        if stage == 'placement_confirm':
            return self.placement_confirm_key(key)
        if stage.startswith("hardware_"):
            return self.hardware_key(key)
        if stage == "busy":
            return True
        if stage == "quit":
            if key in (10, 13):
                return False
            if key == ord("u"):
                if any(e['status']!='undone' for e in self.memory_session.events):
                    receipt=self.memory_session.latest()
                    self.job('memory_restore_prepare',self.memory_session.prepare_restore,receipt['receipt_id'])
                elif any(e['status'] != 'undone' for e in self.disk_session.events):
                    self.job("utility_disk_undo", self.disk_session.undo)
                elif any(e['status'] != 'undone' for e in self.placement_session.events):
                    self.job('placement_undo', self.placement_session.undo)
                elif any(e['status'] != 'undone' for e in self.hardware.events):
                    self.job('hardware_undo', self.hardware.undo)
                else:
                    self.job("undo", self.session.undo)
            elif key == 27:
                self.modal = None
            return True
        if key == 27:
            self.modal = None
            return True
        if stage == "input":
            if key in (10, 13):
                modal = self.modal
                self.job("prepare", self.session.prepare, modal["target"], modal["property"], modal["value"])
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.modal["value"] = self.modal["value"][:-1]
            elif 32 <= key < 127 and len(self.modal["value"]) < 12:
                self.modal["value"] += chr(key)
        elif stage == "confirm" and key in (10, 13):
            width = max(20, min(76, self.screen_size[1] - 8))
            length = sum(max(1, len(textwrap.wrap(clean(line), width))) for line in self.modal_lines())
            if length <= self.screen_size[0] - 5:
                self.job("apply", self.session.apply, self.modal["plan"])
        elif stage == "result" and key in (10, 13, ord("8")):
            self.modal = None
            if key == ord("8"):
                self.page = 7
        return True

    def cpu_lines(self, width=60):
        s = self.snapshot
        aggregate = next((c for c in s['cpu'] if c['id'] == 'cpu'), {})
        data = s.get('cpu_hardware', {'cpus': [], 'policies': {}})
        from .topology import scoped_temperatures
        temperatures=scoped_temperatures(data,s.get('temperatures',[]))
        if self.focus and self.focus[0] == 'cpu' and not self.placement:
            data = {**data, 'cpus': [c for c in data['cpus'] if str(c['socket']) == self.focus[1]]}
        metrics = {c['id']: c for c in s['cpu']}
        p = self.placement
        if p:
            lines = ['Place: ' + clean(p['target']['unit']),
                     'Selected: ' + ('inherit parent' if p['inherit'] else format_cpus(p['selected']) or '(empty)'),
                     'x selected | - eligible | ! excluded | click CPU/core/socket',
                     '[ All ]  [ None ]  [ Inherit ]  [ Preview ]  [ Cancel ]']
            self.topology_line_regions = {3: [(0, 7, 'action', ('a',)), (9, 17, 'action', ('n',)),
                                             (19, 30, 'action', ('r',)), (32, 43, 'action', ('enter',)),
                                             (45, 55, 'action', ('esc',))]}
        else:
            lines = [clean(s['cpu_model']),
                     'CPU ' + bar(aggregate.get('busy_pct'), 18),
                     'History ' + self.trend(self.history['cpu'], max(8, width - 10)),
                     '[ CPU settings ] g | click a CPU, core or socket',
                     'Shift-click: show related applications']
            self.topology_line_regions = {}
        if not p:
            lines += self.overlay_lines()
            lines += indicators(s, resources=('cpu',))
            lines.append(self.marker_line(max(8, min(50, width - 30))))
        overlay_cpus = self.overlay_cpus() if self.overlay else None
        tiles, hits = topology_map(data, metrics, width,
            p['selected'] if p else overlay_cpus, set(p['info']['eligible']) if p else {c['id'] for c in data['cpus']}, p['focus'] if p else None,
            temperatures=temperatures,energy=s.get('energy'))
        offset = len(lines)
        self.topology_line_regions.update({row + offset: value for row, value in hits.items()})
        lines.extend(tiles)
        if not p:
            from .presentation import source_note
            lines += [note for name in ('frequencies', 'cpu_hardware') if (note := source_note(s, name, problems_only=True))]
            energy=s.get('energy')
            if energy:
                from .energy import cpu_energy_lines
                lines.extend(cpu_energy_lines(energy,width))
        return lines

    def primary_network(self):
        entries = self.snapshot["network"]
        entries = [entry for entry in entries if entry["name"] != "lo"] or entries
        return max(entries, key=lambda entry: (
            entry["state"] == "up", (entry["rx_bps"] or 0) + (entry["tx_bps"] or 0)), default=None)

    def process_lines(self, width=100):
        rows = [p for p in self.snapshot["processes"] if self.filter.casefold() in
                f"{p['pid']} {p['name']}".casefold()]
        keys = {"cpu": "cpu_pct", "memory": "memory", "pid": "pid", "name": "name"}
        key = keys[self.sort]
        rows.sort(key=lambda p: p[key] if p[key] is not None else -1,
                  reverse=self.sort in ("cpu", "memory"))
        name_width = max(10, min(30, width - 32))
        lines = [f"Sort: {self.sort} | t tree={self.process_tree} | Filter: {clean(self.filter) or '(none)'} | {len(rows)} visible",
                 "CPU: 100% = one logical CPU; memory: resident pages (RSS).",
                 f"{'NAME':<{name_width}} {'PID':>7} {'CPU':>7} {'MEMORY':>10}" +
                 (f" {'RAM':>6} {'NICE':>4} STATE" if width >= 85 else "")]
        for p, depth in (tree_rows(rows) if self.process_tree else [(p,0) for p in rows]):
            label = ("  " * min(depth, 8) + p["name"])
            lines.append(f"{clean(label)[:name_width]:<{name_width}} {p['pid']:>7} {pct(p['cpu_pct']):>7} {size(p['memory']):>10}" +
                         (f" {pct(p['memory_pct']):>6} {p['nice']:>4} {p['state']:>5}" if width >= 85 else ""))
        lines.append(f"Skipped/exited/unreadable processes: {self.snapshot['processes_skipped']}")
        return lines

    def lines(self, width):
        if self.help:
            return context_help(self, width)
        resource = {2: 'memory', 3: 'disk', 4: 'gpu', 6: 'network'}.get(self.page)
        if resource:
            return resource_lines(self, resource, width)
        if self.page == 8:
            from .readability import wrap_lines
            return wrap_lines(self.investigation.lines(), width)
        return {1: self.cpu_lines, 9: self.capability_lines,
                7: self.workload_lines}.get(self.page, self.process_lines)(width)

    def mouse(self, x, y, state):
        if self.sessions_view:
            return self.sessions_view.mouse(x,y,state,self.screen_size[0])
        wheel_up = state & getattr(curses, "BUTTON4_PRESSED", 0)
        wheel_down = state & getattr(curses, "BUTTON5_PRESSED", 0)
        left = state & (getattr(curses, "BUTTON1_CLICKED", 0) | getattr(curses, "BUTTON1_PRESSED", 0))
        right = state & (getattr(curses, "BUTTON3_CLICKED", 0) | getattr(curses, "BUTTON3_PRESSED", 0))
        if self.modal:
            if left and self.modal['stage'].startswith('workflow_'):
                for row, start, end, key in getattr(self,'workflow_regions',[]):
                    if y == row and start <= x < end:
                        self.workflow_key(key)
                        return
            if (self.modal['stage'] in ('insights','impact','memory_confirm') or self.modal['stage'].startswith('workflow_')) and (wheel_up or wheel_down):
                self.modal_key(curses.KEY_UP if wheel_up else curses.KEY_DOWN)
                return
            hit = self.modal_regions.get(y)
            if left and hit and hit[0] <= x < hit[1]:
                if self.modal['stage'].startswith('workflow_'):
                    self.workflow_key(hit[2])
                elif hit[2] == 'impact':
                    self.modal_key(ord('i'))
                elif self.modal['stage'] == 'insights' and not self.modal.get('detail'):
                    self.modal['selected'] = hit[2]
                    self.insight_key(10)
                elif self.modal['stage'] == 'control_mode':
                    self.modal_key(27 if hit[2] == 'cancel' else 10)
                elif self.modal['stage'] == 'focus':
                    self.modal['selected'] = hit[2]
                    self.focus_key(10)
                elif self.modal['stage'] == 'navigate':
                    self.modal['selected'] = hit[2]
                    self.navigation_key(10)
                elif self.modal['stage'] == 'hardware_menu':
                    self.modal['selected'] = hit[2]
                    self.hardware_choose()
                elif self.modal['stage'] == 'placement_confirm' and hit[2] == 'apply':
                    self.placement_confirm_key(10)
                elif self.modal['stage']=='memory_confirm':
                    self.memory_control_key(10 if hit[2]=='apply' else 27)
                elif self.modal['stage'] == 'hardware_confirm' and hit[2] == 'apply':
                    self.hardware_key(10)
                elif self.modal['stage'] == 'utility_disk_confirm' and hit[2] == 'apply':
                    self.utility_modal_key(10)
                elif self.modal['stage'] in ('hardware_confirm', 'placement_confirm', 'utility_disk_confirm') and hit[2] == 'cancel':
                    self.modal = None
            return
        if left and y == self.screen_size[0]-2 and getattr(self,'health_region',None) and self.health_region[0] <= x < self.health_region[1] and not self.placement and not self.editing_filter and not self.profile_view and not self.memory_sharing:
            self.open_insights('health')
            return
        if left and getattr(self,'guide_region',None) and y == 2 and self.guide_region[0] <= x < self.guide_region[1] and not self.profile_view and not self.memory_sharing and not self.browser:
            self.open_insights('guide')
            return
        if left and y == self.screen_size[0] - 2 and 1 <= x < 1 + len(self.control_label()) and not self.placement and not self.editing_filter:
            self.open_control_mode()
            return
        if self.profile_view:
            if left and y == self.screen_size[0]-1:
                for start,end,key in self.action_regions:
                    if start<=x<end:return self.key(key,self.screen_size[0])
            if left:self.profile_view.mouse(x,y)
            if wheel_up:self.profile_view.key(curses.KEY_UP,self.screen_size[0])
            elif wheel_down:self.profile_view.key(curses.KEY_DOWN,self.screen_size[0])
            return
        if self.memory_sharing:
            if left and y == self.screen_size[0] - 1:
                for start,end,key in self.action_regions:
                    if start <= x < end:return self.key(key,self.screen_size[0])
            if left:
                for start,end,row,tab in self.memory_sharing.regions:
                    if y == row and start <= x < end:
                        self.memory_sharing.tab,self.memory_sharing.scroll=tab,0
                        return
            for button,key in ((curses.BUTTON4_PRESSED,curses.KEY_UP),(getattr(curses,'BUTTON5_PRESSED',0),curses.KEY_DOWN)):
                if state & button:
                    for _ in range(3):self.memory_sharing.key(key,self.screen_size[0])
            return
        if self.browser:
            if left and y == self.screen_size[0] - 1:
                for start,end,key in self.action_regions:
                    if start <= x < end:
                        return self.key(key,self.screen_size[0])
            if self.browser.mouse(x,y,state,self.snapshot,self.screen_size[0]) is False:
                self.browser = None
            self.browser_action()
            return
        if self.page == 7 and left and self.application_mode_region and y == 2 and self.application_mode_region[0] <= x < self.application_mode_region[1]:
            return self.key(ord('g'),self.screen_size[0])
        if self.page == 7 and self.application_mode == 'processes':
            if left:
                for start,end,row,identity in self.application_process_regions:
                    if y == row and start <= x < end:
                        self.application_process_manual = True
                        self.application_process = identity
                        return
            for button,key in ((curses.BUTTON4_PRESSED,curses.KEY_UP),(getattr(curses,'BUTTON5_PRESSED',0),curses.KEY_DOWN)):
                if state & button:
                    for _ in range(3):self.application_process_key(key,self.screen_size[0])
                    return
        if left and self.page == 7 and self.browser_button and y == 2 and self.browser_button[0] <= x < self.browser_button[1]:
            self.open_cgroup_browser()
            return
        if left and y == self.screen_size[0] - 1:
            for start, end, key in self.action_regions:
                if start <= x < end:
                    return self.key(key, self.screen_size[0])
        if self.help:
            if state & getattr(curses, 'BUTTON4_PRESSED', 0):
                self.scroll = max(0, self.scroll - 3)
            elif state & getattr(curses, 'BUTTON5_PRESSED', 0):
                self.scroll += 3
            return
        if left and not self.placement:
            if self.page == 7:
                for start,end,row,tab in self.app_tab_regions:
                    if y==row and start<=x<end:
                        self.app_tab,self.scroll=tab,0
                        return
                for start,end,row,identity,inode in self.app_regions:
                    if y==row and start<=x<end:
                        self.workload_id,self.workload_identity=identity,(identity,inode)
                        self.workload_notice,self.workload_detail,self.scroll='',False,0
                        return
                if self.app_regions and y>=5:
                    if x > self.app_regions[0][1]:
                        self.workload_detail = True
                    return  # Right detail pane is not a workload-list row.
        if self.page == 0 and not self.help and left:
            if self.page == 0 and y == 2 and 1 <= x <= 26:
                self.overview_expanded = not self.overview_expanded
                self.scroll = 0
                return
            if y in self.overview_links:
                self.page, self.scroll = self.overview_links[y], 0
                self.focus = None
                return
            for top, start, height, width, page in self.overview_cards_regions:
                if top <= y < top + height and start <= x < start + width:
                    self.page, self.scroll = page, 0
                    self.focus = None
                    return
        if self.page == 7 and self.application_mode == 'cgroups' and not self.workload_detail and left and y >= 7:
            rows = self.workload_rows()
            index = y - 3 + self.scroll - 4
            if 0 <= index < len(rows):
                group = rows[index]
                self.workload_id, self.workload_identity = group['id'], (group['id'],group.get('inode'))
                self.workload_notice = ''
                return
        if self.page == 1 and not self.help and (left or right):
            for start, end, kind, ids in self.topology_regions.get(y, []):
                if start <= x - 1 < end:
                    if self.placement:
                        if kind == 'action':
                            self.placement_key({'enter': 10, 'esc': 27}.get(ids[0], ord(ids[0][0])))
                        else:
                            self.placement_toggle(ids)
                    elif left and state & getattr(curses, 'BUTTON_SHIFT', 0) and kind in ('cpu','core','socket'):
                        self.hardware_selection = tuple(ids)
                        self.open_related()
                    elif kind == 'cpu':
                        self.hardware_menu(('id', ids[0]))
                    elif kind == 'socket':
                        cpu = next(c for c in self.snapshot['cpu_hardware']['cpus'] if c['id'] == ids[0])
                        if cpu['socket'] is not None:
                            self.hardware_menu(('socket', cpu['socket']))
                    return
        if self.page == 1 and not self.help and (left or right) and y in self.cpu_regions:
            self.hardware_menu(self.cpu_regions[y])
            return
        if self.placement and y == 1:
            return
        if state & (getattr(curses, "BUTTON1_CLICKED", 0) | getattr(curses, "BUTTON1_PRESSED", 0)) and y == 1:
            for start, end, page in self.tab_regions:
                if start <= x < end:
                    self.page, self.scroll = page, 0
                    self.focus = None
                    return
        wheel_up = state & getattr(curses, "BUTTON4_PRESSED", 0)
        wheel_down = state & getattr(curses, "BUTTON5_PRESSED", 0)
        if self.page == 7 and self.app_regions and (wheel_up or wheel_down):
            self.workload_detail = x > self.app_regions[0][1]
            if not self.workload_detail:
                for _ in range(3):
                    self.key(curses.KEY_UP if wheel_up else curses.KEY_DOWN, self.screen_size[0])
                return
        if wheel_up:
            self.scroll = max(0, self.scroll - 3)
        elif wheel_down:
            self.scroll += 3

    def key(self, key, height=24):
        if self.sessions_view:
            if key==curses.KEY_MOUSE:
                try:
                    _,x,y,_,state=curses.getmouse();self.mouse(x,y,state)
                except curses.error:pass
            elif not self.sessions_view.key(key,height):
                self.sessions_view=None
                return not self.sessions_only
            return True
        if self.modal:
            if key == curses.KEY_MOUSE:
                try:
                    _, x, y, _, state = curses.getmouse()
                    self.mouse(x, y, state)
                except curses.error:
                    pass
                return True
            return self.modal_key(key)
        if self.editing_filter:
            if key == curses.KEY_MOUSE:
                try:
                    _, x, y, _, state = curses.getmouse()
                    if y == self.screen_size[0] - 1:
                        self.mouse(x, y, state)
                except curses.error:
                    pass
                return True
            field = "workload_filter" if self.filter_context == "workloads" else "filter"
            if key in (10, 13):
                self.editing_filter = False
            elif key == 27:
                setattr(self, field, "")
                self.editing_filter = False
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                setattr(self, field, getattr(self, field)[:-1])
            elif 32 <= key < 127 and len(getattr(self, field)) < 80:
                setattr(self, field, getattr(self, field) + chr(key))
            self.scroll = 0
            return True
        if key == curses.KEY_MOUSE:
            try:
                _, x, y, _, state = curses.getmouse()
                if self.mouse(x, y, state) is False:
                    return False
            except curses.error:
                pass
            return True
        if key in (ord('s'),ord('E'),ord('H'),ord('G')) and not self.placement and not self.profile_view and not self.memory_sharing:
            self.open_insights({ord('s'):'search',ord('E'):'metrics',ord('H'):'health',ord('G'):'guide'}[key])
            return True
        if key == ord('C') and not self.placement:
            self.open_control_mode()
            return True
        if key == ord('J') and not self.placement and not self.profile_view and not self.memory_sharing:
            self.open_changes()
            return True
        if key==ord('L') and not self.placement and not self.profile_view and not self.memory_sharing and not self.help:
            self.open_sessions()
            return True
        if self.profile_view:
            if not self.profile_view.key(key,height):self.profile_view=None
            return True
        if key in (ord('P'),ord('W')) and not self.help and not self.placement and not self.memory_sharing and not (self.active_inspector() and self.active_inspector().help):
            self.open_profile(scheduler=key==ord('W'))
            return True
        if self.memory_sharing:
            if key == ord('r'):
                if self.memory_sharing.result is None and self.memory_sharing.error is None:
                    return True
                old=self.memory_sharing
                old.stop.set()
                self.memory_sharing=MemorySharingView(old.target,self.snapshot.get('processes',[]),self.proc_root)
            elif not self.memory_sharing.key(key,height):
                self.memory_sharing=None
            return True
        if key == ord('V') and not self.help and not self.placement and not (self.active_inspector() and self.active_inspector().help):
            self.open_memory_sharing()
            return True
        if self.browser:
            if not self.browser.key(key,self.snapshot,height):
                self.browser = None
            self.browser_action()
            return True
        key = {curses.KEY_F1: ord('?'), curses.KEY_F4: ord('/'), curses.KEY_F10: ord('q')}.get(key, key)
        if key == ord('?') or self.help and key == 27:
            if self.help:
                self.help, self.scroll = False, self.help_return_scroll
            else:
                self.help_return_scroll, self.scroll, self.help = self.scroll, 0, True
            return True
        if self.help:
            if key in (curses.KEY_DOWN, ord('j'), curses.KEY_NPAGE):
                self.scroll += max(1, height - 5) if key == curses.KEY_NPAGE else 1
            elif key in (curses.KEY_UP, ord('k'), curses.KEY_PPAGE):
                self.scroll = max(0, self.scroll - (max(1, height - 5) if key == curses.KEY_PPAGE else 1))
            elif key == curses.KEY_HOME:
                self.scroll = 0
            elif key == curses.KEY_END:
                self.scroll = 10**9
            return True
        if self.page == 7 and key == ord('I') and not self.placement:
            from .actions import dispatch
            if self.application_mode == 'processes':
                self.selected_application_process(self.application_process_rows())
            else:
                self.selected_workload()
            dispatch(self, 'inspect')
            return True
        if self.placement:
            return self.placement_key(key)
        if self.page == 7 and key == ord('g'):
            self.application_mode = 'cgroups' if self.application_mode == 'processes' else 'processes'
            self.scroll,self.workload_detail = 0,False
            return True
        if self.application_process_key(key,height):
            return True
        if self.page == 7 and key in (10, 13, curses.KEY_RIGHT, ord('v'), ord('T')):
            self.open_cgroup_browser(root=key == ord('T'))
            return True
        if self.workspace_key(key):
            return True
        if self.utility_key(key):
            return True
        if self.replay and key in (curses.KEY_LEFT, curses.KEY_RIGHT, ord(' '), ord('('), ord(')'), ord(','), ord('.')):
            if key == ord(' '):
                self.replay_playing = not self.replay_playing
            else:
                self.replay_delta += {curses.KEY_LEFT: -1, curses.KEY_RIGHT: 1, ord('('): -len(self.replay.offsets), ord(')'): len(self.replay.offsets), ord(','): -10, ord('.'): 10}[key]
                self.replay_playing = False
            return True
        if key == ord('f') and self.page in (0, 1, 2, 3, 4, 6):
            self.open_focus()
            return True
        if key in (ord('['), ord(']')) and self.focus:
            self.cycle_focus(-1 if key == ord('[') else 1)
            return True
        if key == ord('o'):
            if self.page == 7:
                group = self.selected_workload()
                if group:
                    self.overlay = {'id': group['id'], 'inode': group.get('inode')}
                    self.focus = None
                    self.page, self.scroll = 1, 0
            else:
                self.overlay = None
            return True
        if key == ord('M') and self.snapshot:
            self.mark_event('Manual baseline', target=self.overlay, timestamp=self.snapshot['timestamp'])
            return True
        if self.page == 8 and key in (curses.KEY_UP, curses.KEY_DOWN, ord('j'), ord('k')):
            count = len(self.investigation.events)
            if count:
                self.investigation.selected = (self.investigation.selected + (1 if key in (curses.KEY_DOWN, ord('j')) else -1)) % count
                self.scroll = 0
            return True
        if self.page==8 and key in (ord('['),ord(']')):
            durations=(5,15,30,60);index=durations.index(self.investigation.window_seconds)
            self.investigation.window_seconds=durations[(index+(1 if key==ord(']') else -1))%len(durations)]
            return True
        if key == ord(':'):
            self.modal = dict(stage='navigate', query='', selected=0)
            return True
        key = {ord('j'): curses.KEY_DOWN, ord('k'): curses.KEY_UP}.get(key, key)
        if key in (ord("q"), 27):
            if key == 27 and self.page == 7 and self.workload_detail:
                self.workload_detail, self.scroll = False, 0
                return True
            if any(event['status'] in ('confirmed','unconfirmed') for session in self.control_sessions().values() for event in session.events):
                self.modal = {"stage": "quit"}
                return True
            return False
        if self.page == 0 and not self.help and key == ord('e'):
            self.overview_expanded = not self.overview_expanded
            self.scroll = 0
            return True
        if self.page == 1 and key in (ord('g'), ord('u')):
            if key == ord('g') or not self.control:
                self.hardware_menu()
            else:
                self.job('hardware_undo', self.hardware.undo)
            return True
        if self.page == 7 and key in (ord('p'), ord('U')):
            if key == ord('p'):
                self.open_placement()
            elif self.control:
                self.job('placement_undo', self.placement_session.undo)
            return True
        if self.page==7 and key==ord('h'):
            self.open_memory_control(self.selected_workload())
            return True
        if self.page == 7 and key in (ord("w"), ord("l"), ord("u")):
            if not self.control:
                self.open_control_mode()
            elif self.screen_size[0] < 18 or self.screen_size[1] < 70:
                self.modal = {"stage": "result", "message": "Resize to at least 70 columns by 18 rows to preview CPU controls."}
            elif key == ord("u"):
                self.job("undo", self.session.undo)
            else:
                group = self.selected_workload()
                if not group or not group["controllable"]:
                    self.modal = {"stage": "result", "message": "Select a systemd service/scope owned by an accessible manager."}
                else:
                    self.modal = {"stage": "input", "target": dict(group), "property": "weight" if key == ord("w") else "quota", "value": ""}
            return True
        if self.page == 7 and not self.workload_detail:
            rows = self.workload_rows()
            if rows and key in (curses.KEY_DOWN, curses.KEY_UP, curses.KEY_NPAGE, curses.KEY_PPAGE, curses.KEY_HOME, curses.KEY_END):
                index = next((i for i, row in enumerate(rows) if row["id"] == self.workload_id), -1)
                step = max(1, height - 9) if key in (curses.KEY_NPAGE, curses.KEY_PPAGE) else 1
                if key in (curses.KEY_DOWN, curses.KEY_NPAGE):
                    index = min(len(rows) - 1, index + step)
                elif key in (curses.KEY_UP, curses.KEY_PPAGE):
                    index = max(0, index - step)
                else:
                    index = 0 if key == curses.KEY_HOME else len(rows) - 1
                self.workload_id = rows[index]["id"]
                self.workload_identity = (rows[index]["id"], rows[index].get("inode"))
                self.workload_notice = ""
                line, capacity = index + 4, max(1, height - 5)
                self.scroll = min(self.scroll, line) if line < self.scroll else max(self.scroll, line - capacity + 1)
                return True
        if ord("1") <= key <= ord("9"):
            self.page, self.scroll = key - ord("1"), 0
            self.focus = None
        elif key in (9, curses.KEY_BTAB):
            self.page = (self.page + (1 if key == 9 else -1)) % len(self.names)
            self.focus = None
            self.scroll = 0
        elif key == ord("?"):
            self.help, self.scroll = not self.help, 0
        elif key == ord(" "):
            self.paused = not self.paused
        elif key == ord("/"):
            self.filter_context = "workloads" if self.page == 7 else "processes"
            self.page, self.scroll, self.editing_filter = (7 if self.page == 7 else 5), 0, True
            if self.page == 7:
                self.workload_detail = False
        elif key in map(ord, "cmnp"):
            self.sort = {ord("c"): "cpu", ord("m"): "memory", ord("n"): "name", ord("p"): "pid"}[key]
            self.scroll = 0
        elif key in (curses.KEY_DOWN, curses.KEY_NPAGE):
            self.scroll += max(1, height - 5) if key == curses.KEY_NPAGE else 1
        elif key in (curses.KEY_UP, curses.KEY_PPAGE):
            self.scroll = max(0, self.scroll - (max(1, height - 5) if key == curses.KEY_PPAGE else 1))
        elif key == curses.KEY_HOME:
            self.scroll = 0
        elif key == curses.KEY_END:
            self.scroll = 10**9
        return True


def put(screen, y, x, text, attr=0, limit=None):
    h, w = screen.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w - 1:
        return
    try:
        screen.addnstr(y, x, clean(text), max(0, min(w - x - 1, limit if limit is not None else w)), attr)
    except curses.error:
        pass  # Terminal may resize between bounds check and write.


def draw(screen, view, interval, error=None):
    screen.erase()
    h, w = screen.getmaxyx()
    view.screen_size = h, w
    view.action_regions = []
    view.workflow_regions = []
    view.guide_region = None
    view.application_process_regions = []
    view.application_mode_region = None
    view.browser_button = None
    view.app_regions = []
    view.app_tab_regions = []
    view.overview_links = {}
    view.overview_cards_regions = []
    view.topology_regions = {}
    view.cpu_regions = {}
    view.modal_regions = {}
    if h < 10 or w < 40:
        put(screen, 0, 0, "Resize to at least 40 x 10. q: quit")
        screen.refresh()
        return
    if view.sessions_view:
        view.sessions_view.draw(screen,w,h)
        return
    s = view.snapshot
    stamp = time.strftime("%H:%M:%S", time.localtime(s["timestamp"])) if s else "waiting"
    state = "REPLAY" if view.replay else "PAUSED" if view.paused else "LIVE"
    if error:
        state = "COLLECTOR ERROR"
    elif s and not view.replay and not view.paused and time.time() - s["timestamp"] > max(5, interval * 3):
        state = "STALE"
    accent = view.colors["accent"] | curses.A_BOLD
    muted = view.colors["muted"] | curses.A_DIM
    put(screen, 0, 1, f"RESOURCE LENS  {state}  {interval:g}s  |  {stamp}", accent)
    if s and w >= 95:
        put(screen, 0, 60, s["hostname"], muted)
    labels = ("Home", "CPU", "RAM", "Disk", "GPU", "Procs", "Net", "Apps", "Timeline", "System")
    view.tab_regions = []
    x = 1
    compact_tabs = [f'{(i+1)%10}:{label}' for i,label in enumerate(labels)]
    named_tabs = w >= sum(map(len,compact_tabs)) + len(labels) + 2
    for page, label in enumerate(labels):
        text = f" {(page + 1) % 10} {label} " if w >= 105 else compact_tabs[page] if named_tabs else f" {(page + 1) % 10} "
        attr = accent | curses.A_REVERSE if page == view.page else muted
        put(screen, 1, x, text, attr)
        view.tab_regions.append((x, x + len(text), page))
        x += len(text) + 1
    if not named_tabs:
        put(screen, 1, x + 1, view.names[view.page], accent)
    status = "Newest samples at right"
    if not s:
        put(screen, 4, 2, "Collecting the first sample...", muted)
    elif view.profile_view:
        view.profile_view.draw(screen,w,h)
    elif view.memory_sharing:
        view.memory_sharing.draw(screen,w,h)
        status = "Memory sharing | [] tabs | arrows scroll | r refresh | Esc back"
    elif view.browser:
        view.browser.draw(screen,s,w,h,view=view)
        tree = s.get('cgroup_tree') or {}
        coverage = "PARTIAL: unreadable directories / node limit" if tree.get('truncated') or tree.get('errors') else 'visible directories'
        status = ("Process details | arrows/wheel: scroll | Esc: back" if view.browser.process else
                  "Cgroup browser | "+coverage+" | group totals include children")
        if view.browser.inspector:
            status = 'Inspector / '+view.browser.inspector.request[0]+' | Tab sections | Esc: previous selection'
    elif view.page == 7 and not view.help and view.application_mode == 'processes':
        status = view.draw_application_processes(screen,w,h)
    elif view.page == 7 and not view.help and w >= 100 and h >= 20:
        status = view.draw_applications(screen,w,h)
    elif view.page == 0 and not view.help and view.focus:
        kind, identity = view.focus
        summary = compact_overview(view)
        lines = summary[:summary.index('')] + [f'FOCUS {kind}: {identity} | f restore | [ / ] switch']
        lines += view.cpu_lines(w - 3) if kind == 'cpu' else resource_lines(view, kind, w - 3)
        capacity = h - 5
        view.scroll = min(view.scroll, max(0, len(lines) - capacity))
        for i, line in enumerate(lines[view.scroll:view.scroll + capacity]):
            put(screen, i + 3, 1, line, accent if i < 5 else 0)
        status = 'Focused overview | other resources summarized above'
    elif not view.help and view.page == 0 and view.overview_expanded:
        lines, links = expanded_overview(view, w - 3)
        capacity = h - 5
        view.scroll = min(view.scroll, max(0, len(lines) - capacity))
        for i, line in enumerate(lines[view.scroll:view.scroll + capacity]):
            index = view.scroll + i
            if index in links:
                view.overview_links[i + 3] = links[index]
            section_page = next((links[key] for key in sorted(links, reverse=True) if key <= index), 1)
            color = view.colors[{1: 'cpu', 2: 'memory', 3: 'disk', 4: 'gpu', 6: 'network'}.get(section_page, 'accent')]
            put(screen, i + 3, 1, line, color | (curses.A_BOLD if index in links else 0))
        status = f"Expanded overview | {view.scroll + 1}-{min(len(lines), view.scroll + capacity)} of {len(lines)} | arrows/wheel scroll"
    elif view.page == 0 and not view.help:
        layout = card_layout(h, w)
        if layout:
            first = layout[0]
            cards = overview_cards(view, first[3], first[2], view.colors)
            view.overview_cards_regions = [(*geometry, page) for geometry, page in zip(layout, (1, 2, 3, 4, 6, 5))]
            for geometry, (title, hint, color, content) in zip(layout, cards):
                draw_card(screen, put, geometry, title, hint, color, content, view.unicode)
            status = "Click a card for details | G: start here | N/A: unavailable"
        else:
            lines = compact_overview(view)
            capacity = h - 5
            view.scroll = min(view.scroll, max(0, len(lines) - capacity))
            for i, line in enumerate(lines[view.scroll:view.scroll + capacity]):
                put(screen, i + 3, 1, line, curses.A_BOLD if i < 2 else 0)
            status = f"Overview | {view.scroll + 1}-{min(len(lines), view.scroll + capacity)} of {len(lines)} | arrows scroll"
    else:
        lines = view.lines(w - 3)
        # Put a readable multi-row chart above percentage-based detail pages.
        kinds = {2: "memory", 3: "disk", 4: "gpu"}
        if not view.help and view.page in kinds and not view.focus:
            kind = kinds[view.page]
            color = view.colors[kind]
            plot = chart(view.history[kind], max(1, w - 9), 3, unicode=view.unicode)
            lines = ["History: 0-100%, oldest left / newest right"] + [
                ("100% " if i == 0 else "  0% " if i == 2 else "     ") + row
                for i, row in enumerate(plot)] + [""] + [
                line for line in lines if not line.startswith(("History ", "Active time history "))]
        else:
            color = accent
        capacity = h - 5
        view.scroll = min(view.scroll, max(0, len(lines) - capacity))
        for i, line in enumerate(lines[view.scroll:view.scroll + capacity]):
            index = view.scroll + i
            if view.help:
                attribute = accent if line.startswith(('HELP /', '-- ')) else (muted if index in (1, 2, 3) else 0)
            elif view.page == 5 and not view.help:
                attribute = accent | curses.A_REVERSE if index == 2 else (curses.A_BOLD if index >= 3 and index % 2 else 0)
            elif view.page == 7 and not view.workload_detail and not view.help:
                attribute = accent | curses.A_REVERSE if line.startswith(">") or index == 3 else 0
            else:
                attribute = color if index < 5 else (accent if i == 0 else 0)
            if view.page in (2, 3, 4, 6) and not view.help and (line.startswith('+') or
                    (line.startswith('| ') and index > 0 and lines[index - 1].startswith('+'))):
                attribute = view.colors[{2: 'memory', 3: 'disk', 4: 'gpu', 6: 'network'}[view.page]] | curses.A_BOLD
            if view.page == 1 and not view.help:
                view.topology_regions[i + 3] = view.topology_line_regions.get(index, [])
                if line.startswith('[ CPU settings ]'):
                    view.cpu_regions[i + 3] = ('all', None)
            draw_text(screen, i + 3, 1, line, attribute, accent=accent)
            if view.help and w - 3 >= 60 and line.startswith('  ') and line[:20].strip():
                put(screen, i + 3, 1, line[:20], accent, 20)
            if view.page == 1 and not view.help:
                for start, end, kind, ids in view.topology_regions.get(i + 3, []):
                    attr = view.colors['cpu']
                    if kind in ('core', 'socket'):
                        attr |= curses.A_BOLD
                    if view.overlay and kind == 'cpu' and ids[0] in (view.overlay_cpus() or set()):
                        attr |= curses.A_REVERSE
                    if view.placement and kind == 'cpu':
                        if ids[0] in view.placement['selected']:
                            attr |= curses.A_REVERSE
                        if ids[0] not in view.placement['info']['eligible']:
                            attr = muted
                        if ids[0] == view.placement['focus']:
                            attr |= curses.A_BOLD
                    put(screen, i + 3, start + 1, line[start:end], attr, end - start)

        status = f"{'Help' if view.help else view.names[view.page]} | {view.scroll + 1}-{min(len(lines), view.scroll + capacity)} of {len(lines)} | arrows scroll"
    if view.page == 0 and not view.help:
        put(screen, 2, 1, '[ - Collapse overview ] e' if view.overview_expanded else '[ + Expand overview ] e', accent)
        if w >= 48:
            label = '[ G Start here ]'
            put(screen,2,29,label,accent | curses.A_REVERSE)
            view.guide_region = (29,29+len(label))
    if view.page == 7 and not view.browser and not view.memory_sharing and not view.profile_view and not view.help and view.application_mode == 'cgroups':
        label = '[ Enter Open group ]'
        put(screen,2,1,label,accent | curses.A_REVERSE)
        view.browser_button = (1,1+len(label))
        put(screen,2,24,'I: inspect | T: root | [] metrics')
    if view.page == 7 and not view.browser and not view.memory_sharing and not view.profile_view and not view.help:
        label = '[ g: '+('Service groups' if view.application_mode == 'processes' else 'Processes')+' ]'
        x = 1 if view.application_mode == 'processes' else max(1,w-len(label)-2)
        put(screen,2,x,label,accent | curses.A_REVERSE)
        view.application_mode_region = (x,x+len(label))
    if view.focus:
        status += f' | focus {view.focus[1]} (f restores)'
    if view.replay:
        status = f'REPLAY {view.replay.index + 1}/{len(view.replay.offsets)} | Left/Right step | Space play/pause | ' + status
    if view.recorder:
        status = (view.recorder.error or 'REC ' + view.recorder.path) + ' | ' + status
    if view.incident_status:
        status = view.incident_status + " | " + status
    if view.page == 8 and not view.help:
        status = status.replace('arrows scroll', 'PgUp/PgDn scroll; Up/Down select event')
    message = error or ("; ".join(s["warnings"]) if s else "") or status
    if view.editing_filter:
        value = view.workload_filter if view.filter_context == "workloads" else view.filter
        message = f"Filter: {value}_ ({view.filter_context})  Enter: apply  Esc: clear"
    freshness = 'Recorded' if view.replay else ('No sample' if not s else
                 'Stale' if time.time()-s['timestamp'] > getattr(view,'health_stale_after',5) else 'Live')
    health_label = ' [' + freshness + '; H health] '
    view.health_region = (1+len(view.control_label()), 1+len(view.control_label())+len(health_label))
    message = view.control_label() + health_label + message
    put(screen, h - 2, 1, message, view.colors["warning"] if error else muted)
    x = 1
    for label, key in actions(view):
        text = ' ' + label + ' '
        if x + len(text) > w - 1:
            continue
        put(screen, h - 1, x, text, accent | curses.A_REVERSE, len(text))
        view.action_regions.append((x, x + len(text), key))
        x += len(text) + 1
    if view.modal:
        content_width = max(20, min(76, w - 8))
        lines = [piece for line in view.modal_lines() for piece in (textwrap.wrap(clean(line), content_width) or [""])]
        if view.modal["stage"] in ("confirm", "hardware_confirm", "placement_confirm", "utility_disk_confirm") and len(lines) > h - 5:
            lines = ["The full target preview does not fit this terminal.",
                     "Resize larger to review and confirm, or Esc to cancel.",
                     "Enter is disabled until the complete preview is visible."]
        box_height = min(h - 2, len(lines) + 4)
        box_width = min(w - 2, content_width + 4)
        top, left = (h - box_height) // 2, (w - box_width) // 2
        for row in range(box_height):
            put(screen, top + row, left, " " * box_width, curses.A_REVERSE, box_width)
        put(screen, top, left, "+" + "-" * (box_width - 2) + "+", accent, box_width)
        put(screen, top + box_height - 1, left, "+" + "-" * (box_width - 2) + "+", accent, box_width)
        option_index = view.modal.get('offset', 0)
        for index, line in enumerate(lines[:box_height - 3]):
            if view.modal['stage'].startswith('workflow_'):
                view.workflow_regions.extend((top+1+index, left+2+start, left+2+end, key)
                                             for start,end,key in workflow_buttons(line))
            if view.modal['stage'] in ('hardware_menu', 'navigate', 'focus', 'insights') and (line.startswith('> ') or line.startswith('- ')):
                view.modal_regions[top + 1 + index] = (left, left + box_width, option_index)
                option_index += 1
            if view.modal['stage'] in ('confirm','hardware_confirm','placement_confirm','utility_disk_confirm') and line.startswith('[ Impact ]'):
                view.modal_regions[top + 1 + index] = (left, left + box_width, 'impact')
            if view.modal['stage'] == 'control_mode' and line.startswith(('[ Enable controls ]', '[ Disable controls ]', '[ Cancel ]')):
                view.modal_regions[top + 1 + index] = (left, left + box_width, 'cancel' if line.startswith('[ Cancel ]') else 'toggle')
            if view.modal['stage'] in ('hardware_confirm', 'placement_confirm', 'utility_disk_confirm','memory_confirm') and line.startswith(('[ Apply ]', '[ Cancel ]')):
                view.modal_regions[top + 1 + index] = (left, left + box_width, 'apply' if line.startswith('[ Apply ]') else 'cancel')
            draw_text(screen, top + 1 + index, left + 2, line, curses.A_REVERSE | (curses.A_BOLD if index == 0 else 0), content_width, accent=accent)
    screen.refresh()


def read_input(screen, view):
    """Apply queued keys in order, then render once; bound work to avoid starvation."""
    key = screen.getch()
    if key == -1:
        return True
    deadline = time.monotonic() + .004
    screen.timeout(0)
    try:
        for index in range(64):
            if not view.key(key, screen.getmaxyx()[0]):
                return False
            if index == 63 or key in (curses.KEY_MOUSE, curses.KEY_RESIZE) or time.monotonic() >= deadline:
                break
            key = screen.getch()
            if key == -1:
                break
            if key in (curses.KEY_MOUSE, curses.KEY_RESIZE):
                # A preceding navigation key may have changed the layout. Draw
                # its new hit regions before handling the next mouse/resize event.
                curses.ungetch(key)
                break
        return True
    finally:
        screen.timeout(100)


def run(collector, interval, ascii=False, control=False, recorder=None, replay=None, preferences=None, incidents=None, helper=None, sessions=None):
    latest = {"snapshot": None, "sequence": 0, "error": None}
    lock, stop = threading.Lock(), threading.Event()
    alerts = queue.SimpleQueue()

    def collect():
        while not stop.is_set():
            started = time.monotonic()
            try:
                snapshot = collector.sample()
                if stop.is_set():
                    break  # Discard an unfinished final read; do not publish after exit.
                if incidents:
                    try:
                        event = incidents.observe(snapshot)
                        if event:
                            alerts.put(event)
                    except (OSError, ValueError) as exc:
                        incidents.status = 'Incident recording failed: ' + str(exc)
                if recorder:
                    recorder.write('sample', snapshot)
                with lock:
                    latest.update(snapshot=snapshot, sequence=latest["sequence"] + 1, error=None)
            except Exception as exc:
                with lock:
                    latest["error"] = f"Collection failed: {type(exc).__name__}: {exc}"
            stop.wait(max(0.05, interval - (time.monotonic() - started)))
        if incidents:
            incidents.close()

    def terminal(screen):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        # A bare Escape otherwise commonly waits one second in ncurses.
        # Keep sequence decoding enabled for arrows/function keys/mouse over SSH.
        try:
            escape_ms = int(os.environ.get('ESCDELAY', '1'))
            curses.set_escdelay(escape_ms if escape_ms > 0 else 1)
        except (ValueError, curses.error):
            curses.set_escdelay(1)
        screen.timeout(100)  # Input wakes immediately; this only bounds idle polling.
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS)
            curses.mouseinterval(0)
        except curses.error:
            pass
        encoding = (sys.stdout.encoding or "").lower().replace("-", "")
        view, sequence = View(unicode=not ascii and encoding == "utf8", control=control and replay is None), -1
        view.health_stale_after = max(5, interval * 3)
        view.proc_root = str(getattr(collector, "proc", "/proc"))
        view.cgroup_root = str(getattr(collector, 'sys', '/sys')) + '/fs/cgroup'
        for name,value in (preferences or {}).items():
            setattr(view, name, value)
        if helper:
            from .helper import RemoteSession
            view.session = RemoteSession(helper, 'cpu')
            view.disk_session = RemoteSession(helper, 'disk')
        view.colors = palette()
        if sessions is not None:
            view.sessions_only=True
            view.open_sessions(sessions or None)
        view.recorder, view.replay = recorder, replay
        replay_position, replay_deadline = -1, time.monotonic()
        if replay:
            view.investigation.events = list(replay.events)

        previous_render = None
        inventory_revision = view.inventory_revision
        while True:
            view.poll_actions()
            if not replay and inventory_revision != view.inventory_revision:
                invalidate = getattr(collector, 'invalidate_inventory', None)
                if invalidate:
                    invalidate()
                inventory_revision = view.inventory_revision
            while not alerts.empty():
                event = alerts.get()
                view.mark_event(event['label'],event['status'],event.get('target'),event['timestamp'],
                                **{k:v for k,v in event.items() if k not in ('label','status','target','timestamp')})
            if replay:
                desired = replay.index + view.replay_delta
                view.replay_delta = 0
                if view.replay_playing and time.monotonic() >= replay_deadline:
                    desired += 1
                    replay_deadline = time.monotonic() + interval
                desired = max(0, min(len(replay.offsets) - 1, desired))
                if desired != replay_position:
                    view.history.clear()
                    view.sample_times.clear()
                    view.investigation.samples.clear()
                    view.diagnostic_history.clear()
                    for sample in replay.window(desired):
                        view.update(sample)
                    replay_position = desired
                if desired == len(replay.offsets) - 1:
                    view.replay_playing = False
            with lock:
                current = latest.copy()
            if current["snapshot"] is not None and current["sequence"] != sequence:
                view.update(current["snapshot"])
                sequence = current["sequence"]
            if not replay:
                request = getattr(collector, 'request_target', None)
                if request:
                    from .targets import selection_request
                    request(selection_request(view))
            view.collector_error = current['error']
            view.incident_status = incidents.status if incidents else None
            stale = bool(not replay and view.snapshot and time.time() - view.snapshot["timestamp"] > max(5, interval * 3))
            render = (id(view.snapshot), view.page, view.scroll, view.sort, view.filter,
                      view.editing_filter, view.workload_filter, view.help, view.paused, current["error"],
                      screen.getmaxyx(), stale, view.control, repr({k:v for k,v in view.modal.items() if k != 'items'}) if view.modal and view.modal['stage']=='insights' else repr(view.modal), view.workload_id, view.workload_detail, repr(view.placement), view.overview_expanded, view.focus, repr(view.overlay), len(view.investigation.events), view.investigation.selected, view.replay_playing, view.workload_sort, view.process_tree, view.incident_status, view.application_mode, view.application_process, view.app_tab, repr(view.relation), view.hardware_selection, (view.browser.path, view.browser.inode, view.browser.selected, view.browser.process, view.browser.sort, view.browser.detail_scroll) if view.browser else None, (view.memory_sharing.tab, view.memory_sharing.scroll, id(view.memory_sharing.result), view.memory_sharing.error) if view.memory_sharing else None, (view.profile_view.scroll, id(view.profile_view.result), view.profile_view.error, view.profile_view.choosing, view.profile_view.selection, view.profile_view.details) if view.profile_view else None)
            render += (view.browser.context_detail if view.browser else None,)
            render += (view.browser.inspector.signature() if view.browser and view.browser.inspector else None,)
            render += ((view.profile_view.target.get('duration'), view.profile_view.context_error) if view.profile_view else None,)
            render += ((view.sessions_view.version,view.sessions_view.pending) if view.sessions_view else None,)
            render += (view.investigation.window_seconds,)
            if render != previous_render:
                draw(screen, view, interval, current["error"])
                previous_render = render
            if not read_input(screen, view):
                if view.sessions_view:view.sessions_view.close()
                for job in view.profile_jobs:job.stop.set()
                break

    worker = threading.Thread(target=collect, daemon=True)
    if not replay and collector is not None:
        worker.start()
    try:
        curses.wrapper(terminal)
    finally:
        stop.set()
        if not replay:
            close = getattr(collector, "close", None)
            if close:
                close()
            # Collection is read-only and the worker is a daemon. A slow kernel
            # read or GPU query must not hold the terminal application open.
            # Recorder.close serializes with any write already in progress.
            # Do not wait for an unfinished read-only sample. The daemon exits
            # cooperatively; completed recording writes are still finalized.
            if incidents and not worker.is_alive():
                incidents.close()
