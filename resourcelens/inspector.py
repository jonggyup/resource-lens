"""Snapshot-only target inspector state, rendering and local input."""

import curses
import time

from .diagnostics import context_for, explain, request_current
from .model import TargetRef
from .readability import wrap_lines, draw_text


TABS = ('Summary', 'Usage', 'Limits', 'Evidence', 'Actions')
PROCESS_STATES = {'R': 'Running or ready for CPU', 'S': 'Sleeping', 'D': 'Waiting (often disk I/O)',
                  'T': 'Stopped', 't': 'Stopped by a debugger', 'Z': 'Exited; waiting for its parent',
                  'I': 'Idle kernel thread', 'X': 'Exited'}


class Inspector:
    def __init__(self, request, tab=0):
        self.request = tuple(request)
        self.tab, self.scroll, self.finding_index, self.action_index = tab, 0, 0, 0
        self.trail = []
        self.scope_id = None
        self.group_identity = None
        self.findings = []
        self.tab_regions, self.action_regions, self.finding_regions = [], [], []
        self.help = False

    def signature(self):
        return (self.request, self.tab, self.scroll, self.finding_index, self.action_index, self.help, len(self.trail))

    def select_tab(self, name):
        self.tab, self.scroll, self.help = TABS.index(name), 0, False

    def navigate(self, request):
        if tuple(request) == self.request:
            return
        self.trail.append((self.request, self.tab, self.scroll, self.finding_index,
                           self.action_index, self.scope_id, self.group_identity))
        self.trail = self.trail[-32:]
        self.request, self.tab, self.scroll = tuple(request), 0, 0
        self.finding_index = self.action_index = 0
        self.scope_id = self.group_identity = None

    def back(self):
        if self.help:
            self.help = False
            return True
        if not self.trail:
            return False
        (self.request, self.tab, self.scroll, self.finding_index,
         self.action_index, self.scope_id, self.group_identity) = self.trail.pop()
        return True

    def invalid_reason(self, sample):
        scope = sample.get('collection_scope', {}).get('id')
        if self.scope_id is not None and scope != self.scope_id:
            return 'The collection scope changed; return and select this target again.'
        if not request_current(sample, self.request):
            return 'Process exited, moved, or PID was reused; the group may have been replaced. Return and reselect.'
        context = context_for(sample, self.request)
        if context and self.group_identity is not None:
            try:
                if TargetRef.from_dict(context['selected_group']) != self.group_identity:
                    return 'The cgroup lifetime changed; return and select this target again.'
            except (KeyError, TypeError, ValueError):
                return 'The recorded cgroup identity is invalid; return and reselect.'
        return None

    def refresh(self, view):
        sample = view.snapshot
        if self.scope_id is None:
            self.scope_id = sample.get('collection_scope', {}).get('id')
        context = context_for(sample, self.request)
        if self.group_identity is None and self.request[0] == 'cgroup' and context:
            try:
                self.group_identity = TargetRef.from_dict(context['selected_group'])
            except (KeyError, TypeError, ValueError):
                pass
        reason = self.invalid_reason(sample)
        self.findings = [] if reason else explain(view.diagnostic_history, sample, self.request,
            now=sample['timestamp'] if view.replay or view.paused else time.time())
        self.finding_index = min(self.finding_index, max(0, len(self.findings) - 1))
        return reason

    def selected_finding(self):
        return self.findings[min(self.finding_index, len(self.findings)-1)] if self.findings else None

    def key(self, key, height):
        if key in (27, curses.KEY_LEFT, curses.KEY_BACKSPACE, 127, 8):
            return 'handled' if self.back() else 'close'
        if key in (ord('?'), curses.KEY_F1):
            self.help, self.scroll = not self.help, 0
            return 'handled'
        if self.help:
            if key in (curses.KEY_DOWN, curses.KEY_NPAGE, ord('j')):
                self.scroll += max(1,height-8) if key == curses.KEY_NPAGE else 1
            elif key in (curses.KEY_UP, curses.KEY_PPAGE, ord('k')):
                self.scroll = max(0,self.scroll-(max(1,height-8) if key == curses.KEY_PPAGE else 1))
            elif key in (curses.KEY_HOME,curses.KEY_END):
                self.scroll = 0 if key == curses.KEY_HOME else 10**9
            return 'handled'
        if key in (9, curses.KEY_BTAB, ord('['), ord(']')):
            self.tab = (self.tab + (-1 if key in (curses.KEY_BTAB, ord('[')) else 1)) % len(TABS)
            self.scroll, self.help = 0, False
            return 'handled'
        if ord('1') <= key <= ord('5'):
            self.tab, self.scroll, self.help = key-ord('1'), 0, False
            return 'handled'
        if key in (ord('e'), ord('D')):
            self.select_tab('Evidence'); return 'handled'
        if key == ord('c'):
            self.select_tab('Limits'); return 'handled'
        if self.tab == 4 and not self.help and key in (curses.KEY_DOWN, curses.KEY_UP, ord('j'), ord('k'), 10, 13):
            from .actions import ACTIONS
            if key in (10, 13):
                return ACTIONS[self.action_index].id
            self.action_index = (self.action_index + (-1 if key in (curses.KEY_UP, ord('k')) else 1)) % len(ACTIONS)
            self.scroll = 0
            return 'handled'
        if self.tab == 3 and key in (ord('n'), ord('p')) and self.findings:
            self.finding_index = (self.finding_index + (1 if key == ord('n') else -1)) % len(self.findings)
            self.scroll = 0
            return 'handled'
        moves = {curses.KEY_DOWN: 1, ord('j'): 1, curses.KEY_UP: -1, ord('k'): -1,
                 curses.KEY_NPAGE: max(1, height-8), curses.KEY_PPAGE: -max(1, height-8)}
        if key in moves:
            self.scroll = max(0, self.scroll + moves[key]); return 'handled'
        if key in (curses.KEY_HOME, curses.KEY_END):
            self.scroll = 0 if key == curses.KEY_HOME else 10**9
            return 'handled'
        return {ord('m'): 'group_memory',ord('g'): 'open_group', ord('o'): 'open_service', ord('a'): 'open_ancestor',
                ord('r'): 'review_controls', ord('J'): 'show_changes'}.get(key)

    def lines(self, view):
        from .ui import pct, size
        from .actions import ACTIONS, availability
        sample, request = view.snapshot, self.request
        reason = self.refresh(view)
        if self.help:
            return ['INSPECTOR HELP', 'Tab / [ / ]: sections | 1-5: direct section | arrows/wheel: scroll',
                    'Evidence: n/p selects a finding. Actions: arrows select, Enter opens.',
                    *[a.key + ': ' + a.label + ' - ' + a.help for a in ACTIONS],
                    'Esc/Back returns one level; capture and memory views return here.',
                    'Recorded data never enables live capture or controls. Review opens editors; apply requires a separate preview confirmation.']
        if reason:
            return [reason, 'Esc returns to the previous selection.']
        context = context_for(sample, request)
        process = next((p for p in sample.get('processes', []) if request[0] == 'process' and
                        (p['pid'], p.get('starttime')) == request[1:3]), None)
        if self.tab == 4:
            return ['ACTIONS - arrows select; Enter opens', *[
                ('> ' if i == self.action_index else '  ') + a.key + ' ' + a.label +
                (' - unavailable: ' + why if (why := availability(view, a, request)) else '')
                for i, a in enumerate(ACTIONS)]]
        if self.tab == 3:
            selected = self.selected_finding()
            if selected is None:
                return ['No usable finding evidence. Return to Summary for coverage.']
            lines = [f'{self.finding_index+1}/{len(self.findings)} findings',
                     selected.title, selected.explanation, 'Evidence strength: ' + selected.relevance,
                     f'Rule: {selected.rule_id} / version {selected.rule_version}', '']
            for evidence in selected.evidence:
                lines += ['Source: ' + evidence['path'] + '/' + evidence['file']]
                if 'delta' in evidence:
                    lines += [f"{evidence.get('counter','throttled_usec')}: {evidence['before']} -> {evidence['after']} (delta {evidence['delta']})",
                              f"Interval: {evidence['interval_start']:.6f}-{evidence['interval_end']:.6f} monotonic; {evidence['interval_s']:.3f}s",
                              'Collection scope: ' + evidence['scope']]
                else:
                    lines += ['Recorded value: ' + str(evidence.get('value'))]
                    if 'interval_s' in evidence:lines += [f"Interval: {evidence['interval_s']:.3f}s; scope {evidence.get('scope','unknown')}"]
            if selected.window:
                lines += [f"Covered: {selected.window['covered_seconds']:.2f}s / {selected.window['intervals']} intervals"]
                if 'throttled_ms_per_second' in selected.window:
                    lines += [f"Throttle counter rate: {selected.window['throttled_ms_per_second']:.2f} ms/s"]
                if 'mean_psi_pct' in selected.window:lines += [f"Mean PSI some.avg10: {selected.window['mean_psi_pct']:.2f}%"]
                if 'events_delta' in selected.window:lines += [f"Counter increase: {selected.window['events_delta']} events"]
            return lines + ['', '-- Interpretation limits --', *selected.limitations,
                            'c Limits | W CPU + waits | a Limiting parent']
        if self.tab == 2:
            from .targets import context_lines
            if context:
                return context_lines(dict(sample, target_context=context), request)
            return ['Limits are unavailable for this group. Unknown limits may still apply.']
        if self.tab == 1:
            lines = ['USAGE - each measurement names its scope']
            if process:
                lines += [f"Process CPU: {pct(process.get('cpu_pct'))} (100% = one logical CPU)",
                          f"Process RSS: {size(process.get('memory'))}",
                          'RSS includes shared mappings; V inspects allocation/sharing.']
            if context:
                for node in context.get('ancestors', []):
                    stats = node.get('counters', {}).get('cpu.stat') or {}
                    lines += ['', 'Group: ' + node['path'] + ' (includes other members and descendants)',
                              'Cumulative CPU usage: ' + str(stats.get('usage_usec', 'unknown')) + ' us',
                              'Cumulative quota throttling: ' + str(stats.get('throttled_usec', 'unknown')) + ' us']
            from .memory_observation import memory_lines
            return lines + ['',*memory_lines(context,now=sample['timestamp'] if view.replay or view.paused else time.time()),
                            'Group usage and process usage are different scopes; do not add parent/child totals.']
        lines = ['SUMMARY']
        if process:
            lines += [f"Process CPU: {pct(process.get('cpu_pct'))} | RSS: {size(process.get('memory'))}",
                      '100% means one logical CPU; RSS may include shared mappings.']
        lines += ['']
        for index, item in enumerate(self.findings):
            lines += [f'{index+1}. {item.title}', item.explanation]
        if context:
            lines += ['', 'Exact group: ' + context.get('exact_cgroup', 'unknown'),
                      'Service association: ' + (context.get('service', {}).get('unit') or 'none observed'),
                      'Coverage: ' + context.get('ancestry_visibility', 'unknown')]
        if process:
            lines += ['', '-- Process identity --', f"PID {process['pid']} | parent {process.get('ppid')} | start ticks {process.get('starttime')}",
                      'State: ' + PROCESS_STATES.get(process.get('state'), 'Unknown') + ' (' + str(process.get('state') or '?') + ')',
                      'Cgroup: ' + (process.get('cgroup') or 'unknown')]
        investigation = getattr(view, 'investigation', None)
        if investigation:
            paths = {n['path'] for n in (context or {}).get('ancestors', [])}
            events = [e for e in investigation.events if e.get('domain') and
                      (not e.get('target') or e['target'].get('id') in paths)]
            if events:
                event = events[-1]
                lines += ['', '-- Latest configuration event --', event['label']+' ['+event['status']+']',
                          *investigation.observation_lines(event), 'J: full Changes history and restoration']
        return lines

    def draw(self, screen, view, width, height):
        from .ui import put
        sample = view.snapshot
        request = self.request
        process = next((p for p in sample.get('processes', []) if request[0] == 'process' and
                        (p['pid'], p.get('starttime')) == request[1:3]), None)
        label = f"PID {request[1]}: {process['name']}" if process else 'Cgroup ' + str(request[1]) if request[0] == 'cgroup' else 'PID '+str(request[1])
        put(screen, 2, 1, '[ < Back ]', curses.A_BOLD)
        put(screen, 3, 1, 'INSPECT / ' + label, curses.A_BOLD)
        self.tab_regions, self.action_regions, self.finding_regions = [], [], []
        if width >= 70:
            x = 1
            for i, tab in enumerate(TABS):
                text = f' {i+1} {tab} '
                put(screen, 4, x, text, curses.A_REVERSE if i == self.tab else curses.A_BOLD)
                self.tab_regions.append((x, x+len(text), 4, i)); x += len(text)
        else:
            put(screen, 4, 1, f'{self.tab+1}/5 {TABS[self.tab]} | Tab: next | ?: help', curses.A_BOLD)
        lines = wrap_lines(self.lines(view), width-3)
        capacity = max(1, height-8)
        if self.tab == 4 and not self.help:
            selected_line = next((i for i, line in enumerate(lines) if line.startswith('> ')), 0)
            self.scroll = min(self.scroll, selected_line) if selected_line < self.scroll else max(self.scroll, selected_line-capacity+1)
        self.scroll = min(self.scroll, max(0, len(lines)-capacity))
        for i, line in enumerate(lines[self.scroll:self.scroll+capacity]):
            draw_text(screen, 5+i, 1, line, limit=width-3)
            if self.tab == 0 and not self.help:
                number, separator, _ = line.partition('. ')
                if separator and number.isdigit() and 1 <= int(number) <= len(self.findings):
                    self.finding_regions.append((1, width-1, 5+i, 'select', int(number)-1))
        if self.tab == 3 and not self.help and self.scroll == 0:
            put(screen, 5, 15, '[p Prev]', curses.A_BOLD)
            put(screen, 5, 24, '[n Next]', curses.A_BOLD)
            self.finding_regions += [(15, 23, 5, 'step', -1), (24, 32, 5, 'step', 1)]
        if self.tab == 4 and not self.help:
            # Map whole wrapped action blocks, including disabled reasons.
            from .actions import ACTIONS, availability
            row = len(wrap_lines(['ACTIONS - arrows select; Enter opens'], width-3))
            for i, action in enumerate(ACTIONS):
                why = availability(view, action, request)
                text = ('> ' if i == self.action_index else '  ') + action.key + ' ' + action.label + (' - unavailable: '+why if why else '')
                count = len(wrap_lines([text], width-3))
                for n in range(count):
                    y = 5 + row + n - self.scroll
                    if 5 <= y < 5 + capacity:
                        self.action_regions.append((1, width-1, y, action.id))
                row += count
        if height >= 12 and not self.help:
            x = 1
            for label, action in (('[P Investigate]', 'profile'), ('[V Memory]', 'memory'), ('[e Evidence]', 'show_evidence'),
                                  ('[r Controls]', 'review_controls'), ('[J Changes]', 'show_changes')):
                if x+len(label) >= width:
                    break
                put(screen, height-3, x, label, curses.A_BOLD)
                self.action_regions.append((x, x+len(label), height-3, action))
                x += len(label)+1

    def mouse(self, x, y, state, height):
        if state & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED):
            for left, right, row, tab in self.tab_regions:
                if y == row and left <= x < right:
                    self.tab, self.scroll, self.help = tab, 0, False
                    return 'handled'
            for left, right, row, kind, value in self.finding_regions:
                if y == row and left <= x < right and self.findings:
                    self.finding_index = (self.finding_index+value)%len(self.findings) if kind == 'step' else value
                    self.tab, self.scroll = 3, 0
                    return 'handled'
            for left, right, row, action in self.action_regions:
                if y == row and left <= x < right:
                    return action
        for button, key in ((curses.BUTTON4_PRESSED, curses.KEY_UP),
                            (getattr(curses, 'BUTTON5_PRESSED', 0), curses.KEY_DOWN)):
            if state & button:
                for _ in range(3): self.key(key, height)
                return 'handled'
        return None
