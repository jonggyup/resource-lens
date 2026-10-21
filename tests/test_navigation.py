import curses
import unittest
from unittest.mock import patch
from resourcelens.ui import View, draw
from resourcelens.navigation import actions, context_help
from resourcelens.collect import Collector


class Screen:
    def __init__(self, height=24, width=80):
        self.height, self.width = height, width
        self.lines = {}
    def getmaxyx(self): return self.height, self.width
    def erase(self): self.lines = {}
    def refresh(self): pass
    def addnstr(self, y, x, text, length, attr=0):
        written = text[:length]
        self.lines[(y, x)] = written + self.lines.get((y, x), "")[len(written):]


class NavigationTests(unittest.TestCase):
    def test_visible_shortcuts_do_not_require_function_keys(self):
        view = View()
        for page in range(10):
            view.page = page
            for label, key in actions(view):
                self.assertNotRegex(label, r'\bF\d+\b')
                self.assertNotIn(key, (curses.KEY_F1, curses.KEY_F2, curses.KEY_F4, curses.KEY_F10))

    def test_help_wraps_and_prioritizes_current_application_mode(self):
        view = View()
        view.page = 7
        for width in (20, 37, 60, 77, 117):
            lines = context_help(view, width)
            self.assertTrue(all(len(line) <= width for line in lines))
            self.assertLess(lines.index('-- Processes --'), lines.index('-- Move around --'))
            self.assertNotIn('-- Tune a service --', lines)
        view.application_mode = 'cgroups'
        lines = context_help(view, 77)
        self.assertIn('-- Tune a service --', lines)
        self.assertNotIn('-- Processes --', lines)
        view.placement = {'selected': set()}
        self.assertIn('-- Choose CPUs --', context_help(view, 77))

    def test_palette_matches_aliases_and_cancels(self):
        view = View()
        for query, page in [('services', 7), ('ram', 2), ('net', 6), ('4', 3)]:
            view.key(ord(':'))
            for char in query:
                view.key(ord(char))
            view.key(10)
            self.assertEqual(view.page, page)
        view.key(ord(':'))
        view.key(ord('z'))
        view.key(10)
        self.assertEqual(view.modal['stage'], 'navigate')
        view.key(27)
        self.assertIsNone(view.modal)

    def test_palette_typing_does_not_execute_actions(self):
        view = View(control=True)
        view.key(ord(':'))
        for char in 'qwpj':
            self.assertTrue(view.key(ord(char)))
        self.assertFalse(view.session.events)
        self.assertEqual(view.modal['query'], 'qwpj')
        self.assertEqual(view.page, 0)

    def test_context_help_restores_scroll_and_blocks_controls(self):
        view = View(control=True)
        view.page, view.scroll = 1, 9
        view.key(curses.KEY_F1)
        self.assertTrue(view.help)
        self.assertIn('HELP / CPU', '\n'.join(view.lines(77)))
        view.key(ord('g'))
        self.assertIsNone(view.modal)
        view.key(27)
        self.assertFalse(view.help)
        self.assertEqual(view.scroll, 9)

    def test_vim_navigation_and_filter_text_are_separate(self):
        view = View()
        view.key(ord('j'))
        self.assertEqual(view.scroll, 1)
        view.key(ord('k'))
        self.assertEqual(view.scroll, 0)
        view.key(curses.KEY_F4)
        view.key(ord('j'))
        view.key(ord('k'))
        self.assertEqual(view.filter, 'jk')

    def test_clickable_footer_quit_propagates(self):
        view = View()
        view.action_regions = [(1, 10, ord('q'))]
        with patch('curses.getmouse', return_value=(0, 3, 23, 0, curses.BUTTON1_PRESSED)):
            self.assertFalse(view.key(curses.KEY_MOUSE))

    def test_filter_footer_can_complete_editing(self):
        view = View()
        view.key(ord('/'))
        view.action_regions = [(1, 13, 10)]
        with patch('curses.getmouse', return_value=(0, 3, 23, 0, curses.BUTTON1_PRESSED)):
            view.key(curses.KEY_MOUSE)
        self.assertFalse(view.editing_filter)

    def test_actions_fit_and_rendered_palette_rows_are_clickable(self):
        view = View()
        view.update(Collector(nvidia=False).sample())
        for width in (40, 80, 120):
            screen = Screen(24, width)
            draw(screen, view, 1)
            self.assertTrue(view.action_regions)
            self.assertTrue(all(1 <= start < end <= width - 1 for start, end, _ in view.action_regions))
            self.assertIn('[Read-only C]', ' '.join(screen.lines.values()))
        view.key(ord(':'))
        draw(Screen(24, 80), view, 1)
        row, (start, end, index) = next(iter(view.modal_regions.items()))
        view.mouse(start + 1, row, curses.BUTTON1_PRESSED)
        self.assertIsNone(view.modal)
        self.assertEqual(view.page, index)

    def test_quit_shortcut_still_requires_active_changes_acknowledgement(self):
        view = View(control=True)
        view.hardware.events.append({'status': 'confirmed'})
        self.assertTrue(view.key(curses.KEY_F10))
        self.assertEqual(view.modal['stage'], 'quit')
