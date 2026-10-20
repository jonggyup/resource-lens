import curses
import unittest
from unittest.mock import patch
from resourcelens.ui import View, draw
from test_navigation import Screen


class ControlModeTests(unittest.TestCase):
    def test_toggle_preserves_sessions_and_warns_on_exit(self):
        view = View()
        sessions = (view.session, view.hardware, view.placement_session, view.disk_session)
        with patch.object(view.hardware, 'snapshot', side_effect=AssertionError('no hardware reads')):
            view.key(ord('C'))
            self.assertFalse(view.control)
            view.key(10)
            self.assertTrue(view.control)
            view.session.events.append({'status': 'confirmed'})
            view.key(ord('C'))
            view.key(10)
        self.assertFalse(view.control)
        self.assertEqual(sessions, (view.session, view.hardware, view.placement_session, view.disk_session))
        self.assertTrue(view.key(ord('q')))
        self.assertEqual(view.modal['stage'], 'quit')

    def test_replay_cannot_enable(self):
        view = View()
        view.replay = object()
        view.key(ord('C'))
        self.assertEqual(view.modal['stage'], 'result')
        view.key(10)
        self.assertFalse(view.control)
        view.modal = {'stage': 'control_mode'}
        view.key(10)
        self.assertFalse(view.control)

    def test_mouse_toggle_and_cancel(self):
        view = View()
        screen = Screen(height=30, width=100)
        draw(screen, view, 1)
        view.mouse(2, 28, curses.BUTTON1_CLICKED)
        self.assertEqual(view.modal['stage'], 'control_mode')
        draw(screen, view, 1)
        row, (left, _, _) = next((y, r) for y, r in view.modal_regions.items() if r[2] == 'toggle')
        view.mouse(left + 2, row, curses.BUTTON1_CLICKED)
        self.assertTrue(view.control)
        view.key(ord('C'))
        draw(screen, view, 1)
        row, (left, _, _) = next((y, r) for y, r in view.modal_regions.items() if r[2] == 'cancel')
        view.mouse(left + 2, row, curses.BUTTON1_CLICKED)
        self.assertTrue(view.control)
        self.assertIsNone(view.modal)

    def test_disabled_entry_points_offer_enablement(self):
        for action in (lambda v: v.hardware_menu(), lambda v: v.open_placement(),
                       lambda v: v.utility_key(ord('b'))):
            view = View()
            view.page = 7
            action(view)
            self.assertEqual(view.modal['stage'], 'control_mode')
            view.key(27)
            self.assertFalse(view.control)

    def test_pending_dialog_and_editor_are_not_interrupted(self):
        for stage in ('busy', 'confirm', 'input'):
            view = View()
            view.modal = {'stage': stage, 'value': ''}
            view.key(ord('C'))
            self.assertEqual(view.modal['stage'], stage)
            self.assertFalse(view.control)
        view = View()
        view.editing_filter = True
        view.key(ord('C'))
        self.assertIsNone(view.modal)
        view.editing_filter = False
        view.placement = {'selected': set()}
        view.key(ord('C'))
        self.assertIsNone(view.modal)
