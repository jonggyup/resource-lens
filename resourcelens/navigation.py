"""Discoverable navigation and contextual shortcuts, independent of controls."""
import curses
import textwrap

ALIASES = ('home overview dashboard', 'cpu processor topology', 'ram memory numa',
           'disk storage io', 'gpu graphics video', 'tasks processes pid',
           'net network interfaces', 'applications apps workloads services containers cgroups', 'timeline events changes comparison', 'status capabilities permissions collector')


def actions(view):
    if view.profile_view:
        profile=view.profile_view
        if profile.choosing:
            return [('1 CPU usage',ord('1')),('2 CPU + waits',ord('2')),('3 Energy',ord('3')),('Enter Start',10),('Esc Back',27)]
        if profile.result is not None or profile.error is not None:
            return [('D Summary' if profile.details else 'D Details',ord('D')),('Esc Back',27)]
        return [('Esc Cancel',27)]
    if view.memory_sharing:
        return [('Esc Back',27), ('Tab Next',9), ('r Refresh',ord('r'))]
    if view.browser:
        return [('Esc Back',27), ('Left Parent',curses.KEY_LEFT), ('Enter Open',10), ('V Memory',ord('V')),('P Investigate',ord('P')),('W CPU+waits',ord('W')), ('q Applications',ord('q'))]
    if view.page == 7 and view.application_mode == 'processes' and not view.help and not view.editing_filter:
        return [('? Help',ord('?')),('q Quit',ord('q')),('g Cgroups',ord('g')),('Enter Open',10),('V Memory',ord('V')),('P Investigate',ord('P')),('W CPU+waits',ord('W')),('c CPU',ord('c')),('m Memory',ord('m')),('/ Search',ord('/'))]
    if view.editing_filter:
        return [('Enter Done', 10), ('Esc Clear', 27)]
    if view.help:
        return [('Esc Back', 27)]
    if view.placement:
        return [('? Help', ord('?')), ('Enter Preview', 10), ('a All', ord('a')),
                ('r Inherit', ord('r')), ('Esc Cancel', 27)]
    common = [('? Help', ord('?')), (': Views', ord(':'))]
    local = {
        0: [('e Collapse' if view.overview_expanded else 'e Expand', ord('e'))],
        1: [('g CPU menu', ord('g')), ('u Undo', ord('u')), ('a Related apps', ord('a'))],
        5: [('/ Filter', ord('/')), ('c CPU sort', ord('c')), ('m RAM sort', ord('m'))],
        7: [('g Processes', ord('g')), ('Enter Open', 10), ('D Details', ord('D')), ('/ Filter', ord('/')), ('p Placement', ord('p')),
            ('w Weight', ord('w')), ('l Quota', ord('l')), ('U Undo place', ord('U')), ('P Investigate',ord('P')),('W CPU+waits',ord('W'))],
    }.get(view.page, [('Space Freeze', ord(' '))])
    # Quit stays discoverable even when only the first local action fits.
    extra = [('f Focus', ord('f'))] if view.page in (0, 1, 2, 3, 4, 6) else []
    if view.page == 7:
        extra = [('o Overlay', ord('o'))]
    extra += [('0 Status', ord('0')), ('M Mark', ord('M')), ('9 Timeline', ord('9'))]
    return common + [('q Quit', ord('q'))] + local + extra


