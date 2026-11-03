import curses
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from resourcelens.actions import dispatch
from resourcelens.memory_controls import MemoryHighSession
from resourcelens.readability import wrap_lines
from resourcelens.recording import Recorder,Replay
from resourcelens.ui import View,draw
from test_control_workflow import finish
from test_diagnostics import snapshot,REQUEST
from test_memory_controls import FixtureManager,MIB
from test_navigation import Screen


def fixture(root):
    manager=FixtureManager(Path(root)/'cg',service_path='/demo.service')
    sample=snapshot(3)
    sample['workloads'][0].update(manager.target)
    sample['target_context']['service']['manager']='user'
    ref=sample['target_context']['ancestors'][1]['identity']['identity']
    ref.update(inode=manager.target['inode'],device=manager.target['device'])
    view=View(control=True);view.update(sample);view.inspect_request(REQUEST)
    view.memory_session=MemoryHighSession(manager.backend);view.screen_size=(30,100)
    return view,manager


def review_to_end(view):
    for _ in range(100):
        view.memory_control_lines()
        lines=wrap_lines(view.memory_preview_content(),max(20,min(76,view.screen_size[1]-8)))
        if view.modal.get('scroll',0)+max(1,view.screen_size[0]-8)>=len(lines):return
        view.key(curses.KEY_NPAGE)
    raise AssertionError('Memory preview could not be reviewed')


class MemoryControlUITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.view,self.manager=fixture(self.tmp.name)

    def prepare(self,value='128M'):
        dispatch(self.view,'review_controls');self.view.key(ord('h'))
        self.assertEqual(self.view.modal['stage'],'memory_input')
        for c in value:self.view.key(ord(c))
        self.view.key(10);finish(self.view)

    def test_scope_handoff_preview_apply_history_observe_and_fresh_restore(self):
        self.prepare()
        self.assertEqual(self.view.modal['stage'],'memory_confirm');self.assertEqual(self.manager.writes,[])
        text=' '.join(self.view.memory_preview_content())
        self.assertIn('entire service and descendants',text)
        self.assertIn('reclaim/throttling',text);self.assertIn('Existing MemoryMax',text)
        review_to_end(self.view);self.view.key(10);finish(self.view)
        event=self.view.investigation.events[-1]
        self.assertEqual(event['domain'],'memory');self.assertEqual(event['status'],'verified')
        self.assertIn('coverage',' '.join(self.view.workflow_content()))
        self.view.key(ord('u'));finish(self.view)
        self.assertTrue(self.view.modal['plan']['restoring'])
        review_to_end(self.view);self.view.key(10);finish(self.view)
        restored=self.view.investigation.events[-1]
        self.assertEqual(restored['action'],'restore');self.assertEqual(restored['status'],'verified')
        self.assertEqual(restored['previous_value'],128*MIB);self.assertIsNone(restored['requested'])
        self.assertEqual(self.manager.writes,[str(128*MIB),'infinity'])
        self.assertEqual(self.view.active_inspector().request,REQUEST)

    def test_small_terminal_and_mouse_cannot_apply_before_the_full_preview(self):
        self.prepare('32M');self.view.screen_size=(14,60)
        self.assertIn('WARNING:',' '.join(self.view.memory_preview_content()))
        with patch.object(self.view,'job') as job:
            self.view.key(10);job.assert_not_called()
            review_to_end(self.view);draw(Screen(14,60),self.view,1)
            row,region=next((y,hit) for y,hit in self.view.modal_regions.items() if hit[2]=='apply')
            self.view.mouse(region[0],row,curses.BUTTON1_CLICKED)
            self.assertEqual(job.call_args.args[0],'memory_apply')

    def test_cancel_input_and_preview_do_not_write_and_controls_are_explicit(self):
        self.view.control=False;dispatch(self.view,'review_controls');review=self.view.modal
        self.view.key(ord('h'));self.assertEqual(self.view.modal['stage'],'control_mode')
        self.view.key(10);self.assertIs(self.view.modal,review)
        self.view.key(ord('h'));self.view.key(27);self.assertEqual(self.manager.writes,[])
        self.prepare();self.view.key(27);self.assertEqual(self.manager.writes,[])

    def test_denial_and_bad_input_show_readable_unconfirmed_or_blocked_outcomes(self):
        self.prepare('0')
        self.assertEqual(self.view.investigation.events[-1]['status'],'blocked')
        self.assertIn('unverified input',' '.join(self.view.workflow_content()))
        self.view.key(27);self.prepare();review_to_end(self.view)
        self.manager.deny=True;self.view.key(10);finish(self.view)
        self.assertEqual(self.view.investigation.events[-1]['status'],'unconfirmed')
        self.view.key(ord('H'));self.assertEqual(self.view.modal['mode'],'health')

    def test_replay_and_stale_confirmations_cannot_reach_the_memory_backend(self):
        self.prepare();review_to_end(self.view);self.view.replay=SimpleNamespace(index=0,offsets=[0])
        with patch.object(self.manager.backend,'snapshot',side_effect=AssertionError('live memory read')):
            self.view.key(10)
            self.assertIn('Replay',self.view.modal['message'])
            self.view.open_memory_control(self.manager.target)
            self.assertIn('Replay',self.view.modal['message'])
        self.assertEqual(self.manager.writes,[])

    def test_memory_receipts_are_recorded_and_offline_restore_is_disabled(self):
        path=Path(self.tmp.name)/'recording.jsonl'
        writer=Recorder(path);writer.write('sample',self.view.snapshot);self.view.recorder=writer
        self.prepare();review_to_end(self.view);self.view.key(10);finish(self.view);writer.close();self.view.recorder=None
        replay=Replay(path);self.addCleanup(replay.close)
        self.assertEqual(replay.events[-1]['domain'],'memory')
        self.view.replay=replay
        self.assertIn('read-only',self.view.restore_reason(replay.events[-1]))

    def test_group_memory_action_is_recorded_data_only(self):
        self.view.replay=object()
        with patch.object(self.manager.backend,'snapshot',side_effect=AssertionError('unexpected live read')):
            self.view.key(ord('m'))
            self.assertEqual(self.view.active_inspector().tab,1)
            self.assertIn('CGROUP MEMORY',' '.join(self.view.active_inspector().lines(self.view)))

    def test_quit_acknowledges_memory_changes_and_undo_opens_a_fresh_preview(self):
        self.prepare();review_to_end(self.view);self.view.key(10);finish(self.view)
        self.view.modal=None;self.view.browser=None
        self.view.key(ord('q'));self.assertEqual(self.view.modal['stage'],'quit')
        self.view.key(ord('u'));finish(self.view)
        self.assertEqual(self.view.modal['stage'],'memory_confirm')
        self.assertTrue(self.view.modal['plan']['restoring'])
        self.assertEqual(len(self.manager.writes),1)


if __name__=='__main__':unittest.main()
