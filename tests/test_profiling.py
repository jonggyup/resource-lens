import curses
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from resourcelens.profiling import capture,compare,metrics,ProfileView
from resourcelens.ui import View


class ProfilingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_live_private_capture_and_no_overwrite(self):
        path=self.root/'capture'
        r=capture(path,pid=os.getpid(),duration=.2,perf=False)
        self.assertEqual(r['status'],'complete')
        self.assertGreater(len(r['samples']),1)
        self.assertEqual(path.stat().st_mode & 0o777,0o700)
        self.assertEqual((path/'session.json').stat().st_mode & 0o777,0o600)
        with self.assertRaises(FileExistsError):capture(path,pid=os.getpid(),duration=.2,perf=False)

    def test_cancel_saves_partial_without_misleading_comparison(self):
        stop=threading.Event();stop.set()
        r=capture(self.root/'cancel',pid=os.getpid(),duration=10,stop=stop,perf=False)
        self.assertEqual(r['status'],'cancelled')
        with self.assertRaisesRegex(ValueError,'complete'):compare(self.root/'cancel',self.root/'cancel')

    def test_identity_reuse_and_invalid_arguments(self):
        with self.assertRaisesRegex(ValueError,'reused'):
            capture(self.root/'reuse',pid=os.getpid(),expected_start=-1)
        for kwargs in ({'pid':0},{'pid':os.getpid(),'duration':float('nan')},{'cgroup':'/tmp'},{}):
            with self.assertRaises(ValueError):capture(self.root/'bad',**kwargs)
        self.assertFalse((self.root/'bad').exists())

    def test_missing_perf_keeps_honest_counter_session(self):
        with patch('resourcelens.profiling.shutil.which',return_value=None):
            r=capture(self.root/'missing',pid=os.getpid(),duration=.2)
        self.assertEqual(r['profile']['status'],'unavailable')
        self.assertEqual(r['status'],'complete')

    def test_failed_perf_not_success(self):
        script=self.root/'perf';script.write_text('#!/bin/sh\necho permission-denied >&2\nexit 1\n');script.chmod(0o700)
        with patch('resourcelens.profiling.shutil.which',return_value=str(script)):
            r=capture(self.root/'failed',pid=os.getpid(),duration=.2)
        self.assertEqual(r['profile']['status'],'failed')
        self.assertIn('permission-denied',r['profile']['diagnostic'])

    def test_wait_accounting_excludes_reused_threads_and_disabled_accounting(self):
        first=dict(cpu_ticks=0,rss_bytes=0,major_faults=0,threads={'1':dict(starttime=10,schedstat='0 100 0'),'2':dict(starttime=20,schedstat='0 500 0')})
        last=dict(cpu_ticks=10,rss_bytes=20,major_faults=1,threads={'1':dict(starttime=10,schedstat='0 1000000100 0'),'2':dict(starttime=21,schedstat='0 10000000000 0')})
        key='surviving_threads_runqueue_wait_seconds_per_second'
        self.assertNotIn(key,metrics(first,last,2))
        self.assertEqual(metrics(first,last,2,schedstats=True)[key],.5)

    def test_repeated_comparison_reports_variability_and_outcomes(self):
        original=capture(self.root/'seed',pid=os.getpid(),duration=.2,perf=False)
        for side,values in [('before',[10,20,30]),('after',[20,30,40])]:
            for i,value in enumerate(values):
                folder=self.root/side/str(i);folder.mkdir(parents=True)
                original['metrics']={'cpu_percent':value};original['outcomes']={'p99_ms':value}
                (folder/'session.json').write_text(json.dumps(original))
        r=compare(self.root/'before',self.root/'after')
        row=r['metrics']['cpu_percent']
        self.assertEqual(row['baseline']['count'],3)
        self.assertEqual(row['baseline']['stdev'],10)
        self.assertEqual(row['change_percent'],50)
        self.assertIn('outcome.p99_ms',r['metrics'])

    def test_ui_cancel_is_immediate_and_replay_cannot_capture(self):
        with patch('resourcelens.profiling.capture',side_effect=lambda *a,stop,**k: stop.wait(5)):
            view=ProfileView(dict(pid=os.getpid()))
            start=time.monotonic()
            self.assertFalse(view.key(27,24))
            self.assertLess(time.monotonic()-start,.05)
            self.assertTrue(view.stop.is_set())
        dashboard=View();dashboard.replay=True;dashboard.key(ord('P'))
        self.assertIsNone(dashboard.profile_view)
        self.assertIn('live',dashboard.modal['message'])

    def test_target_exit_marks_partial(self):
        process=subprocess.Popen(['sleep','.1'])
        try:
            reaper=threading.Thread(target=process.wait);reaper.start()
            r=capture(self.root/'exit',pid=process.pid,duration=.5,perf=False)
            reaper.join()
            self.assertEqual(r['status'],'partial')
            with self.assertRaises(ValueError):capture(self.root/'gone',pid=process.pid,duration=.2,perf=False)
        finally:
            if process.poll() is None:process.terminate();process.wait()

    def test_cli_rejects_capture_flags_in_compare_mode(self):
        result=subprocess.run(['python3','-m','resourcelens','--compare','a','b','--pid','1'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('require --profile',result.stderr)

    def test_chooser_does_not_collect_until_choice_and_can_cancel(self):
        with patch('resourcelens.profiling.capture') as collecting:
            view=ProfileView(dict(pid=os.getpid()),choose=True)
            self.assertTrue(view.choosing)
            collecting.assert_not_called()
            self.assertFalse(view.key(27,24))
            view.start(True)
            collecting.assert_not_called()

    def test_chooser_keyboard_and_mouse_select_explicit_mode(self):
        from test_navigation import Screen
        for use_mouse in (False,True):
            view=ProfileView(dict(pid=os.getpid()),choose=True)
            with patch.object(view,'start') as start:
                if use_mouse:
                    view.draw(Screen(24,100),100,24)
                    left,right,row,mode=view.regions[1]
                    view.mouse(left,row)
                else:
                    view.key(curses.KEY_DOWN,24)
                    self.assertEqual(view.selection,1)
                    view.key(10,24)
                start.assert_called_once_with(True)

    def test_summary_keeps_failures_visible_and_details_separate(self):
        view=ProfileView(dict(pid=os.getpid()),choose=True)
        view.choosing=False
        view.result=dict(status='complete',metrics={'cpu_percent':20},warnings=['measurement warning'],
                         profile={'status':'failed','diagnostic':'precise error'},
                         scheduler={'status':'unavailable','reason':'Permission denied'})
        summary='\n'.join(view.lines())
        self.assertIn('CPU stack capture failed',summary)
        self.assertIn('Permission denied',summary)
        self.assertNotIn('precise error',summary)
        view.key(ord('D'),24)
        self.assertIn('precise error','\n'.join(view.lines()))
        self.assertIn('measurement warning','\n'.join(view.lines()))

    def test_main_mouse_route_handles_profile_click_and_wheel(self):
        from test_navigation import Screen
        dashboard=View()
        profile=ProfileView(dict(pid=os.getpid()),choose=True)
        dashboard.profile_view=profile
        profile.draw(Screen(24,100),100,24)
        with patch.object(profile,'start') as start:
            dashboard.mouse(5,8,curses.BUTTON1_PRESSED)
            start.assert_called_once_with(True)
        profile.choosing=False
        profile.scroll=2
        dashboard.mouse(5,8,curses.BUTTON4_PRESSED)
        self.assertEqual(profile.scroll,1)
