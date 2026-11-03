import curses
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from resourcelens.sessions_ui import SessionBrowser
from resourcelens.sessions import entry
from resourcelens.ui import View,draw
from test_navigation import Screen
from test_sessions import saved_profile,saved_recording
from test_diagnostics import snapshot,REQUEST


def finish(browser):
    deadline=time.monotonic()+3
    while browser.pending and time.monotonic()<deadline:
        browser.poll();time.sleep(.001)
    if browser.pending:raise AssertionError('Sessions worker did not finish: '+browser.pending)
    browser.poll()


class SessionsUITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def browser(self):
        browser=SessionBrowser(self.root);self.addCleanup(browser.close);finish(browser);return browser

    def test_profiles_baseline_comparison_outcome_and_redacted_export_without_cli(self):
        a,_=saved_profile(self.root/'profiles','secret-before',10)
        b,_=saved_profile(self.root/'profiles','secret-after',20)
        browser=self.browser()
        browser.open(entry(a));finish(browser)
        self.assertEqual(browser.stage,'profile')
        browser.key(ord('o'),24)
        for c in 'p99 | ms | 12 | secret workload log':browser.key(ord(c),24)
        browser.key(10,24);finish(browser)
        self.assertEqual(browser.profile['outcomes']['p99'],12)
        browser.key(ord('b'),24)
        browser.key(27,24)
        browser.selected=next(i for i,e in enumerate(browser.entries) if e['path']==str(b/'session.json'))
        browser.key(ord('c'),24);finish(browser)
        self.assertEqual(browser.stage,'comparison')
        self.assertEqual(browser.comparison['metrics']['cpu_percent']['change_percent'],100)
        browser.key(ord('x'),24);finish(browser)
        report=next((self.root/'reports').glob('*.json')).read_text()
        self.assertNotIn('secret-',report);self.assertNotIn(str(self.root),report)
        self.assertIn('capture_coverage',report)

    def test_recording_reopens_offline_and_never_calls_live_capture_or_controls(self):
        path=saved_recording(self.root/'record.jsonl')
        browser=self.browser()
        with patch('resourcelens.controls.SystemdBackend.snapshot',side_effect=AssertionError('live control')), \
             patch('resourcelens.profiling.capture',side_effect=AssertionError('live capture')), \
             patch('resourcelens.memory_sharing.capture',create=True,side_effect=AssertionError('live PID')):
            browser.open(entry(path));finish(browser)
            self.assertIsNotNone(browser.record_view)
            view=browser.record_view;view.inspect_request(REQUEST)
            for key in ('P','V','C'):
                browser.key(ord(key),24)
                self.assertIn('live' if key in ('P','V') else 'Replay',view.modal['message'])
                browser.key(27,24)
            screen=Screen(24,100);browser.draw(screen,100,24)
            self.assertIsNotNone(browser.record_view,browser.message)
            browser.key(ord('L'),24)
            self.assertEqual(browser.stage,'list');self.assertIsNone(browser.record_view)

    def test_recording_seek_retains_inspector_and_new_findings_use_recorded_time(self):
        path=saved_recording(self.root/'record.jsonl')
        browser=self.browser();browser.open(entry(path));finish(browser)
        view=browser.record_view;view.inspect_request(REQUEST)
        browser.key(ord(','),24)  # Inspector owns keys; seek through replay route directly.
        view.replay_delta=-1;browser.poll();finish(browser)
        self.assertEqual(browser.record_view.replay.index,2)
        self.assertEqual(browser.record_view.active_inspector().request,REQUEST)
        self.assertTrue(browser.record_view.active_inspector().lines(browser.record_view))
        browser.record_view.replay_delta=-2;browser.poll();finish(browser)
        browser.record_view.replay_playing=True
        for expected in (1,2,3):
            browser.play_deadline=0;browser.poll();finish(browser)
            self.assertEqual(browser.record_view.replay.index,expected)
            self.assertEqual(browser.record_view.replay_playing,expected<3)
        version=browser.version
        browser.record_view.job('utility_report',lambda:'fixture report',allow_replay=True)
        deadline=time.monotonic()+1
        while browser.record_view.results.empty() and time.monotonic()<deadline:time.sleep(.001)
        browser.poll()
        self.assertEqual(browser.record_view.modal['stage'],'result')
        self.assertGreater(browser.version,version)

    def test_live_dashboard_selection_and_data_are_preserved_behind_sessions(self):
        view=View();view.update(snapshot(3));view.inspect_request(REQUEST)
        before=view.snapshot
        with patch('resourcelens.usability.state_dir',return_value=self.root):view.key(ord('L'))
        finish(view.sessions_view)
        self.assertIs(view.snapshot,before)
        self.assertEqual(view.active_inspector().request,REQUEST)
        view.key(27)
        self.assertIsNone(view.sessions_view)
        self.assertEqual(view.active_inspector().request,REQUEST)

    def test_missing_and_malformed_imports_are_readable_and_list_is_usable(self):
        browser=self.browser();browser.key(ord('i'),24)
        for c in str(self.root/'missing.jsonl'):browser.key(ord(c),24)
        browser.key(10,24);finish(browser)
        self.assertTrue(browser.message)
        self.assertEqual(browser.stage,'import')
        browser.key(27,24);self.assertEqual(browser.stage,'list')

    def test_cancel_pending_discovery_is_immediate(self):
        release=threading.Event()
        with patch('resourcelens.sessions_ui.discover',side_effect=lambda *a:release.wait(2) or {}):
            browser=SessionBrowser(self.root)
            started=time.monotonic();self.assertFalse(browser.key(27,24))
            self.assertLess(time.monotonic()-started,.05);release.set()

    def test_mouse_small_layout_scrolling_and_partial_results(self):
        path,_=saved_profile(self.root/'profiles',status='partial')
        browser=self.browser();screen=Screen(14,60);browser.draw(screen,60,14)
        row,start,end,key=next(r for r in browser.regions if isinstance(r[3],tuple))
        browser.mouse(start,row,curses.BUTTON1_CLICKED,14);finish(browser)
        browser.draw(screen,60,14)
        self.assertEqual(browser.stage,'profile')
        browser.key(curses.KEY_NPAGE,14);browser.draw(screen,60,14)
        self.assertGreater(browser.scroll,0)
        browser.key(ord('b'),14);browser.key(ord('c'),14);finish(browser)
        self.assertIn('complete',browser.message)
        self.assertIsNone(browser.comparison)

    def test_offline_cli_never_constructs_collector_and_rejects_live_flags(self):
        from resourcelens.__main__ import main
        with patch('sys.argv',['resource-lens','--sessions',str(self.root),'--no-config']), \
             patch('sys.stdin.isatty',return_value=True),patch('sys.stdout.isatty',return_value=True), \
             patch('resourcelens.__main__.Collector',side_effect=AssertionError('live telemetry')), \
             patch('resourcelens.__main__.run') as run:
            self.assertEqual(main(),0)
            self.assertIsNone(run.call_args.args[0]);self.assertEqual(run.call_args.kwargs['sessions'],str(self.root))
        with patch('sys.argv',['resource-lens','--sessions','--control']),patch('sys.stderr'):
            with self.assertRaises(SystemExit):main()

    def test_sparse_legacy_recording_remains_navigable_without_live_fallbacks(self):
        from resourcelens.recording import Recorder
        sample=json.loads((Path(__file__).parent/'fixtures/legacy-sample-v1.json').read_text())
        path=self.root/'legacy.jsonl';writer=Recorder(path);writer.write('sample',sample);writer.close()
        browser=self.browser();browser.open(entry(path));finish(browser)
        self.assertIsNotNone(browser.record_view,browser.message)
        for page in range(10):
            browser.record_view.page=page
            browser.draw(Screen(24,80),80,24)
            self.assertIsNotNone(browser.record_view,browser.message)


if __name__=='__main__':unittest.main()
