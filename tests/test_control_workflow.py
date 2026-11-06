import curses
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from resourcelens.actions import dispatch
from resourcelens.controls import ControlError, ControlSession
from resourcelens.control_workflow import KINDS, service_target
from resourcelens.recording import Recorder, Replay
from resourcelens.ui import View, draw
from test_controls import FakeBackend
from test_diagnostics import snapshot, REQUEST
from test_navigation import Screen


def fixture():
    view = View(control=True)
    view.update(snapshot(3))
    view.snapshot['workloads'][0].update(controllable=True, manager='system')
    view.screen_size = (40,100)
    view.inspect_request(REQUEST)
    backend = FakeBackend()
    backend.info.update(path='/demo.service', inode=11)
    view.session = ControlSession(backend)
    return view, backend


def finish(view):
    deadline = time.monotonic()+2
    while view.results.empty() and time.monotonic()<deadline:
        time.sleep(.001)
    if view.results.empty(): raise AssertionError('Control worker did not finish')
    view.poll_actions()


class WorkflowTests(unittest.TestCase):
    def test_process_handoff_names_service_and_never_changes_during_review(self):
        view, backend = fixture()
        dispatch(view,'review_controls')
        self.assertEqual(view.modal['stage'],'workflow_review')
        text=' '.join(view.workflow_content())
        self.assertIn('entire service and child groups',text)
        self.assertIn('/demo.service/jobs',text)
        self.assertIn('Quota source: /demo.service',text)
        self.assertEqual(backend.calls,[])
        view.key(ord('l'))
        self.assertEqual(view.modal['target']['path'],'/demo.service')
        self.assertEqual(view.modal['stage'],'input')
        self.assertEqual(view.active_inspector().request,REQUEST)

    def test_handoff_rejects_movement_replacement_generation_and_scope(self):
        for change in ('membership','inode','scope','generation','service_generation'):
            view, backend = fixture(); dispatch(view,'review_controls')
            if change=='membership': view.snapshot['processes'][0]['cgroup']='/other'
            elif change=='inode': view.snapshot['workloads'][0]['inode']=77
            elif change=='scope': view.snapshot['collection_scope']['id']='other'
            elif change=='generation':view.snapshot['target_context']['selected_group']['identity']['generation']=1
            else:view.snapshot['target_context']['ancestors'][1]['identity']['identity']['generation']=1
            view.key(ord('l'))
            self.assertEqual(view.modal['stage'],'result',change)
            self.assertFalse(backend.calls)

    def test_changing_usage_does_not_invalidate_review_and_no_accessible_manager_is_explicit(self):
        view,backend=fixture(); dispatch(view,'review_controls')
        view.snapshot['workloads'][0]['cpu_pct']=78
        view.key(ord('l')); self.assertEqual(view.modal['stage'],'input')
        view.snapshot['workloads'][0]['controllable']=False
        target,reason=service_target(view.snapshot,REQUEST)
        self.assertIsNone(target); self.assertIn('not controllable',reason)

    def test_enabling_controls_returns_to_the_bound_scope_review(self):
        view,backend=fixture();view.control=False
        dispatch(view,'review_controls');review=view.modal
        view.key(ord('l'));self.assertEqual(view.modal['stage'],'control_mode')
        view.key(10)
        self.assertTrue(view.control);self.assertIs(view.modal,review)
        self.assertFalse(backend.calls)
        view.key(ord('l'));self.assertEqual(view.modal['stage'],'input')

    def test_end_to_end_preview_apply_observe_restore_uses_real_session_with_fake_backend(self):
        view,backend=fixture(); dispatch(view,'review_controls'); view.key(ord('l'))
        view.key(ord('2')); view.key(ord('5')); view.key(10); finish(view)
        self.assertEqual(view.modal['stage'],'confirm'); self.assertFalse(backend.calls)
        view.key(10); finish(view)
        event=view.investigation.events[-1]
        self.assertEqual(event['status'],'verified')
        self.assertEqual(event['target']['id'],'/demo.service')
        self.assertGreaterEqual(event['verified_at'],event['started_at'])
        self.assertEqual(backend.info['quota'],25)
        self.assertIn('coverage',' '.join(view.workflow_content()))
        self.assertIn('Latest configuration event',' '.join(view.active_inspector().lines(view)))
        view.key(ord('u')); self.assertEqual(view.modal['stage'],'workflow_restore')
        view.key(10); finish(view)
        self.assertIsNone(backend.info['quota'])
        self.assertEqual(view.investigation.events[-1]['action'],'restore')
        self.assertEqual(view.investigation.events[-1]['previous_value'],25)
        self.assertIsNone(view.investigation.events[-1]['requested'])
        self.assertEqual(view.session.events[0]['status'],'undone')
        view.open_changes(); view.key(ord('p'))
        self.assertIn('Restoration receipt: restored',' '.join(view.workflow_content()))
        self.assertEqual(view.active_inspector().request,REQUEST)

    def test_denial_and_stale_preview_differ_from_verified_setting(self):
        for failure in ('denied','stale','restart','timeout'):
            view,backend=fixture()
            target=view.snapshot['workloads'][0]
            plan=view.session.prepare(target,'quota','25')
            if failure=='denied': backend.deny=True
            elif failure=='stale': backend.info['quota']=30
            elif failure=='restart': backend.info['invocation']='new'
            else:
                def timeout(*args): raise ControlError('timeout; may have changed')
                backend.set=timeout
            view.job('apply',view.session.apply,plan); finish(view)
            event=view.investigation.events[-1]
            self.assertEqual(event['status'],'blocked' if failure in ('stale','restart') else 'unconfirmed')
            self.assertIsNone(event['verified_at'])
            self.assertFalse(view.investigation.comparison(event)['post_available'])
            self.assertTrue(event['error'])
            view.key(ord('H')); self.assertEqual(view.modal['mode'],'health')

    def test_verification_time_is_worker_completion_and_busy_escape_does_not_claim_rollback(self):
        view,backend=fixture();entered,release=threading.Event(),threading.Event()
        original_set=backend.set
        def delayed_set(*args):
            entered.set();release.wait(1);original_set(*args)
        backend.set=delayed_set
        view.job('apply',view.session.apply,view.session.prepare(view.snapshot['workloads'][0],'quota','25'))
        self.assertTrue(entered.wait(1))
        view.key(27);self.assertEqual(view.modal['stage'],'busy')
        release.set()
        deadline=time.monotonic()+2
        while view.results.empty() and time.monotonic()<deadline:time.sleep(.001)
        outcome=view.results.get_nowait()
        completed_at=outcome[-1]
        view.results.put(outcome);view.poll_actions()
        event=view.investigation.events[-1]
        self.assertEqual(event['verified_at'],completed_at)
        self.assertEqual(view.investigation.comparison(event)['after']['start'],completed_at)

    def test_failed_prepare_is_recorded_as_blocked_and_readonly_has_no_worker(self):
        view,backend=fixture(); backend.info['kernel_weight']=999
        view.job('prepare',view.session.prepare,view.snapshot['workloads'][0],'quota','25');finish(view)
        self.assertEqual(view.investigation.events[-1]['status'],'blocked')
        self.assertEqual(view.investigation.events[-1]['action'],'review')
        view.replay=object(); view.modal=None
        with patch.object(view,'job') as job:
            dispatch(view,'review_controls')
            job.assert_not_called()
        self.assertIn('live target',view.modal['message'])

    def test_restore_cannot_skip_newer_receipt_and_external_changes_are_not_overwritten(self):
        view,backend=fixture();target=view.snapshot['workloads'][0]
        for value in ('25','50'):
            view.job('apply',view.session.apply,view.session.prepare(target,'quota',value));finish(view)
        first,second=view.investigation.events
        self.assertIn('latest active change',view.restore_reason(first))
        backend.info.update(quota=70,kernel_quota=70,kernel_max='70000 100000')
        view.key(ord('u'));view.key(10);finish(view)
        self.assertEqual(backend.info['quota'],70)
        self.assertEqual(len(backend.calls),2)
        self.assertEqual(view.investigation.events[-1]['status'],'unconfirmed')
        self.assertIn('another actor',view.investigation.events[-1]['error'])

    def test_all_domains_share_history_and_restoration_is_domain_specific(self):
        view,backend=fixture()
        for kind,(domain,action) in KINDS.items():
            if action!='apply':continue
            target=view.snapshot['workloads'][0]
            plan=dict(target=target, before={}, value=20, property='quota')
            context=view.control_job_context(kind,(plan,))
            receipt=dict(target=target,plan=plan,before={},status='confirmed')
            view.control_sessions()[domain].events.append(receipt)
            view.control_finished(kind,receipt,None,context,context['started_at']+1)
            self.assertEqual(view.investigation.events[-1]['domain'],domain)
            self.assertIsNone(view.restore_reason(view.investigation.events[-1]))
        view.open_changes()
        self.assertIn('Entry 5/5',' '.join(view.workflow_content()))
        view.key(ord('p'));self.assertEqual(view.workflow_event()['domain'],'disk')

    def test_recorded_change_preserves_transition_without_a_live_restore_receipt(self):
        view,backend=fixture()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'recording'
            view.recorder=Recorder(path)
            view.recorder.write('sample',view.snapshot)
            view.job('apply',view.session.apply,view.session.prepare(view.snapshot['workloads'][0],'quota','25'));finish(view)
            view.recorder.close();view.recorder=None
            replay=Replay(path)
            try:
                event=replay.events[0]
                self.assertEqual(event['verified_at'],view.investigation.events[-1]['verified_at'])
                self.assertIn('before',event)
                view.replay=replay
                self.assertIn('read-only',view.restore_reason(event))
            finally:replay.close()

    def test_mouse_choices_wheel_and_small_terminal_restore_require_full_review(self):
        view,backend=fixture(); dispatch(view,'review_controls')
        screen=Screen(40,100);draw(screen,view,1)
        row,x,end,key=next(r for r in view.workflow_regions if r[3]==ord('l'))
        view.mouse(x,row,curses.BUTTON1_CLICKED)
        self.assertEqual(view.modal['property'],'quota')
        view.job('apply',view.session.apply,view.session.prepare(view.snapshot['workloads'][0],'quota','25'));finish(view)
        view.key(ord('u')); view.screen_size=(14,60)
        with patch.object(view,'job') as job:
            view.key(10);job.assert_not_called()
            view.key(curses.KEY_NPAGE);view.key(curses.KEY_NPAGE);view.workflow_lines()
            view.key(10);job.assert_called_once()


if __name__=='__main__':unittest.main()
