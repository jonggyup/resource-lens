"""Regression coverage for controls coexisting with workspace navigation."""
import curses
import unittest
from unittest.mock import patch
from resourcelens.ui import View, draw
from resourcelens.collect import Collector
from test_navigation import Screen
from test_workspaces import group


class InteractionRoutingTests(unittest.TestCase):
    def setUp(self):
        self.view = View(control=True)
        sample = Collector(nvidia=False).sample()
        sample['workloads'] = [dict(group('demo.service'), controllable=True)]
        self.view.update(sample)
        self.view.screen_size = (40, 120)

    def test_plain_cpu_and_socket_clicks_keep_original_control_menu(self):
        view = self.view
        view.page = 1
        cpu = view.snapshot['cpu_hardware']['cpus'][0]
        for kind, scope in [('cpu', ('id', cpu['id'])), ('socket', ('socket', cpu['socket']))]:
            view.topology_regions = {12: [(0, 20, kind, (cpu['id'],))]}
            for button in (curses.BUTTON1_PRESSED, curses.BUTTON1_CLICKED,
                           curses.BUTTON3_PRESSED, curses.BUTTON3_CLICKED):
                with self.subTest(kind=kind, button=button), patch.object(view, 'hardware_menu') as menu:
                    view.mouse(2, 12, button)
                    menu.assert_called_once_with(scope)
                    self.assertEqual(view.page, 1)
                    self.assertIsNone(view.relation)

    def test_shift_click_relationship_does_not_open_controls(self):
        view = self.view
        view.page = 1
        view.topology_regions = {12: [(0, 20, 'core', (0, 2))]}
        with patch.object(view, 'hardware_menu') as menu:
            view.mouse(2, 12, curses.BUTTON1_CLICKED | curses.BUTTON_SHIFT)
            menu.assert_not_called()
        self.assertEqual(view.page, 7)
        self.assertEqual(view.relation, ('cpu', (0, 2)))

    def test_placement_blocks_workspace_clicks_and_preserves_tile_toggle(self):
        view = self.view
        view.placement_ready(dict(target=group('demo.service'), info=dict(
            effective=[0, 1], eligible=[0, 1], allowed=[0, 1])))
        view.workspace_regions = [(1, 15, 2, ord('H')), (16, 32, 2, ord('A'))]
        for x in (2, 17):
            view.mouse(x, 2, curses.BUTTON1_CLICKED)
            self.assertEqual(view.page, 1)
            self.assertIsNotNone(view.placement)
        for key in 'HA':
            view.key(ord(key))
            self.assertEqual(view.page, 1)
        view.topology_regions = {12: [(0, 20, 'core', (0, 1))]}
        view.mouse(2, 12, curses.BUTTON1_CLICKED)
        self.assertEqual(view.placement['selected'], set())
        view.key(ord('a'))
        self.assertEqual(view.placement['selected'], {0, 1})
        draw(Screen(40, 120), view, 1)
        self.assertEqual(view.workspace_regions, [])
        view.key(27)
        self.assertIsNone(view.placement)
        self.assertEqual(view.page, 7)

    def test_wide_application_wheel_scrolls_hovered_pane(self):
        view = self.view
        view.snapshot['workloads'] = [group('service'+str(i)) for i in range(12)]
        view.page = 7
        view.app_tab = 3
        screen = Screen(24, 120)
        draw(screen, view, 1)
        selected = view.workload_id
        view.mouse(3, 10, curses.BUTTON5_PRESSED)
        self.assertNotEqual(view.workload_id, selected)
        self.assertFalse(view.workload_detail)
        draw(screen, view, 1)
        view.mouse(70, 10, curses.BUTTON5_PRESSED)
        self.assertTrue(view.workload_detail)
        self.assertGreater(view.scroll, 0)
        draw(screen, view, 1)
        self.assertGreater(view.scroll, 0)
        view.key(27)
        self.assertFalse(view.workload_detail)
        view.mouse(70, 10, curses.BUTTON1_CLICKED)
        self.assertTrue(view.workload_detail)

    def test_application_controls_remain_available_in_every_detail_tab(self):
        view = self.view
        view.page = 7
        for tab in range(5):
            view.app_tab = tab
            for key, stage in [('w', 'input'), ('l', 'input'), ('b', 'utility_disk')]:
                with self.subTest(tab=tab, key=key):
                    view.key(ord(key))
                    self.assertEqual(view.modal['stage'], stage)
                    self.assertEqual(view.modal['target']['unit'], 'demo.service')
                    view.key(27)
            for key, job in [('p', 'placement_open'), ('u', 'undo'),
                             ('U', 'placement_undo'), ('B', 'utility_disk_undo')]:
                with self.subTest(tab=tab, key=key), patch.object(view, 'job') as submit:
                    view.key(ord(key))
                    self.assertEqual(submit.call_args.args[0], job)

    def test_modal_and_filter_take_priority_over_workspace_shortcuts(self):
        view = self.view
        view.page = 7
        view.key(ord('w'))
        view.workspace_regions = [(1, 15, 2, ord('H'))]
        view.mouse(2, 2, curses.BUTTON1_CLICKED)
        self.assertEqual(view.modal['stage'], 'input')
        self.assertEqual(view.page, 7)
        view.key(27)
        view.key(ord('/'))
        for key in 'HA[]x':
            view.key(ord(key))
        self.assertEqual(view.workload_filter, 'HA[]x')
        self.assertEqual(view.app_tab, 0)
        self.assertEqual(view.page, 7)
        view.key(27)
        self.assertFalse(view.editing_filter)

    def test_local_actions_remain_visible_in_normal_terminal(self):
        view = self.view
        view.page = 1
        draw(Screen(24, 100), view, 1)
        self.assertIn(ord('g'), [key for _, _, key in view.action_regions])
        view.page = 7
        draw(Screen(24, 100), view, 1)
        self.assertIn(ord('p'), [key for _, _, key in view.action_regions])


if __name__ == '__main__':
    unittest.main()
