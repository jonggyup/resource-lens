from copy import deepcopy
import curses
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from resourcelens.collect import Collector
from resourcelens.controls import ControlError
from resourcelens.disk_controls import DiskSession, bandwidth
from resourcelens.helper import dispatch, trusted
from resourcelens.incidents import IncidentCapture
from resourcelens.recording import Replay
from resourcelens.ui import View
from resourcelens.usability import preferences, save_preferences, load_preferences, export_report, tree_rows, capability_lines


class FakeDisk:
    def __init__(self):
        self.state = dict(path='/test',inode=1,invocation='a',read={},write={},kernel={})
        self.dev = dict(name='sda',path='/dev/sda',device='8:0',inode=3,backing=[],holders=[])
        self.writes = []
    def device(self,name):
        return deepcopy(self.dev)
    def snapshot(self,target):
        return deepcopy(self.state)
    def matches(self,state):
        return state['kernel'] == {'values': [state['read'],state['write']]} or not any((state['kernel'],state['read'],state['write']))
    def set(self,target,direction,values,removed):
        self.writes.append((direction,deepcopy(values)))
        self.state[direction] = deepcopy(values)
        self.state['kernel'] = {'values':[self.state['read'],self.state['write']]} if self.state['read'] or self.state['write'] else {}


TARGET = dict(unit='test.service',path='/test',inode=1,manager='system',controllable=True)


class UsabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = Collector(nvidia=False).sample()

    def test_shutdown_cancels_external_telemetry_query(self):
        import threading
        import time
        import sys
        collector=Collector(nvidia=False)
        result=[]
        worker=threading.Thread(target=lambda:result.append(collector.run_query(
            [sys.executable,'-c','import time; time.sleep(30)'],capture_output=True,text=True,timeout=31)))
        worker.start()
        try:
            deadline=time.monotonic()+2
            while collector.active_query is None and time.monotonic()<deadline:
                time.sleep(.005)
            child=collector.active_query
            self.assertIsNotNone(child)
            collector.close();worker.join(timeout=1)
            self.assertFalse(worker.is_alive())
            self.assertIsNotNone(child.poll())
            with self.assertRaises(OSError):collector.run_query(['true'],timeout=1)
        finally:
            collector.close();worker.join(timeout=2)

    def test_capabilities_unknown_and_collection_cost(self):
        sample=deepcopy(self.sample)
        sample['gpus']=[]
        text='\n'.join(capability_lines(sample,'test failure'))
        self.assertIn('test failure',text)
        self.assertIn('GPU utilization: unavailable',text)
        self.assertGreaterEqual(sample['collector']['duration_ms'],0)
        self.assertGreaterEqual(sample['collector']['cpu_ms'],0)

    def test_preferences_only_presentation_and_private_atomic_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'preferences.json'
            view=View();view.page=9;view.workload_filter='test';view.control=True
            save_preferences(view,path)
            values=load_preferences(path)
            self.assertEqual(values['page'],9)
            self.assertNotIn('control',values)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(preferences({'page':100,'sort':'invalid','control':True}),{})
            self.assertEqual(preferences({'page':10,'overview_expanded':False}),
                             {'page':0,'overview_expanded':True})
            path.write_text('[]')
            with self.assertRaises(ValueError):load_preferences(path)

    def test_report_redaction_and_no_overwrite(self):
        view=View();view.update(self.sample)
        view.mark_event('secret-name',target={'id':'/private/path','inode':1})
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report'
            export_report(view,path)
            text=path.read_text()
            self.assertNotIn('secret-name',text)
            self.assertNotIn('/private/path',text)
            self.assertIn('available_samples',text)
            with self.assertRaises(FileExistsError):export_report(view,path)
            path.unlink();export_report(view,path,False)
            self.assertIn('secret-name',path.read_text())

    def test_tree_handles_missing_parents_and_cycles(self):
        rows=[dict(pid=2,ppid=1),dict(pid=1,ppid=0),dict(pid=3,ppid=4),dict(pid=4,ppid=3)]
        result=tree_rows(rows)
        self.assertEqual([p['pid'] for p,d in result],[1,2,3,4])
        self.assertEqual(result[1][1],1)
        deep=[dict(pid=i,ppid=i-1) for i in range(2000)]
        self.assertEqual(len(tree_rows(deep)),2000)

    def test_workload_selection_survives_sort_and_reports_replacement(self):
        view=View();sample=deepcopy(self.sample)
        a=dict(id='/a',inode=1,unit='a.service',cpu_pct=1,memory_current=10)
        b=dict(id='/b',inode=2,unit='b.service',cpu_pct=2,memory_current=1)
        sample['workloads']=[a,b];view.update(sample)
        view.workload_rows();self.assertEqual(view.workload_id,'/b')
        view.workload_sort='memory';view.workload_rows();self.assertEqual(view.workload_id,'/b')
        b['inode']=3;view.workload_rows();self.assertIn('restarted',view.workload_notice)
        sample['workloads']=[a];view.workload_rows();self.assertIsNone(view.selected_workload())
        view.page=7;view.application_mode='cgroups';view.key(curses.KEY_DOWN);self.assertEqual(view.workload_id,'/a')

    def test_new_ui_shortcuts_and_replay_control_block(self):
        view=View();view.update(self.sample)
        view.key(ord('0'));self.assertEqual(view.page,9)
        view.page=5;view.key(ord('t'));self.assertTrue(view.process_tree)
        view.key(ord('N'));view.key(ord('X'));view.key(10)
        self.assertEqual(view.investigation.events[-1]['label'],'X')
        view.page=7;view.application_mode='cgroups';view.replay=object();view.key(ord('b'))
        self.assertIn('Replay is read-only',view.modal['message'])

    def sample_at(self,t,value=30):
        sample=deepcopy(self.sample);sample['timestamp']=t;sample['elapsed_s']=1
        sample['workloads']=[];sample['pressure']={'cpu':{'some':{'avg10':value}}}
        return sample

    def test_incident_sustained_capture_replay_and_file_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            capture=IncidentCapture(directory,duration=2,post=1,cooldown=3,max_files=1)
            self.assertIsNone(capture.observe(self.sample_at(0)))
            self.assertIsNone(capture.observe(self.sample_at(1)))
            event=capture.observe(self.sample_at(2));self.assertEqual(event['status'],'alert')
            capture.observe(self.sample_at(3));self.assertIsNone(capture.writer)
            path=next(Path(directory).glob('*.jsonl'))
            replay=Replay(path)
            self.assertEqual(len(replay.offsets),4);self.assertEqual(len(replay.events),1);replay.close()
            capture.observe(self.sample_at(6));self.assertIn('budget reached',capture.status)

    def test_quota_incident_uses_rate_and_cgroup_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            capture=IncidentCapture(directory,threshold=20,duration=2,post=0,cooldown=0)
            def sample(t,inode):
                value=self.sample_at(t,0)
                value['workloads']=[dict(id='/service',inode=inode,throttled_usec_per_s=300000)]
                return value
            capture.observe(sample(0,1));capture.observe(sample(1,1))
            self.assertIsNone(capture.observe(sample(2,2)))
            self.assertIsNone(capture.observe(sample(3,2)))
            event=capture.observe(sample(4,2))
            self.assertIn('quota /service inode=2',event['label'])
            capture.close()

    def test_missing_signal_and_sample_gaps_reset_incident_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            capture=IncidentCapture(directory,duration=2)
            capture.observe(self.sample_at(0));capture.observe(self.sample_at(1,None))
            self.assertIsNone(capture.observe(self.sample_at(2)))
            sample=self.sample_at(30);sample['elapsed_s']=28
            self.assertIsNone(capture.observe(sample))
            self.assertIsNone(capture.writer)

    def test_disk_apply_undo_preserves_other_devices(self):
        backend=FakeDisk();backend.set(TARGET,'read',{'/dev/sdb':500},[])
        session=DiskSession(backend)
        plan=session.prepare(TARGET,'sda','write','2M')
        event=session.apply(plan);self.assertEqual(event['status'],'confirmed')
        self.assertEqual(backend.state['read'],{'/dev/sdb':500})
        session.undo();self.assertEqual(backend.state['write'],{})
        self.assertEqual(backend.state['read'],{'/dev/sdb':500})

    def test_disk_stale_device_preview_and_external_change_refused(self):
        backend=FakeDisk();session=DiskSession(backend)
        plan=session.prepare(TARGET,'sda','write','2M')
        backend.dev['inode']=4
        with self.assertRaises(ControlError):session.apply(plan)
        backend.dev['inode']=3;session.apply(plan)
        backend.set(TARGET,'write',{'/dev/sda':30},[])
        with self.assertRaises(ControlError):session.undo()

    def test_disk_invalid_values_and_false_success(self):
        for value in ('0','-1','nan','1;reboot','3T'):
            with self.assertRaises(ControlError):bandwidth(value)
        self.assertEqual(bandwidth('2M'),2097152)
        self.assertIsNone(bandwidth('max'))
        backend=FakeDisk();session=DiskSession(backend)
        plan=session.prepare(TARGET,'sda','write','2M')
        with patch.object(backend,'set'),patch('resourcelens.disk_controls.time.monotonic',side_effect=[0,2]):
            with self.assertRaises(ControlError):session.apply(plan)
        self.assertEqual(session.events[-1]['status'],'unconfirmed')

    def test_disk_preview_cannot_apply_when_clipped(self):
        view=View(control=True)
        plan=DiskSession(FakeDisk()).prepare(TARGET,'sda','write','2M')
        view.modal=dict(stage='utility_disk_confirm',plan=plan)
        view.screen_size=(12,40)
        with patch.object(view,'job') as job:
            view.key(10)
            job.assert_not_called()
        self.assertEqual(view.modal['stage'],'utility_disk_confirm')
        view.key(27);self.assertIsNone(view.modal)

    def test_incident_rejects_invalid_thresholds(self):
        with tempfile.TemporaryDirectory() as directory:
            for threshold in (float('nan'),0,101):
                with self.assertRaises(ValueError):IncidentCapture(directory,threshold=threshold)

    def test_helper_recovers_receipt_after_interrupted_apply(self):
        backend=FakeDisk()
        class Interrupted(DiskSession):
            def apply(self,plan):
                super().apply(plan)
                raise SystemExit('simulate termination before response/save')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);policy=root/'policy';policy.write_text(json.dumps({'1000':['test.service']}))
            state=root/'state'
            with patch('resourcelens.helper.trusted',side_effect=Path):
                with patch('resourcelens.helper.DiskSession',side_effect=lambda:Interrupted(backend)):
                    plan=dispatch(dict(action='prepare',domain='disk',target=TARGET,args=['sda','write','2M']),1000,policy,state)['result']
                    with self.assertRaises(SystemExit):dispatch(dict(action='apply',token=plan['helper_token']),1000,policy,state)
                receipt=json.loads((state/plan['helper_token']).read_text())
                self.assertEqual(receipt['event']['status'],'unconfirmed')
                with patch('resourcelens.helper.DiskSession',side_effect=lambda:DiskSession(backend)):
                    result=dispatch(dict(action='undo',token=plan['helper_token']),1000,policy,state)
                self.assertEqual(result['event']['status'],'undone')
                self.assertEqual(backend.state['write'],{})

    def test_helper_rejects_writable_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'helper';path.write_text('')
            with self.assertRaises(ControlError):trusted(path)

    def test_helper_policy_receipts_and_owner_enforcement(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);policy=root/'policy';policy.write_text(json.dumps({'1000':['test.service']}))
            state=root/'state';backend=FakeDisk()
            with patch('resourcelens.helper.trusted',side_effect=Path),patch('resourcelens.helper.DiskSession',side_effect=lambda:DiskSession(backend)):
                call=lambda request,uid=1000:dispatch(request,uid,policy,state)
                request=dict(action='prepare',domain='disk',target=TARGET,args=['sda','write','2M'])
                plan=call(request)['result'];token=plan['helper_token']
                with self.assertRaises(ControlError):call(dict(action='apply',token=token),1001)
                result=call(dict(action='apply',token=token));self.assertEqual(result['event']['status'],'confirmed')
                self.assertIn('error',call(dict(action='apply',token=token)))
                result=call(dict(action='undo',token=token));self.assertEqual(result['event']['status'],'undone')
                with self.assertRaises(ControlError):call(dict(request,target=dict(TARGET,unit='other.service')))
                with self.assertRaises(ControlError):call(dict(action='shell',command='echo nope'))
                with self.assertRaises(ControlError):call(dict(action='apply',token='../policy'))


if __name__=='__main__':unittest.main()
