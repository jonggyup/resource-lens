"""Usability actions kept separate from core rendering and telemetry."""
import curses
import copy
import textwrap
from .usability import save_preferences, export_report, capability_lines
from .disk_controls import DiskSession


class UsabilityView:
    def usability_init(self):
        self.workload_sort = 'cpu'
        self.process_tree = False
        self.workload_identity = None
        self.workload_notice = ''
        self.collector_error = None
        self.incident_status = None
        self.disk_session = DiskSession()

    def capability_lines(self, width):
        return [part for line in capability_lines(self.snapshot, self.collector_error, bool(self.replay))
                for part in (textwrap.wrap(line, max(20,width)) or [''])]

    def utility_key(self, key):
        if key == ord('0'):
            self.page, self.scroll, self.focus = 9, 0, None
        elif key == ord('S'):
            self.job('utility_save', save_preferences, copy.copy(self), allow_replay=True)
        elif key == ord('R'):
            report_view = copy.copy(self)
            # Collected samples are immutable after publication. Snapshot only
            # their references to avoid copying hundreds of workload snapshots
            # on the input thread; comparison/export work runs in the worker.
            report_view.investigation = copy.copy(self.investigation)
            report_view.investigation.samples = tuple(self.investigation.samples)
            report_view.investigation.events = tuple(dict(e) for e in self.investigation.events)
            self.job('utility_report', export_report, report_view, allow_replay=True)
        elif key == ord('N'):
            self.modal = dict(stage='utility_name', value='')
        elif key == ord('t') and self.page == 5:
            self.process_tree = not self.process_tree
        elif self.page == 7 and key in map(ord,'cmni'):
            self.workload_sort = {ord('c'):'cpu',ord('m'):'memory',ord('n'):'name',ord('i'):'io'}[key]
            self.scroll = 0
        elif self.page == 7 and key == ord('d'):
            group = self.selected_workload()
            devices = {row['device'] for row in (group or {}).get('io_devices', [])}
            disk = next((d for d in self.snapshot['disks'] if d.get('identity','').split(':')[:2] in [dev.split(':') for dev in devices]), None)
            if disk:
                self.page, self.focus, self.scroll = 3, ('disk',disk['name']), 0
            else:
                self.modal = dict(stage='result', message='No accounting device maps to a visible whole disk. Inspect I/O device IDs and backing topology.')
        elif self.page == 7 and key in (ord('b'),ord('B')):
            if self.replay or not self.control:
                self.open_control_mode()
            elif key == ord('B'):
                self.job('utility_disk_undo', self.disk_session.undo)
            else:
                group = self.selected_workload()
                if not group or not group.get('controllable'):
                    self.modal = dict(stage='result', message='Select a controllable systemd service/scope.')
                else:
                    self.modal = dict(stage='utility_disk', target=dict(group), value='')
        else:
            return False
        return True

    def utility_lines(self):
        modal = self.modal
        if modal['stage'] == 'utility_name':
            return ['Name a baseline marker', modal['value']+'_', 'Enter: save marker | Esc: cancel']
        if modal['stage'] == 'utility_disk':
            devices = ', '.join(d['name'] for d in self.snapshot['disks'])
            return ['Set runtime disk bandwidth for '+modal['target']['unit'],
                    'Visible devices: '+devices, 'Enter: DEVICE read|write RATE',
                    'Example: sda write 10M  (M = MiB/s); max removes this limit.',
                    'Applies to unit descendants; buffered/cache traffic may differ.',
                    'Value: '+modal['value']+'_', 'Enter: preview | Esc: cancel']
        plan = modal['plan']
        dev = plan['device']
        return ['Confirm runtime disk bandwidth', 'Unit: '+plan['target']['unit'],
                f"Device: {dev['path']} ({dev['device']})", 'Backing: '+(', '.join(dev['backing']) or 'none reported'),
                'Holders: '+(', '.join(dev['holders']) or 'none reported'),
                f"{plan['direction']} bytes/sec: {plan['before'][plan['direction']].get(dev['path'], 'max')} -> {plan['value'] if plan['value'] is not None else 'max'}",
                'Affects this unit and descendants; ancestor limits still apply.',
                'Cache hits bypass disk I/O. Kernel read-back does not prove workload enforcement.',
                'Runtime setting remains after exit. B on Workloads: Undo.',
                '[ Apply ] Enter: apply and verify', '[ Cancel ] Esc: cancel']

    def utility_modal_key(self, key):
        modal = self.modal
        if key == 27:
            self.modal = None
        elif key in (10,13):
            if modal['stage'] == 'utility_disk_confirm':
                height, width = self.screen_size
                wrapped = sum(max(1, len(textwrap.wrap(line, max(20, min(76, width-8))))) for line in self.utility_lines())
                if width < 70 or height < 18 or wrapped > height-5:
                    return True
                self.job('utility_disk_apply', self.disk_session.apply, modal['plan'])
            elif modal['stage'] == 'utility_name':
                if modal['value'].strip() and self.snapshot:
                    self.mark_event(modal['value'].strip(), target=self.overlay, timestamp=self.snapshot['timestamp'])
                self.modal = None
            else:
                fields = modal['value'].split()
                if len(fields) != 3:
                    self.modal = dict(stage='result', message='Expected DEVICE read|write RATE, for example sda write 10M.')
                else:
                    self.job('utility_disk_prepare', self.disk_session.prepare, modal['target'], *fields)
        elif modal['stage'] != 'utility_disk_confirm':
            if key in (curses.KEY_BACKSPACE,127,8):
                modal['value'] = modal['value'][:-1]
            elif 32 <= key < 127 and len(modal['value']) < 100:
                modal['value'] += chr(key)
        return True

    def utility_result(self, kind, result, error):
        if kind in ('utility_disk_apply','utility_disk_undo'):
            detail = result or self.pending_action.get('plan', {})
            plan = detail.get('plan', detail)
            target = plan.get('target', {})
            identity = {'id':target['path'],'inode':target.get('inode')} if target.get('path') else None
            self.mark_event(kind.replace('utility_','').replace('_',' '), 'unconfirmed' if error else 'verified', identity)
        if error:
            self.modal = dict(stage='result', message=error)
        elif kind == 'utility_disk_prepare':
            self.modal = dict(stage='utility_disk_confirm', plan=result)
        else:
            message = ('Saved: '+str(result)) if kind in ('utility_save','utility_report') else 'Disk setting verified in systemd and kernel. B: Undo disk changes.'
            self.modal = dict(stage='result', message=message)
