from copy import deepcopy
import curses
import unittest
from unittest.mock import patch
from resourcelens.controls import ControlError
from resourcelens.placement import PlacementSession, format_cpus, parse_cpus
from resourcelens.topology import topology_map
from resourcelens.ui import View


class Backend:
    def __init__(self):
        self.info = dict(path='/example.service', inode=1, invocation='one', allowed=[], kernel=None,
                         effective=[0, 1, 2, 3], eligible=[0, 1, 2, 3], online=[0, 1, 2, 3])
        self.calls = []
        self.fail = False
        self.false_success = False
    def snapshot(self, target):
        return deepcopy(self.info)
    def set(self, target, cpus):
        self.calls.append(cpus)
        if self.fail:
            raise ControlError('Denied')
        self.info['allowed'] = cpus
        if not self.false_success:
            self.info['kernel'] = cpus
            self.info['effective'] = cpus or self.info['eligible']


class PlacementTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.session = PlacementSession(self.backend)
        self.target = dict(unit='example.service', path='/example.service')

    def test_ranges_roundtrip(self):
        self.assertEqual(format_cpus([7, 0, 2, 1, 7, 4]), '0-2,4,7')
        self.assertEqual(parse_cpus('0-2,4 7'), [0, 1, 2, 4, 7])
        for value in ['3-1', '0-999999', '1;2', 'no']:
            with self.assertRaises(ControlError):
                parse_cpus(value)

    def test_apply_and_restore_inheritance(self):
        self.session.apply(self.session.prepare(self.target, [1, 3]))
        self.assertEqual(self.backend.info['effective'], [1, 3])
        self.session.undo()
        self.assertEqual(self.backend.info['effective'], [0, 1, 2, 3])
        self.assertEqual(self.session.events[0]['status'], 'undone')

    def test_explicit_original_restored(self):
        self.backend.info.update(allowed=[0, 1], kernel=[0, 1], effective=[0, 1])
        self.session.apply(self.session.prepare(self.target, [2]))
        self.session.undo()
        self.assertEqual(self.backend.info['allowed'], [0, 1])

    def test_empty_offline_and_parent_excluded_rejected(self):
        for cpus in ([], [9], [2]):
            self.backend.info['eligible'] = [0, 1]
            with self.assertRaises(ControlError):
                self.session.prepare(self.target, cpus)
        self.assertFalse(self.backend.calls)

    def test_reset_inherits_parent(self):
        self.backend.info.update(allowed=[0], kernel=[0], effective=[0], eligible=[0, 1])
        self.session.apply(self.session.prepare(self.target, None))
        self.assertEqual(self.backend.info['effective'], [0, 1])

    def test_replaced_unit_and_changed_parent_abort(self):
        for key, value in [('invocation', 'two'), ('inode', 2), ('eligible', [1])]:
            backend = Backend()
            session = PlacementSession(backend)
            plan = session.prepare(self.target, [1])
            backend.info[key] = value
            with self.assertRaises(ControlError):
                session.apply(plan)
            self.assertFalse(backend.calls)

    def test_undo_external_conflict(self):
        self.session.apply(self.session.prepare(self.target, [1]))
        self.backend.info.update(allowed=[2], kernel=[2], effective=[2])
        with self.assertRaises(ControlError):
            self.session.undo()

    def test_denied_request_has_noop_undo(self):
        self.backend.fail = True
        with self.assertRaises(ControlError):
            self.session.apply(self.session.prepare(self.target, [1]))
        self.session.undo()
        self.assertEqual(self.session.events[-1]['status'], 'undone')

    def test_false_success_never_confirmed(self):
        self.backend.false_success = True
        with patch('resourcelens.placement.time.monotonic', side_effect=[0, 2]):
            with self.assertRaises(ControlError):
                self.session.apply(self.session.prepare(self.target, [1]))
        self.assertEqual(self.session.events[-1]['status'], 'unconfirmed')
        self.backend.false_success = False
        self.session.undo()

    def test_offline_during_apply_not_silently_accepted(self):
        plan = self.session.prepare(self.target, [1, 2])
        self.backend.info.update(allowed=[1, 2], kernel=[1, 2], effective=[1], eligible=[0, 1, 3])
        with patch('resourcelens.placement.time.monotonic', side_effect=[0, 2]):
            with self.assertRaises(ControlError):
                self.session.verify(self.target, plan['before'], [1, 2])

    def test_ui_core_socket_toggle_and_empty_preview(self):
        view = View(control=True)
        view.placement_session = self.session
        view.placement_ready(dict(target=self.target, info=self.backend.snapshot(self.target)))
        view.placement_toggle([0, 2])
        self.assertEqual(view.placement['selected'], {1, 3})
        view.placement_key(ord('n'))
        self.assertEqual(view.placement['selected'], set())
        view.placement_key(ord('r'))
        self.assertTrue(view.placement['inherit'])
        view.placement_key(curses.KEY_RIGHT)
        self.assertEqual(view.placement['focus'], 1)
        view.placement_key(27)
        self.assertEqual(view.page, 7)
        self.assertIsNone(view.placement)

    def test_navigation_follows_tile_geometry(self):
        view = View(control=True)
        view.placement_ready(dict(target=self.target, info=self.backend.snapshot(self.target)))
        view.topology_line_regions = {8: [(0, 28, 'cpu', (0,)), (29, 57, 'cpu', (1,))],
                                      9: [(0, 28, 'cpu', (2,)), (29, 57, 'cpu', (3,))]}
        view.placement_key(curses.KEY_DOWN)
        self.assertEqual(view.placement['focus'], 2)
        view.placement_key(curses.KEY_RIGHT)
        self.assertEqual(view.placement['focus'], 3)
        view.placement_key(curses.KEY_UP)
        self.assertEqual(view.placement['focus'], 1)

    def test_preview_cannot_apply_when_clipped(self):
        view = View(control=True)
        view.placement_session = self.session
        view.modal = dict(stage='placement_confirm', plan=self.session.prepare(self.target, [1]))
        view.screen_size = (12, 40)
        view.placement_confirm_key(10)
        self.assertFalse(self.backend.calls)