def context_help(view, width=80):
    """A contextual shortcut sheet with aligned keys and hanging descriptions."""
    title = 'CPU placement' if view.placement else view.names[view.page]
    sections = []
    if view.placement:
        sections.append(('Choose CPUs', [
            ('Click', 'Select a CPU. Click a core or socket to select its eligible CPUs.'),
            ('Arrows / Space', 'Move between CPU tiles / toggle the selected tile.'),
            ('a / n', 'Select all eligible CPUs / clear the selection.'),
            ('r', 'Use the CPU set inherited from the parent group.'),
            ('Enter', 'Preview the change before applying it.'),
            ('Esc', 'Leave the editor without applying changes.')]))
    elif view.page == 7:
        if view.application_mode == 'processes':
            sections.append(('Processes', [
                ('Enter', 'Open details for the selected process.'),
                ('c / m / n / p', 'Sort by CPU usage / memory / name / process ID.'),
                ('/', 'Filter the process list. Enter finishes; Esc clears.'),
                ('g', 'Switch to cgroups: services and groups of processes.')]))
        else:
            sections.append(('Services and cgroups', [
                ('Enter / Right', 'Open the selected group to see its children and processes.'),
                ('c / m / n / i', 'Sort by CPU usage / memory / name / disk I/O.'),
                ('/', 'Filter the group list.'),
                ('g', 'Switch to individual processes.'),
                ('T', 'Browse the cgroup hierarchy from its root.'),
                ('[ / ]', 'Switch detail tabs. D focuses details; Esc returns to the list.'),
                ('x', 'Clear the hardware filter.')]))
            sections.append(('Inside a group', [
                ('Enter / Click', 'Open a child group or process.'),
                ('Left / Backspace', 'Go to the parent group. Esc also goes up one level.'),
                ('c / m / n', 'Sort children and direct processes by CPU / memory / name.'),
                ('q', 'Return to Applications.')]))
            sections.append(('Tune a service', [
                ('p / U', 'Choose its CPUs / undo the last CPU placement change.'),
                ('w / l / u', 'Set CPU weight / set CPU quota / undo the last change.'),
                ('b / B', 'Limit disk bandwidth / undo the last disk limit.'),
                ('d', 'Open the disk used by the selected service.'),
                ('o', 'Pin this service on resource views.')]))
        sections.append(('Investigate the selection', [
            ('P', 'Choose CPU usage, CPU + waits, or Energy efficiency.'),
            ('W', 'Start CPU + waits directly: where the selected work spends time waiting.'),
            ('V', 'Inspect memory allocation, shared address spaces and mapped files.'),
            ('', 'Captures run for 10 seconds. Esc cancels; D switches result details.'),
            ('', 'Energy is measured for hardware domains, not attributed to a process.')]))
    else:
        rows = {
            0: [('e', 'Expand the overview to see hardware topology; press again to collapse.'),
                ('Click', 'Open a resource card or an expanded section for details.')],
            1: [('g / Right-click', 'Open CPU settings for all CPUs or the clicked CPU.'),
                ('u', 'Undo the last CPU hardware change.'),
                ('Shift-click', 'Show applications allowed to run on this CPU.'),
                ('', 'Boxes group sockets, cores and hardware threads. They are not physical chip coordinates.')],
            5: [('/', 'Filter processes. Enter finishes; Esc clears the filter.'),
                ('c / m / n / p', 'Sort by CPU usage / memory / name / process ID.'),
                ('t', 'Switch between a process list and a parent/child tree.')],
            8: [('M', 'Mark a baseline for before/after comparisons.'),
                ('N', 'Add a named marker to the timeline.')],
            9: [('', 'Check which sensors, permissions and system features are available.')],
        }.get(view.page, [('', 'Scroll to see every device, node and metric.')])
        sections.append(('On this view', rows))
    if not view.placement and view.page in (0, 1, 2, 3, 4, 6):
        sections.append(('Explore resources', [
            ('f', 'Focus one resource; press again to restore all resources.'),
            ('[ / ]', 'Switch devices in the focused view.'),
            ('a', 'On a focused CPU or disk, or Memory, show related applications.'),
            ('', 'Relationships come from allowed CPU sets and accounting, not guessed ownership.')]))
    sections.extend([
        ('Move around', [
            (':', 'Find a view by name, such as cpu, net or services.'),
            ('1 / 8 / 0', 'Overview / Applications / system capabilities.'),
            ('1-9 / Tab', 'Open a numbered view / move to the next view. Shift-Tab goes back.'),
            ('Up / Down', 'Scroll or select a row. j / k also work.'),
            ('PgUp / PgDn', 'Scroll one page. Home / End jump to the first / last line.'),
            ('Space', 'Pause live display updates. In the CPU editor, toggle a CPU.'),
            ('? / Esc', 'Close this help and return to your previous position.'),
            ('q', 'Quit from a main view. Editors and dialogs have their own Back action.')]),
        ('Controls and saved work', [
            ('C (Shift+C)', 'Enable or disable control menus. You can also click the mode indicator.'),
            ('', 'Enabling menus changes no settings. Each change needs confirmation and permission.'),
            ('', 'Settings remain after exit. Undo history is available only in this session.'),
            ('S / R', 'Save display preferences / export a redacted report.'),
            ('M / N / 9', 'Mark a baseline / add a named marker / open the timeline.'),
            ('Left / Right', 'In replay, step through samples. Space plays or pauses. Replay is read-only.')]),
        ('Understand the numbers', [
            ('N/A', 'Unavailable measurement, not zero usage.'),
            ('CPU usage', 'For a process, 100% means one logical CPU is fully busy.'),
            ('History', 'Oldest samples are on the left; newest on the right. Gaps mean missing data.'),
            ('Disk activity', 'Active time alone does not tell you whether a disk is saturated.'),
            ('Network', 'Graphs scale to their visible peaks. Counters on layered devices may overlap.')])])
    width = max(20, width)
    key_width = 20 if width >= 60 else 0
    lines = ['HELP / ' + title, 'Shortcuts for this view, then general reference.',
             'Scroll: arrows or mouse wheel    Back: Esc or ?',
             'Uppercase shortcuts use Shift. Bottom buttons are clickable.']
    result = []
    for line in lines:
        result.extend(textwrap.wrap(line, width))
    for heading, rows in sections:
        result.append('')
        result.extend(textwrap.wrap('-- ' + heading + ' --', width))
        for key, description in rows:
            if key_width:
                prefix = ('  ' + key).ljust(key_width) if key else ' ' * key_width
                result.extend(textwrap.wrap(description, width, initial_indent=prefix,
                                            subsequent_indent=' ' * key_width))
            else:
                if key:
                    result.extend(textwrap.wrap('  ' + key, width))
                result.extend(textwrap.wrap(description, width, initial_indent='    ', subsequent_indent='    '))
    return result

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
