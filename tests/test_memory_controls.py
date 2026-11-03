from copy import deepcopy
from pathlib import Path
import subprocess
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from resourcelens.controls import ControlError
from resourcelens.memory_controls import MemoryBackend,MemoryHighSession,memory_value,INFINITY
from resourcelens.sources import ReadResult


MIB=1024**2


class FixtureManager:
    """Only mutates files in an owned TemporaryDirectory; never runs systemctl."""
    def __init__(self,root,service_path='/parent.slice/job.service'):
        self.root=Path(root);self.directory=self.root/service_path.lstrip('/')
        self.directory.mkdir(parents=True)
        (self.root/'cgroup.controllers').write_text('cpu memory io')
        for directory in (self.root,self.directory.parent,self.directory):
            for name,value in {'memory.high':'max','memory.max':str(1024*MIB),'memory.swap.max':'max',
                               'memory.current':str(64*MIB),'memory.swap.current':'0','memory.peak':str(64*MIB)}.items():
                (directory/name).write_text(value)
        (self.directory.parent/'memory.high').write_text(str(32*MIB))
        info=self.directory.stat()
        self.target=dict(unit=self.directory.name,path=service_path,inode=info.st_ino,device=info.st_dev,
                         manager='user',controllable=True,owner_uid=1000)
        self.values=dict(Id=self.directory.name,ControlGroup=self.target['path'],InvocationID='instance-a',ActiveState='active',
                         MemoryHigh=str(INFINITY),MemoryMax=str(1024*MIB),MemorySwapMax=str(INFINITY),
                         StartupMemoryHigh=str(INFINITY),StartupMemoryMax=str(INFINITY),
                         EffectiveMemoryHigh=str(32*MIB),EffectiveMemoryMax=str(1024*MIB))
        self.commands=[];self.writes=[];self.deny=False;self.timeout=False;self.manager_only=False;self.after_write=None
        self.backend=MemoryBackend(root=root,runner=self.run,page_size=4096)
        self.backend.binary='fixture-systemctl'

    def run(self,command,**kwargs):
        self.commands.append(command)
        if 'set-property' not in command:
            return SimpleNamespace(returncode=0,stdout='\n'.join(k+'='+v for k,v in self.values.items()),stderr='')
        setting=command[-1]
        if not setting.startswith('MemoryHigh='):raise AssertionError('Attempt to change another property: '+setting)
        value=setting.split('=',1)[1];self.writes.append(value)
        if self.deny:return SimpleNamespace(returncode=1,stdout='',stderr='Access denied')
        self.values['MemoryHigh']=str(INFINITY) if value=='infinity' else value
        if not self.manager_only:(self.directory/'memory.high').write_text('max' if value=='infinity' else value)
        if self.after_write:self.after_write()
        if self.timeout:raise subprocess.TimeoutExpired(command,5)
        return SimpleNamespace(returncode=0,stdout='',stderr='')


class MemoryControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.manager=FixtureManager(Path(self.tmp.name)/'cg')
        self.session=MemoryHighSession(self.manager.backend);self.target=self.manager.target

    def test_byte_parser_page_alignment_and_explicit_infinity(self):
        for text,value in (('1',4096),('1K',4096),('1MiB',MIB),('2G',2*1024*MIB),('64KiB',65536)):
            self.assertEqual(memory_value(text,4096)[0],value)
        self.assertEqual(memory_value('max',4096),(None,None))
        self.assertEqual(memory_value('65537',65536),(131072,65537))
        for text in ('0','-1','1.5G','50%','default','100; reboot','NaN',str(2**64),''):
            with self.assertRaises(ControlError):memory_value(text,4096)

    def test_prepare_reads_real_fixture_files_and_has_no_mutation(self):
        plan=self.session.prepare(self.target,'128M')
        self.assertEqual(plan['value'],128*MIB)
        self.assertEqual(plan['before']['observations']['memory_current'],64*MIB)
        self.assertEqual(plan['before']['ancestors'][0]['memory.high']['value'],32*MIB)
        self.assertEqual(self.manager.writes,[])
        self.assertTrue(all('--user' in c and '--no-ask-password' in c for c in self.manager.commands))

    def test_apply_verify_and_fresh_restore_preserve_other_settings_and_values(self):
        originals={key:value for key,value in self.manager.values.items() if key!='MemoryHigh'}
        event=self.session.apply(self.session.prepare(self.target,'128M'))
        self.assertEqual(event['status'],'confirmed');self.assertEqual(event['after']['kernel_high'],128*MIB)
        plan=self.session.prepare_restore(event['receipt_id'])
        self.assertTrue(plan['restoring']);self.assertIsNone(plan['value'])
        self.session.undo(plan)
        self.assertEqual(event['status'],'undone')
        self.assertEqual(self.manager.writes,[str(128*MIB),'infinity'])
        self.assertEqual({k:v for k,v in self.manager.values.items() if k!='MemoryHigh'},originals)
        self.assertEqual((self.manager.directory/'memory.max').read_text(),str(1024*MIB))

    def test_below_usage_is_reviewed_and_new_crossing_refuses_apply(self):
        plan=self.session.prepare(self.target,'32M');self.assertTrue(plan['below_usage'])
        plan=self.session.prepare(self.target,'128M');self.assertFalse(plan['below_usage'])
        (self.manager.directory/'memory.current').write_text(str(256*MIB))
        with self.assertRaisesRegex(ControlError,'crossed'):self.session.apply(plan)
        self.assertEqual(self.manager.writes,[])

    def test_stale_manager_kernel_ancestor_and_lifetime_previews_never_write(self):
        for change in ('manager','native','ancestor','invocation','device','hard','startup','swap'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                manager=FixtureManager(Path(directory)/'cg');session=MemoryHighSession(manager.backend)
                plan=session.prepare(manager.target,'128M')
                if change=='manager':manager.values['MemoryHigh']=str(256*MIB)
                elif change=='native':(manager.directory/'memory.high').write_text(str(256*MIB))
                elif change=='ancestor':(manager.directory.parent/'memory.high').write_text(str(16*MIB))
                elif change=='invocation':manager.values['InvocationID']='instance-b'
                elif change=='device':plan['target']['device']+=1
                elif change=='hard':manager.values['MemoryMax']=str(512*MIB)
                elif change=='startup':manager.values['StartupMemoryHigh']=str(512*MIB)
                else:(manager.directory/'memory.swap.max').write_text(str(16*MIB))
                with self.assertRaises(ControlError):session.apply(plan)
                self.assertEqual(manager.writes,[])

    def test_untracked_manager_kernel_mismatch_and_missing_usage_are_explicit(self):
        (self.manager.directory/'memory.high').write_text(str(MIB))
        with self.assertRaisesRegex(ControlError,'disagree'):self.session.prepare(self.target,'128M')
        (self.manager.directory/'memory.high').write_text('max')
        (self.manager.directory/'memory.current').write_text('bad')
        with self.assertRaisesRegex(ControlError,'usage is unavailable'):self.session.prepare(self.target,'128M')
        self.assertEqual(self.manager.writes,[])

    def test_missing_controller_property_and_permission_are_not_unlimited(self):
        original=self.manager.values.pop('MemoryHigh')
        with self.assertRaisesRegex(ControlError,'unavailable'):self.session.prepare(self.target,'128M')
        self.manager.values['MemoryHigh']=original
        original_read=__import__('resourcelens.memory_controls',fromlist=['read_text']).read_text
        def denied(path,**kwargs):
            if Path(path)==self.manager.directory/'memory.high':return ReadResult(None,'permission_denied')
            return original_read(path,**kwargs)
        with patch('resourcelens.memory_controls.read_text',side_effect=denied):
            with self.assertRaisesRegex(ControlError,'permission_denied'):self.session.prepare(self.target,'128M')
        (self.manager.root/'cgroup.controllers').write_text('cpu io')
        with self.assertRaisesRegex(ControlError,'controller is unavailable'):self.session.prepare(self.target,'128M')

    def test_write_denial_is_unconfirmed_and_can_be_cleared_without_another_write(self):
        plan=self.session.prepare(self.target,'128M');self.manager.deny=True
        with self.assertRaisesRegex(ControlError,'denied'):self.session.apply(plan)
        event=self.session.events[-1];self.assertEqual(event['status'],'unconfirmed')
        self.session.undo(self.session.prepare_restore(event['receipt_id']))
        self.assertEqual(event['status'],'undone');self.assertEqual(len(self.manager.writes),1)

    def test_timeout_after_write_does_not_claim_no_change_and_can_restore(self):
        plan=self.session.prepare(self.target,'128M');self.manager.timeout=True
        with self.assertRaisesRegex(ControlError,'may have changed'):self.session.apply(plan)
        event=self.session.events[-1];self.assertEqual(event['status'],'unconfirmed')
        self.assertEqual((self.manager.directory/'memory.high').read_text(),str(128*MIB))
        self.manager.timeout=False
        self.session.undo(self.session.prepare_restore(event['receipt_id']))
        self.assertEqual(event['status'],'undone')

    def test_false_success_and_manager_only_partial_state_have_guarded_recovery(self):
        self.manager.manager_only=True
        with self.assertRaisesRegex(ControlError,'not confirmed'):self.session.apply(self.session.prepare(self.target,'128M'))
        event=self.session.events[-1];self.assertEqual(event['status'],'unconfirmed')
        self.manager.manager_only=False
        self.session.undo(self.session.prepare_restore(event['receipt_id']))
        self.assertEqual(event['status'],'undone')

    def test_changes_during_verification_remain_unconfirmed(self):
        self.manager.after_write=lambda:self.manager.values.update(InvocationID='instance-b')
        with self.assertRaisesRegex(ControlError,'changed during|restarted'):self.session.apply(self.session.prepare(self.target,'128M'))
        self.assertEqual(self.session.events[-1]['status'],'unconfirmed')

    def test_restore_never_overwrites_external_high_hard_or_ancestor_changes(self):
        for change in ('high','hard','ancestor','restart'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                manager=FixtureManager(Path(directory)/'cg');session=MemoryHighSession(manager.backend)
                event=session.apply(session.prepare(manager.target,'128M'))
                if change=='high':manager.values['MemoryHigh']=str(256*MIB);(manager.directory/'memory.high').write_text(str(256*MIB))
                elif change=='hard':manager.values['MemoryMax']=str(512*MIB)
                elif change=='ancestor':(manager.directory.parent/'memory.high').write_text(str(16*MIB))
                else:manager.values['InvocationID']='instance-b'
                with self.assertRaises(ControlError):session.prepare_restore(event['receipt_id'])
                self.assertEqual(len(manager.writes),1)

    def test_lifo_and_stale_restore_previews_cannot_retarget(self):
        first=self.session.apply(self.session.prepare(self.target,'128M'))
        second=self.session.apply(self.session.prepare(self.target,'256M'))
        with self.assertRaisesRegex(ControlError,'newer'):self.session.prepare_restore(first['receipt_id'])
        plan=self.session.prepare_restore(second['receipt_id'])
        self.manager.values['MemoryHigh']=str(512*MIB)
        with self.assertRaises(ControlError):self.session.undo(plan)
        self.assertEqual(len(self.manager.writes),2)

    def test_prior_page_rounded_setting_is_restored_exactly(self):
        self.manager.values['MemoryHigh']='1';(self.manager.directory/'memory.high').write_text('4096')
        event=self.session.apply(self.session.prepare(self.target,'128M'))
        self.manager.after_write=lambda:(self.manager.directory/'memory.high').write_text('4096')
        self.session.undo(self.session.prepare_restore(event['receipt_id']))
        self.assertEqual(event['restored']['memory_high'],1)
        self.assertEqual(event['restored']['kernel_high'],4096)

    def test_other_properties_and_non_aligned_forged_requests_are_refused(self):
        plan=self.session.prepare(self.target,'128M');plan['property']='memory_max'
        with self.assertRaises(ControlError):self.session.apply(plan)
        plan['property']='memory_high';plan['value']=1
        with self.assertRaises(ControlError):self.session.apply(plan)
        self.assertEqual(self.manager.writes,[])

    def test_existing_privileged_helper_does_not_authorize_memory_controls(self):
        from resourcelens.helper import dispatch
        policy=Path(self.tmp.name)/'policy';policy.write_text(json.dumps({'1000':['job.service']}))
        target=dict(self.target,manager='system')
        with patch('resourcelens.helper.trusted',side_effect=Path),patch.object(self.manager.backend,'snapshot') as read:
            with self.assertRaises(ControlError):dispatch(dict(action='prepare',domain='memory',target=target,args=['128M']),1000,policy,Path(self.tmp.name)/'state')
            read.assert_not_called()


if __name__=='__main__':unittest.main()
