"""MemoryHigh editor and scrollable apply/restoration previews."""
import curses

from .memory_controls import MemoryHighSession
from .memory_observation import display
from .readability import wrap_lines


def byte_setting(value):
    if isinstance(value,str):return value+' (unverified input)'
    return display(dict(state='unlimited' if value is None else 'known',value=value))


class MemoryControlsView:
    def memory_control_init(self):
        self.memory_session=MemoryHighSession()

    def open_memory_control(self,target):
        if self.replay:
            self.modal=dict(stage='result',message='Replay is read-only; MemoryHigh requires a live service.')
            return
        if not target or not target.get('controllable'):
            self.modal=dict(stage='result',message='Select a controllable service/scope to review MemoryHigh.')
            return
        editor=dict(stage='memory_input',target=dict(target),value='')
        if not self.control:
            self.open_control_mode();self.modal['return_to']=editor
        else:self.modal=editor

    def review_memory_restore(self,event):
        reason=self.restore_reason(event)
        if reason:
            self.modal=dict(stage='result',message=reason);return
        receipt=self.change_links[event['id']][1]
        self.job('memory_restore_prepare',self.memory_session.prepare_restore,receipt['receipt_id'])

    def memory_preview_content(self):
        plan=self.modal['plan'];before=plan['before'];target=plan['target'];restoring=plan.get('restoring',False)
        lines=['REVIEW MEMORYHIGH RESTORATION' if restoring else 'REVIEW MEMORYHIGH',
               'Unit: '+target['unit']+' | manager: '+target['manager'],'Cgroup: '+target['path'],
               'Applies to this entire service and descendants, including other processes.',
               'Current manager MemoryHigh: '+byte_setting(before['memory_high']),
               'Current kernel memory.high: '+byte_setting(before['kernel_high']),
               'Requested runtime MemoryHigh: '+byte_setting(plan['value']),
               'Current group memory.current: '+byte_setting(before['observations']['memory_current']),
               'Current swap: '+('unknown' if before['observations'].get('memory.swap.current') is None else byte_setting(before['observations']['memory.swap.current'])),
               'MemoryHigh causes reclaim/throttling above its boundary; usage may exceed it.']
        if plan['below_usage']:
            lines+=['WARNING: the requested boundary is below current usage. Applying may cause heavy reclaim and allocation stalls.']
        else:lines+=['Usage can change before applying; crossing this boundary requires a fresh preview.']
        if not restoring and plan.get('requested_bytes') is not None:
            lines+=[f"Input: {plan['requested_bytes']} bytes; page size: {before['page_size']} bytes. The preview rounds up to whole pages."]
        if restoring:lines+=['Restoring previous manager value and captured kernel read-back. This does not restore reclaimed cache or lost work.']
        lines += ['Existing MemoryMax: '+byte_setting(before['memory_max'])+'; this workflow changes only MemoryHigh.',
                  'MemoryMax/ancestor limits can still cause OOM; removing local MemoryHigh does not remove them.',
                  'max explicitly writes runtime MemoryHigh=infinity; it does not delete an existing runtime override.',
                  'Existing manager permissions apply. No automatic elevation or broader helper policy.',
                  'Runtime settings remain after exit. Restoration needs this session and unchanged identity/settings.',
                  'Read-back verifies configuration; workload enforcement and latency still require observation.',
                  '', '-- Fresh ancestor memory boundaries --']
        for ancestor in before['ancestors']:
            lines += [ancestor['path']+' (shared with other descendants)']
            for name in ('memory.high','memory.max','memory.swap.max'):lines.append('  '+name+': '+display(ancestor[name]))
        lines += ['Coverage: to configured cgroup root; host ancestry may be hidden.'+
                  ('' if before['ancestor_scan_complete'] else ' Scan stopped at time/depth budget.'),
                  'Enter: restore and verify' if restoring else 'Enter: apply and verify', 'Esc: cancel']
        return lines

    def memory_control_lines(self):
        if self.modal['stage']=='memory_input':
            return ['SET SERVICE MEMORYHIGH', 'Unit: '+self.modal['target']['unit'],
                    'Reclaim/throttling boundary; this is not a hard memory cap.',
                    'Enter positive whole bytes, K/M/G/T (base 1024), or max.',
                    'Example: 512M. Percentages and default/reset are not supported.',
                    'The next screen reads fresh usage, limits and scope before confirmation.',
                    'Value: '+self.modal['value']+'_', 'Enter: preview | Esc: cancel']
        lines=wrap_lines(self.memory_preview_content(),max(20,min(76,self.screen_size[1]-8)))
        capacity=max(1,self.screen_size[0]-8)
        self.modal['scroll']=min(self.modal.get('scroll',0),max(0,len(lines)-capacity))
        offset=self.modal['scroll'];ready=offset+capacity>=len(lines)
        return lines[offset:offset+capacity]+[
            f'{offset+1}-{min(offset+capacity,len(lines))}/{len(lines)} PgDn: review',
            '[ Apply ] Enter: confirm' if ready else 'Scroll to end to enable Enter.', '[ Cancel ] Esc: cancel']

    def memory_control_key(self,key):
        modal=self.modal
        if key==27:self.modal=None
        elif modal['stage']=='memory_input':
            if key in (10,13):self.job('memory_prepare',self.memory_session.prepare,modal['target'],modal['value'])
            elif key in (curses.KEY_BACKSPACE,127,8):modal['value']=modal['value'][:-1]
            elif 32<=key<127 and len(modal['value'])<64:modal['value']+=chr(key)
        elif key in (curses.KEY_DOWN,curses.KEY_UP,curses.KEY_NPAGE,curses.KEY_PPAGE):
            step=max(1,self.screen_size[0]-8) if key in (curses.KEY_NPAGE,curses.KEY_PPAGE) else 1
            modal['scroll']=max(0,modal.get('scroll',0)+(step if key in (curses.KEY_DOWN,curses.KEY_NPAGE) else -step))
        elif key in (10,13):
            lines=wrap_lines(self.memory_preview_content(),max(20,min(76,self.screen_size[1]-8)))
            if modal.get('scroll',0)+max(1,self.screen_size[0]-8)<len(lines):return True
            if self.replay:
                self.modal=dict(stage='result',message='Replay is read-only; resource controls are disabled.')
            elif not self.control:
                self.open_control_mode();self.modal['return_to']=modal
            elif modal['plan'].get('restoring'):self.job('memory_undo',self.memory_session.undo,modal['plan'])
            else:self.job('memory_apply',self.memory_session.apply,modal['plan'])
        return True