class TopologyTests(unittest.TestCase):
    def sample(self):
        return dict(cpus=[dict(id=i, socket=i // 4, core=i % 2, die=0, nodes=[i // 4],
                              siblings=[i // 4 * 4 + i % 2, i // 4 * 4 + i % 2 + 2],
                              online=i != 7, policy=None) for i in range(8)], policies={})

    def test_siblings_share_tile_and_sockets_separate(self):
        lines, hits = topology_map(self.sample(), {}, 80)
        tiles = [ids for row in hits.values() for _, _, kind, ids in row if kind == 'core']
        self.assertEqual(tiles, [(0, 2), (1, 3), (4, 6), (5, 7)])
        self.assertIn('OFFLINE', '\n'.join(lines))
        self.assertEqual(sum('SOCKET' in line for line in lines), 2)
        self.assertEqual(sum('NUMA' in line for line in lines), 2)

    def test_layout_bounds_and_hit_targets(self):
        for width in (37, 77, 117, 200):
            lines, hits = topology_map(self.sample(), {}, width, {1}, {0, 1, 2, 3}, 1)
            self.assertTrue(all(len(line) <= width for line in lines if line.startswith(('+', '|'))))
            found = []
            for row, entries in hits.items():
                for start, end, kind, ids in entries:
                    self.assertLessEqual(end, width)
                    if kind == 'cpu':
                        self.assertIn(f'CPU{ids[0]}', lines[row][start:end])
                        found.extend(ids)
            self.assertEqual(sorted(found), list(range(8)))

    def test_unknown_topology_does_not_merge_cpus(self):
        data = dict(cpus=[dict(id=i, socket=None, core=None, online=True, policy=None) for i in range(2)])
        _, hits = topology_map(data, {}, 80)
        self.assertEqual(sum(kind == 'core' for row in hits.values() for _, _, kind, _ in row), 2)
