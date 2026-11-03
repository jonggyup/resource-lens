import curses
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from resourcelens.actions import dispatch, search_actions, BY_ID, availability
from resourcelens.diagnostics import context_for
from resourcelens.profiling import ProfileView
from resourcelens.targets import selection_request
from resourcelens.ui import View, draw
from test_diagnostics import snapshot, REQUEST
from test_navigation import Screen


class InspectorTests(unittest.TestCase):
    def setUp(self):
        self.view = View()
        self.view.replay = SimpleNamespace(index=3, offsets=list(range(4)))
        for i in range(4): self.view.update(snapshot(i, parent_delta=25000))
        self.view.page = 7
        self.view.key(10)
        self.screen = Screen(24, 100)
        draw(self.screen, self.view, 1)

    def test_summary_sections_and_clickable_finding_evidence(self):
        inspector = self.view.active_inspector()
        self.assertEqual(inspector.request, REQUEST)
        self.assertIn('Group quota throttling observed', '\n'.join(inspector.lines(self.view)))
        region = inspector.finding_regions[0]
        self.view.mouse(region[0], region[2], curses.BUTTON1_CLICKED)
        self.assertEqual(inspector.tab, 3)
        self.screen.erase(); draw(self.screen, self.view, 1)
        next_button = next(r for r in inspector.finding_regions if r[3] == 'step' and r[4] == 1)
        self.view.mouse(next_button[0], next_button[2], curses.BUTTON1_CLICKED)
        self.assertEqual(inspector.finding_index, 1)
        text = '\n'.join(inspector.lines(self.view))
        self.assertIn('throttled_usec', text)
        self.assertIn('Collection scope: scope', text)
        self.assertIn('not per-process', text)
        self.view.key(ord('1')); self.view.key(ord(']'))
        self.assertEqual(inspector.tab, 1)
        self.assertIn('Process CPU:', '\n'.join(inspector.lines(self.view)))
        self.assertIn('Group:', '\n'.join(inspector.lines(self.view)))

    def test_related_group_service_and_back_restore_inspector_state(self):
        inspector = self.view.active_inspector()
        self.view.key(curses.KEY_END)
        draw(self.screen, self.view, 1)
        previous = (inspector.tab, inspector.scroll)
        self.view.key(ord('g'))
        self.assertEqual(inspector.request, ('cgroup', REQUEST[3], 10, 1))
        self.assertEqual(self.view.browser.process, (123, 99))
        self.view.key(27)
        self.assertEqual(inspector.request, REQUEST)
        self.assertEqual((inspector.tab, inspector.scroll), previous)
        self.view.key(ord('o'))
        self.assertEqual(inspector.request, ('cgroup', '/demo.service', 11, 1))
        self.view.key(27); self.view.key(27)
        self.assertIsNone(self.view.browser)
        self.assertEqual(self.view.application_process, (123, 99))

    def test_ancestor_constraints_are_recomputed_without_child_limits(self):
        sample = snapshot(3, quota=10)
        context = context_for(sample, ('cgroup', '/demo.service', 11, 1))
        self.assertEqual(context['limits']['quota_pct'], 25)
        self.assertEqual(context['limits']['quota_source'], '/demo.service')
        self.assertEqual(len(context['ancestors']), 1)

    def test_group_list_inspect_and_snapshot_only_selection(self):
        self.view.browser = None
        self.view.application_mode, self.view.workload_id = 'cgroups', '/demo.service'
        self.view.key(ord('I'))
        inspector = self.view.active_inspector()
        self.assertEqual(inspector.request, ('cgroup', '/demo.service', 11, 1))
        self.assertIsNone(selection_request(self.view))
        self.view.replay = None
        self.assertEqual(selection_request(self.view), inspector.request)

    def test_search_action_binds_identity_and_never_runs_typed_text(self):
        self.view.replay = None
        self.view.key(ord('s'))
        items = self.view.modal['items']
        action = next(i for i in items if i[0] == 'action' and i[1]['id'] == 'profile')
        self.view.snapshot['processes'][0]['starttime'] = 100
        with patch('resourcelens.ui.ProfileView') as capture:
            self.view.open_search_result(action[0], action[1])
            capture.assert_not_called()
        self.assertIn('changed', self.view.modal['message'])
        self.assertEqual(self.view.browser.process, (123, 99))

    def test_search_navigation_preserves_back_trail_and_scope_changes_disable_actions(self):
        inspector = self.view.active_inspector()
        self.view.open_search_result('group', ('/demo.service', 11))
        self.assertEqual(inspector.request[1], '/demo.service')
        self.view.key(27)
        self.assertEqual(inspector.request, REQUEST)
        bound = next(item for item in search_actions(self.view) if item[1]['id'] == 'open_group')[1]
        self.view.snapshot['collection_scope']['id'] = 'new-scope'
        dispatch(self.view, bound['id'], bound['request'], bound['scope_id'])
        self.assertIn('scope changed', self.view.modal['message'])

    def test_replay_and_replacement_never_start_live_capture_or_memory(self):
        with patch('resourcelens.ui.ProfileView') as profile, patch('resourcelens.ui.MemorySharingView') as memory:
            dispatch(self.view, 'profile')
            self.assertIn('live', self.view.modal['message'])
            self.view.key(27)
            dispatch(self.view, 'memory')
            profile.assert_not_called(); memory.assert_not_called()
        self.view.key(27)
        self.view.key(ord('g'))
        inspector = self.view.active_inspector()
        draw(self.screen, self.view, 1)
        changed = deepcopy(self.view.snapshot)
        changed['target_context']['ancestors'][0]['identity']['identity']['generation'] = 2
        changed['target_context']['selected_group'] = changed['target_context']['ancestors'][0]['identity']
        self.view.update(changed)
        self.assertIn('lifetime changed', inspector.invalid_reason(changed))

    def test_inspector_help_blocks_capture_and_list_capture_locks_selection(self):
        self.view.replay = None
        inspector = self.view.active_inspector()
        self.view.key(ord('?'))
        with patch('resourcelens.ui.ProfileView') as profile, patch('resourcelens.ui.MemorySharingView') as memory:
            self.view.key(ord('P')); self.view.key(ord('V'))
            profile.assert_not_called(); memory.assert_not_called()
        self.assertTrue(inspector.help)
        self.view.key(27)
        self.view.browser = None
        self.view.application_process_manual = False
        with patch('resourcelens.ui.ProfileView'):
            self.view.open_profile()
        self.assertTrue(self.view.application_process_manual)
        self.assertEqual(self.view.application_process, (123,99))

    def test_small_layout_actions_mouse_and_help_share_registry(self):
        inspector = self.view.active_inspector()
        for width, height in ((40,12),(80,24),(120,35)):
            self.view.screen_size = height, width
            screen = Screen(height, width)
            self.view.key(ord('5'))
            for _ in range(8): self.view.key(curses.KEY_DOWN)
            draw(screen, self.view, 1)
            self.assertTrue(all(0 <= row < height for row, _ in screen.lines))
            self.assertTrue(inspector.action_regions)
            memory = next(r for r in inspector.action_regions if r[3] == 'memory')
            with patch('resourcelens.ui.MemorySharingView') as inspect:
                self.view.mouse(memory[0], memory[2], curses.BUTTON1_CLICKED)
                inspect.assert_not_called()
            self.view.key(27)
        inspector.help = True
        text = '\n'.join(inspector.lines(self.view))
        for action in BY_ID.values(): self.assertIn(action.help, text)


