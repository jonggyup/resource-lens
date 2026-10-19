"""Discoverable navigation and contextual shortcuts, independent of controls."""
import curses

ALIASES = ('home overview dashboard', 'cpu processor topology', 'ram memory numa',
           'disk storage io', 'gpu graphics video', 'tasks processes pid',
           'net network interfaces', 'applications apps workloads services containers cgroups', 'timeline events changes comparison', 'status capabilities permissions collector')


def actions(view):
    if view.browser:
        return [('Esc Return',27), ('Left Parent',curses.KEY_LEFT), ('Enter Open',10)]
    if view.editing_filter:
        return [('Enter Done', 10), ('Esc Clear', 27)]
    if view.help:
        return [('Esc Back', 27)]
    if view.placement:
        return [('F1 Help', ord('?')), ('Enter Preview', 10), ('a All', ord('a')),
                ('r Inherit', ord('r')), ('Esc Cancel', 27)]
    common = [('F1 Help', ord('?')), (': Views', ord(':'))]
    local = {
        0: [('e Collapse' if view.overview_expanded else 'e Expand', ord('e'))],
        1: [('g CPU menu', ord('g')), ('u Undo', ord('u')), ('a Related apps', ord('a'))],
        5: [('F4 Filter', ord('/')), ('c CPU sort', ord('c')), ('m RAM sort', ord('m'))],
        7: [('v Browse', ord('v')), ('Enter Inspect', 10), ('F4 Filter', ord('/')), ('p Placement', ord('p')),
            ('w Weight', ord('w')), ('l Quota', ord('l')), ('U Undo place', ord('U'))],
    }.get(view.page, [('Space Freeze', ord(' '))])
    # Quit stays discoverable even when only the first local action fits.
    extra = [('f Focus', ord('f'))] if view.page in (0, 1, 2, 3, 4, 6) else []
    if view.page == 7:
        extra = [('o Overlay', ord('o'))]
    extra += [('0 Status', ord('0')), ('M Mark', ord('M')), ('9 Timeline', ord('9'))]
    return common + [('F10 Quit', ord('q'))] + local + extra


def context_help(view):
    if view.placement:
        title = 'CPU placement'
        details = ['Click a CPU row to toggle it; click a core/socket header to toggle its eligible CPUs.',
                   'Arrows focus a tile; Space toggles selection. PgUp/PgDn scroll.',
                   'a selects all eligible CPUs; n clears selection; r inherits the parent set.',
                   'Enter previews the service change. Esc returns without applying.']
    else:
        title = view.names[view.page]
        details = {
            0: ['e expands/collapses the overview; click its button for the same action.',
                'Click a card or expanded section heading to open its details.'],
            1: ['Right-click a logical CPU or press g for hardware settings.',
                'Socket/core boxes group SMT siblings; positions are not chip coordinates.',
                'u undoes the latest hardware change in this session.'],
            5: ['F4 or / filters processes. Enter finishes; Esc clears the filter.',
                'c/m/n/p sort by CPU, memory, name or PID.'],
            7: ['Up/Down or j/k selects a workload; Enter opens its details; Esc returns.',
                'v / Right opens the actual cgroup hierarchy; T starts at root.',
                'Browser: Enter or click opens a child/PID; Left/Backspace goes up; Esc returns.',
                'F4 or / filters workloads. p opens visual CPU placement.',
                'w sets CPU weight; l sets quota; u undoes weight/quota.',
                'U undoes CPU placement. Controls require --control and permission.'],
        }.get(view.page, ['Scroll to inspect every device, node and metric.',
                          'N/A means unavailable, not idle. Resource panels are read-only.'])
    details += ['1 overview | e expand overview | 8 applications',
                'Applications: [ / ] detail tabs; Enter detail focus; Esc list focus; x clears hardware filter.',
                'CPU/socket tiles: click or right-click opens settings; Shift-click shows allowed applications.',
                'Focused CPU/disk or Memory: a shows related applications (placement/accounting, not inference).','0 capabilities | S save preferences | R export redacted report | N named marker',
                'Workloads: c/m/n/i sort, d device, b disk limit, B Undo disk | Tasks: t tree','f focuses a resource; [ / ] switch devices; f restores all panels.',
                'o on Workloads pins a service overlay; o elsewhere clears it.',
                'M marks a baseline; 9 opens changes and before/after comparisons.',
                'Replay: Left/Right step samples; Space plays/pauses (read-only).']
    return ['HELP / ' + title, 'Esc, F1 or ?: return to the same view', '', *details, '',
            'GLOBAL NAVIGATION', ': opens the searchable view switcher; type cpu, net, services, etc.',
            '1-9 select resource views; 0 opens capabilities; Tab / Shift-Tab switch views; tabs are clickable.',
            'Up/Down or j/k scroll; PgUp/PgDn, Home/End cover longer pages.',
            'Space freezes/unfreezes displayed samples (except in the CPU selector).',
            'F1 or ? opens context help. The bottom action labels are clickable.',
            'F10 or q exits outside editors/dialogs; active settings are acknowledged.',
            '', 'READING METRICS', 'History runs oldest to newest; gaps are missing samples.',
            'CPU process usage: 100% is one logical CPU. Disk active time is not saturation.',
            'Network histories scale to visible peaks. Layered device counters overlap.',
            'Runtime changes remain after exit; Undo history lasts only for this session.']


class NavigationView:
    def navigation_items(self):
        query = self.modal['query'].casefold().strip()
        return [(page, name) for page, name in enumerate(self.names)
                if query in ALIASES[page] or query == str(page + 1)]

    def navigation_lines(self):
        items = self.navigation_items()
        self.modal['selected'] = min(self.modal['selected'], max(0, len(items) - 1))
        count = max(1, self.screen_size[0] - 10)
        start = self.modal['selected'] // count * count
        self.modal['offset'] = start
        return ['Switch view (navigation only)', ': ' + self.modal['query'] + '_',
                *[('> ' if i == self.modal['selected'] else '- ') + f'{page + 1} {name}'
                  for i, (page, name) in enumerate(items) if start <= i < start + count],
                *([] if items else ['No matching views.']), 'Type to filter | Up/Down | Enter opens', 'Esc cancels']

    def navigation_key(self, key):
        if key == 27:
            self.modal = None
        elif key in (10, 13):
            items = self.navigation_items()
            if items:
                self.page = items[self.modal['selected'] % len(items)][0]
                self.focus = None
                self.scroll, self.help, self.modal = 0, False, None
        elif key in (curses.KEY_UP, curses.KEY_DOWN):
            count = len(self.navigation_items())
            if count:
                self.modal['selected'] = (self.modal['selected'] + (1 if key == curses.KEY_DOWN else -1)) % count
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            self.modal['query'] = self.modal['query'][:-1]
            self.modal['selected'] = 0
        elif 32 <= key < 127 and len(self.modal['query']) < 40:
            self.modal['query'] += chr(key)
            self.modal['selected'] = 0
        return True
