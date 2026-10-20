import curses
from pathlib import Path
import tempfile
import unittest
from resourcelens.hardware import Hardware, cpulist
from resourcelens.controls import ControlError
from resourcelens.ui import View


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.hw = Hardware(self.root)
        self.put('present', '0-3')
        self.put('possible', '0-15')
        self.put('online', '0-3')
        for cpu in range(4):
            self.put(f'cpu{cpu}/topology/physical_package_id', str(cpu // 2))
            self.put(f'cpu{cpu}/topology/core_id', str(cpu % 2))
            if cpu:
                self.put(f'cpu{cpu}/online', '1')
        for policy, cpus in ((0, '0 1'), (2, '2 3')):
            for name, value in dict(related_cpus=cpus, scaling_governor='powersave',
                                    scaling_available_governors='powersave performance', scaling_driver='fixture').items():
                self.put(f'cpufreq/policy{policy}/{name}', value)

    def put(self, path, value):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value + '\n')

    def test_present_not_possible(self):
        self.assertEqual(len(self.hw.snapshot()['cpus']), 4)
        self.assertEqual(cpulist('0-2,5 7'), [0, 1, 2, 5, 7])

    def test_shared_policy_preview_and_undo(self):
        plan = self.hw.prepare(('id', 1), 'governor', 'performance')
        self.assertEqual(plan['affected'], [0, 1])
        self.hw.apply(plan)
        self.assertEqual(self.hw.snapshot()['policies']['policy0']['governor'], 'performance')
        self.hw.undo()
        self.assertEqual(self.hw.snapshot()['policies']['policy0']['governor'], 'powersave')

    def test_bulk_deduplicated(self):
        self.assertEqual(len(self.hw.prepare(('all', None), 'governor', 'performance')['changes']), 2)
        self.assertEqual(len(self.hw.prepare(('socket', 1), 'governor', 'performance')['changes']), 1)

    def test_stale_preview_rejected(self):
        plan = self.hw.prepare(('all', None), 'governor', 'performance')
        self.put('cpufreq/policy0/scaling_governor', 'performance')
        with self.assertRaises(ControlError):
            self.hw.apply(plan)
        self.assertEqual(self.hw.events, [])

    def test_partial_failure_can_be_undone(self):
        original = self.hw.write
        def failing(path, value):
            if 'policy2' in path:
                raise PermissionError('denied')
            original(path, value)
        self.hw.write = failing
        with self.assertRaisesRegex(ControlError, 'partial'):
            self.hw.apply(self.hw.prepare(('all', None), 'governor', 'performance'))
        self.hw.write = original
        self.hw.undo()
        self.assertEqual(self.hw.snapshot()['policies']['policy0']['governor'], 'powersave')

    def test_undo_external_conflict(self):
        self.hw.apply(self.hw.prepare(('id', 1), 'governor', 'performance'))
        self.put('cpufreq/policy0/scaling_governor', 'schedutil')
        with self.assertRaisesRegex(ControlError, 'externally'):
            self.hw.undo()

    def test_hotplug_guards_and_restore(self):
        with self.assertRaisesRegex(ControlError, 'all online'):
            self.hw.prepare(('all', None), 'online', '0')
        with self.assertRaisesRegex(ControlError, 'does not expose'):
            self.hw.prepare(('id', 0), 'online', '0')
        self.hw.apply(self.hw.prepare(('id', 3), 'online', '0'))
        self.assertEqual((self.root / 'cpu3/online').read_text().strip(), '0')
        self.hw.undo()
        self.assertEqual((self.root / 'cpu3/online').read_text().strip(), '1')

    def test_last_cpu_checked_again_at_write(self):
        plan = self.hw.prepare(('id', 3), 'online', '0')
        self.put('online', '3')
        with self.assertRaises(ControlError):
            self.hw.apply(plan)

    def test_unsupported_governor(self):
        with self.assertRaises(ControlError):
            self.hw.prepare(('all', None), 'governor', 'bogus')

    def test_mouse_menu_and_keyboard_scope(self):
        view = View(control=True)
        view.hardware = self.hw
        view.page = 1
        view.cpu_regions = {10: ('id', 1)}
        view.mouse(5, 10, curses.BUTTON3_PRESSED)
        self.assertEqual(view.modal['scope'], ('id', 1))
        view.modal_regions = {7: (2, 70, 0)}
        view.mouse(5, 7, curses.BUTTON1_PRESSED)
        self.assertEqual(view.modal['scope'], ('socket', 0))
        view.hardware_key(27)
        view.key(ord('g'))
        self.assertEqual(view.modal['scope'], ('all', None))

    def test_readonly_mouse_does_not_enable_control(self):
        view = View()
        view.hardware_menu()
        self.assertEqual(view.modal['stage'], 'control_mode')
        self.assertFalse(view.control)

    def test_confirmation_resize_blocks_apply(self):
        view = View(control=True)
        view.hardware = self.hw
        view.modal = dict(stage='hardware_confirm', plan=self.hw.prepare(('all', None), 'governor', 'performance'))
        view.screen_size = (10, 40)
        view.hardware_key(10)
        self.assertEqual(view.modal['stage'], 'hardware_confirm')
        self.assertEqual(self.hw.events, [])

    def test_double_click_during_apply_is_ignored(self):
        view = View(control=True)
        view.modal = {'stage': 'busy'}
        view.modal_regions = {8: (2, 70, 'apply')}
        view.mouse(5, 8, curses.BUTTON1_PRESSED)
        self.assertEqual(view.modal['stage'], 'busy')