class CaptureChooserTests(unittest.TestCase):
    def test_duration_keyboard_mouse_and_counters_are_explicit(self):
        view = ProfileView({'pid': os.getpid()}, choose=True)
        self.assertEqual(view.target['duration'], 10)
        view.key(ord(']'), 24)
        self.assertEqual(view.target['duration'], 30)
        view.draw(Screen(12,40),40,12)
        region = next(r for r in view.duration_regions if r[3] == 5)
        view.mouse(region[0], region[2])
        self.assertEqual(view.target['duration'], 5)
        called = threading.Event()
        def capture(*args, **kwargs):
            called.set()
            return {}
        with patch('resourcelens.profiling.capture', side_effect=capture) as job:
            view.key(ord('4'),24)
            self.assertTrue(called.wait(1))
            self.assertFalse(job.call_args.kwargs['perf'])
            self.assertFalse(job.call_args.kwargs['scheduler'])
            self.assertEqual(job.call_args.kwargs['duration'], 5)

    def test_capture_context_is_private_and_saved_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            view = ProfileView({'pid':os.getpid()}, choose=True, context={'schema':1,'findings':[]})
            view.directory = Path(tmp) / 'capture'
            saved = threading.Event()
            original = __import__('resourcelens.profiling', fromlist=['write']).write
            def write(path, value):
                original(path, value)
                saved.set()
            def capture(directory, **kwargs):
                Path(directory).mkdir(mode=0o700)
                return {'status':'complete'}
            with patch('resourcelens.profiling.capture', side_effect=capture), patch('resourcelens.profiling.write', side_effect=write):
                view.start(3)
                self.assertTrue(saved.wait(1))
            path = view.directory / 'capture-context.json'
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text()), {'schema':1,'findings':[]})
