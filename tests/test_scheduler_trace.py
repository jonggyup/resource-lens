import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from resourcelens.scheduler_trace import analyze, distribution, SchedulerTrace, thread_set, summary_lines
from resourcelens.profiling import capture, compare
from resourcelens.ui import View, draw
from test_navigation import Screen


def switch(t, prev, state, nxt, stack='blocked_here'):
    return f'{t:.9f}: sched:sched_switch: prev_comm=worker prev_pid={prev} prev_prio=120 prev_state={state} ==> next_comm=next next_pid={nxt} next_prio=120\n\tffff {stack} ([kernel.kallsyms])\n'


def wake(t, pid):
    return f'{t:.9f}: sched:sched_wakeup: comm=worker pid={pid} prio=120 target_cpu=001\n\tffff wake_source ([kernel.kallsyms])\n'


def parse(text, **kwargs):
    return analyze(text.splitlines(), {10}, **kwargs)


class SchedulerTests(unittest.TestCase):
    def test_sleep_wakeup_and_switch_are_distinct_intervals(self):
        r=parse(switch(1,10,'S',20)+wake(2,10)+switch(2.2,20,'R',10,'wrong_next_stack'))
        self.assertEqual(r['status'],'complete')
        for key,value in [('off_cpu',1200),('sleep_to_wakeup',1000),('runqueue',200)]:
            self.assertAlmostEqual(r['distributions'][key]['p99_ms'],value)
        self.assertIn('blocked_here',r['top_stacks'][0]['frames'][0])
        self.assertNotIn('wrong_next_stack',str(r['top_stacks']))
        self.assertNotIn('wake_source',str(r['top_stacks']))

    def test_preempted_runnable_thread_waits_without_wakeup(self):
        r=parse(switch(1,10,'R+',20)+switch(1.02,20,'R',10))
        self.assertAlmostEqual(r['distributions']['runqueue']['max_ms'],20)
        self.assertEqual(r['distributions']['sleep_to_wakeup']['count'],0)

    def test_missing_wakeup_is_not_inferred(self):
        r=parse(switch(1,10,'D',20)+switch(2,20,'R',10))
        self.assertEqual(r['missing_wakeups'],1)
        self.assertEqual(r['distributions']['runqueue']['count'],0)
        self.assertEqual(r['distributions']['sleep_to_wakeup']['count'],0)
        self.assertEqual(r['distributions']['off_cpu']['max_ms'],1000)

    def test_boundary_intervals_and_duplicate_wakeup(self):
        r=parse(wake(1,10)+wake(1.1,10)+switch(2,20,'R',10)+switch(3,10,'S',20))
        self.assertEqual(r['unfinished_intervals'],1)
        self.assertEqual(r['duplicate_wakeups'],1)
        self.assertEqual(r['distributions']['off_cpu']['count'],0)
        self.assertEqual(r['distributions']['runqueue']['max_ms'],1000)
        r=parse(switch(1,20,'R',10))
        self.assertEqual(r['unpaired_switch_ins'],1)

    def test_loss_malformed_order_and_caps_suppress_derived_results(self):
        clean=switch(1,10,'R',20)+switch(2,20,'R',10)
        bad=[clean+'PERF_RECORD_LOST lost 20\n',clean+'3.000: sched:sched_switch: unsupported format\n',
             switch(2,10,'R',20)+switch(1,20,'R',10)]
        for text in bad:
            r=parse(text)
            self.assertEqual(r['status'],'partial')
            self.assertEqual(r['distributions'],{})
            self.assertEqual(r['top_stacks'],[])
        self.assertTrue(parse(clean,max_events=1)['truncated'])

    def test_foreign_threads_do_not_contribute_and_empty_is_explicit(self):
        r=parse(switch(1,11,'R',20)+switch(2,20,'R',11))
        self.assertEqual(r['distributions']['off_cpu']['count'],0)
        self.assertEqual(parse('')['status'],'empty')
        self.assertEqual(distribution([1,2,3,4])['p95_ms'],4)

    def test_no_fallback_to_unfiltered_capture_when_scope_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('resourcelens.scheduler_trace.shutil.which',return_value='/usr/bin/perf'), \
                 patch('resourcelens.scheduler_trace.thread_set',side_effect=ValueError('too large')), \
                 patch('resourcelens.scheduler_trace.subprocess.Popen') as popen:
                r=SchedulerTrace(tmp,os.getpid(),None,1).finish()
                self.assertEqual(r['status'],'unavailable')
                popen.assert_not_called()

    def test_native_permission_failure_keeps_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            script=Path(tmp)/'fake-perf'
            script.write_text('#!/bin/sh\necho Permission-denied >&2\nexit 1\n');script.chmod(0o700)
            with patch('resourcelens.scheduler_trace.shutil.which',return_value=str(script)), \
                 patch('resourcelens.scheduler_trace.host_pid_namespace',return_value=True):
                trace=SchedulerTrace(tmp,os.getpid(),None,1)
                trace.process.wait()
                r=trace.finish()
            self.assertEqual(r['status'],'failed')
            self.assertIn('Permission-denied',r['diagnostic'])
            self.assertIn('prev_pid ==',r['command'][r['command'].index('--filter')+1])
            self.assertEqual((Path(tmp)/'sched.log').stat().st_mode & 0o777,0o600)

    def test_capture_cancel_and_optional_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop=threading.Event();stop.set()
            with patch('resourcelens.scheduler_trace.SchedulerTrace') as backend:
                backend.return_value.finish.return_value={'status':'cancelled'}
                r=capture(Path(tmp)/'cancel',pid=os.getpid(),duration=.2,perf=False,scheduler=True,stop=stop)
                self.assertEqual(r['scheduler']['status'],'cancelled')
                backend.return_value.finish.assert_called_once_with(cancelled=True)
            with patch('resourcelens.scheduler_trace.SchedulerTrace') as backend:
                capture(Path(tmp)/'plain',pid=os.getpid(),duration=.2,perf=False)
                backend.assert_not_called()

    def test_ui_waits_entry_cancel_replay_and_rendering(self):
        view=View();view.page=7
        with patch.object(view,'open_profile') as opening:
            view.key(ord('W'));opening.assert_called_once_with(scheduler=True)
        view.replay=True;view.key(ord('W'))
        self.assertIsNone(view.profile_view)
        self.assertIn('live',view.modal['message'])
        text='\n'.join(summary_lines(dict(status='complete',distributions={'runqueue':distribution([1,2])})))
        self.assertIn('Ready to running',text)
        self.assertIn('p99 2.000 ms',text)

    def test_live_scope_has_identity(self):
        self.assertIn(os.getpid(),thread_set(pid=os.getpid()))

    def test_old_and_new_sessions_compare_without_scheduler_fabrication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            r=capture(root/'old',pid=os.getpid(),duration=.2,perf=False)
            r.pop('scheduler')
            (root/'old/session.json').write_text(json.dumps(r))
            capture(root/'new',pid=os.getpid(),duration=.2,perf=False)
            result=compare(root/'old',root/'new')
            self.assertEqual(result['scheduler'],{'baseline':['disabled'],'candidate':['disabled']})
            self.assertFalse(any(k.startswith('scheduler.') for k in result['metrics']))

    def test_namespace_mismatch_refuses_collection(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('resourcelens.scheduler_trace.shutil.which',return_value='/usr/bin/perf'), \
             patch('resourcelens.scheduler_trace.host_pid_namespace',return_value=False), \
             patch('resourcelens.scheduler_trace.subprocess.Popen') as popen:
            r=SchedulerTrace(tmp,os.getpid(),None,1).finish()
            self.assertEqual(r['status'],'unavailable')
            self.assertIn('namespace',r['reason'])
            popen.assert_not_called()

    def test_oversized_cgroup_scope_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'cgroup.threads').write_text('\n'.join(str(i) for i in range(1,66)))
            with self.assertRaisesRegex(ValueError,'64'):
                thread_set(cgroup=tmp)

    def test_repeated_switch_out_is_a_discontinuity(self):
        r=parse(switch(1,10,'S',20)+switch(2,10,'S',20)+wake(3,10)+switch(4,20,'R',10))
        self.assertEqual(r['discontinuities'],1)
        self.assertEqual(r['status'],'partial')
        self.assertEqual(r['distributions'],{})
