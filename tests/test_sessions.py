from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from resourcelens.evidence import decode,read_json,load_profile,profile_runs
from resourcelens.recording import Recorder,Replay,RecordingError
from resourcelens.redaction import redact,export_summary
from resourcelens.sessions import discover,entry,import_path,open_entry,add_outcome,profile_lines,presentation_sample
from resourcelens.profiling import compare_runs
from test_diagnostics import snapshot


def saved_profile(root,name='run',value=10,status='complete',cgroup=None):
    path=Path(root)/name;path.mkdir(parents=True,exist_ok=True)
    record=dict(schema=1,label='build-test',target=dict(pid=None if cgroup else 42,cgroup=cgroup,identity=123),
                started=100,requested_seconds=5,elapsed_seconds=5,status=status,
                samples=[dict(elapsed=0),dict(elapsed=5)],metrics={'cpu_percent':value,'rss_end_bytes':32},
                environment={'kernel':'fixture','machine':'fixture-arch'},profile={'status':'disabled'},
                scheduler={'status':'disabled'},energy={'status':'disabled'},warnings=[],outcomes={})
    (path/'session.json').write_text(json.dumps(record))
    return path,record


def saved_recording(path,count=4):
    writer=Recorder(path)
    for i in range(count):writer.write('sample',snapshot(i))
    writer.close();return path


class SessionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_bounded_default_discovery_does_not_scan_arbitrary_home(self):
        profile,_=saved_profile(self.root/'profiles')
        recording=self.root/'recordings/demo.jsonl';recording.parent.mkdir();saved_recording(recording)
        hidden,_=saved_profile(self.root/'unrelated/deep')
        result=discover(self.root)
        self.assertEqual({r['path'] for r in result['entries']},{str(profile/'session.json'),str(recording)})
        with patch('resourcelens.sessions.MAX_ENTRIES',1):
            result=discover(self.root);self.assertEqual(len(result['entries']),1)
            self.assertIn('truncated',' '.join(result['warnings']))
        self.assertTrue((hidden/'session.json').exists())

    def test_import_retains_original_and_writes_private_rebuildable_path_index(self):
        path=saved_recording(self.root/'external-evidence')
        original=path.read_bytes()
        row=import_path(path,self.root/'state')
        self.assertEqual(row['kind'],'recording');self.assertEqual(path.read_bytes(),original)
        index=self.root/'state/session-paths.json'
        self.assertEqual(index.stat().st_mode&0o777,0o600)
        self.assertIn(str(path),index.read_text())
        self.assertEqual(discover(self.root/'state')['entries'][0]['path'],str(path))

    def test_invalid_import_does_not_remember_path_or_execute_content(self):
        path=self.root/'bad.jsonl';path.write_text('{"type":"header","format":"resource-lens","version":99}\n')
        with self.assertRaises(ValueError):import_path(path,self.root/'state')
        self.assertFalse((self.root/'state/session-paths.json').exists())
        for text in ('{"metrics":NaN}','{"value":Infinity}','{"schema":1,"schema":2}',
                     '{"value":'+'9'*400+'}', '['*40+'0'+']'*40):
            with self.assertRaises(ValueError):decode(text)

    def test_symlink_devices_oversize_and_replaced_artifact_are_rejected(self):
        profile,record=saved_profile(self.root)
        row=entry(profile);file=profile/'session.json'
        link=self.root/'link.jsonl';link.symlink_to(file)
        with self.assertRaises(ValueError):entry(link)
        with self.assertRaises((OSError,ValueError)):read_json(link)
        with self.assertRaises(ValueError):read_json(file,limit=2)
        record['metrics']['cpu_percent']=20;file.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError,'changed'):open_entry(row)

    def test_nested_malformed_recording_and_changed_after_import_are_rejected(self):
        for change in ('cpu','pressure','hardware','context','nan'):
            path=self.root/(change+'.jsonl');sample=snapshot(3)
            if change=='cpu':sample['cpu'][0]['busy_pct']={}
            elif change=='pressure':sample['pressure']['cpu']['some']='bad'
            elif change=='hardware':sample['cpu_hardware']={'cpus':'bad'}
            elif change=='context':sample['target_context']['ancestors'][0]['counters']=[]
            else:sample['memory']['used']=float('nan')
            path.write_text(json.dumps(dict(type='header',format='resource-lens',version=1))+'\n'+json.dumps({'type':'sample','sample':sample})+'\n')
            with self.assertRaises(ValueError):Replay(path)
        path=saved_recording(self.root/'valid.jsonl');replay=Replay(path)
        self.addCleanup(replay.close)
        with path.open('a') as file:file.write('\n')
        with self.assertRaisesRegex(ValueError,'changed'):replay.sample_at(0)

    def test_profile_status_metrics_energy_and_units_are_validated(self):
        path,record=saved_profile(self.root)
        for key,value in (('schema',True),('metrics',{'cpu':None}),('target',{'pid':42,'cgroup':'/x'}),
                          ('energy',{'status':'complete','domains':['bad']}),('status','made-up')):
            broken=deepcopy(record);broken[key]=value;(path/'session.json').write_text(json.dumps(broken))
            with self.assertRaises(ValueError):load_profile(path)

    def test_outcomes_are_private_sidecars_with_units_provenance_and_window(self):
        path,record=saved_profile(self.root);row=entry(path);original=(path/'session.json').read_bytes()
        result=add_outcome(row,'p99 | ms | 12.5 | load generator log')
        self.assertEqual(result['outcomes']['p99'],12.5)
        self.assertEqual(result['outcome_metadata']['p99']['window'],[100,105])
        self.assertEqual((path/'outcomes.json').stat().st_mode&0o777,0o600)
        self.assertEqual((path/'session.json').read_bytes(),original)
        self.assertIn('load generator log',' '.join(profile_lines(result)))
        record['label']='changed';(path/'session.json').write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError,'changed profile'):load_profile(path)

    def test_partial_captures_remain_visible_but_cannot_supply_matched_runs(self):
        path,record=saved_profile(self.root,status='partial')
        self.assertIn('Capture: partial',' '.join(profile_lines(load_profile(path))))
        with self.assertRaisesRegex(ValueError,'complete'):compare_runs([record],[record])
        with self.assertRaisesRegex(ValueError,'complete'):add_outcome(entry(path),'p99 | ms | 2 | log')

    def test_repeat_statistics_scope_and_outcome_unit_compatibility(self):
        a=[];b=[]
        for i in range(3):
            _,record=saved_profile(self.root,'a'+str(i),value=10+i);a.append(record)
            _,record=saved_profile(self.root,'b'+str(i),value=20+i);b.append(record)
        result=compare_runs(a,b)
        self.assertEqual(result['metrics']['cpu_percent']['baseline']['count'],3)
        self.assertEqual(result['metrics']['cpu_percent']['baseline']['stdev'],1)
        group=deepcopy(b[0]);group['target']={'pid':None,'cgroup':'/group','identity':2}
        with self.assertRaisesRegex(ValueError,'scope'):compare_runs([a[0]],[group])
        for record,unit in ((a[0],'ms'),(b[0],'seconds')):
            record['outcomes']={'p99':2};record['outcome_metadata']={'p99':dict(unit=unit,provenance='log')}
        result=compare_runs([a[0]],[b[0]])
        self.assertNotIn('outcome.p99',result['metrics'])
        self.assertIn('units differ',' '.join(result['warnings']))

    def test_original_findings_and_rule_versions_survive_profile_reopen(self):
        path,_=saved_profile(self.root)
        context=dict(schema=1,findings=[dict(title='Recorded finding',rule_id='old-rule',rule_version=7)])
        (path/'capture-context.json').write_text(json.dumps(context))
        result=load_profile(path)
        self.assertEqual(result['capture_context'],context)
        self.assertIn('version 7',' '.join(profile_lines(result)))

    def test_redaction_keeps_alias_relationships_units_and_numeric_measurements(self):
        value=dict(label='private-build',target={'path':'/private/service','pid':42,'identity':123},
                   findings=[dict(evidence=[dict(path='/private/service')],explanation='At /private/service CPU pressure')],
                   metrics={'cpu_percent':42},status='complete',outcome_metadata={'secret-work':dict(unit='ms',provenance='/private/log')})
        clean=redact(value)
        text=json.dumps(clean)
        for secret in ('private-build','/private','secret-work'):self.assertNotIn(secret,text)
        self.assertEqual(clean['metrics']['cpu_percent'],42)
        self.assertEqual(clean['status'],'complete')
        self.assertEqual(clean['target']['path'],clean['findings'][0]['evidence'][0]['path'])
        self.assertIn('ms',text)
        output=Path(export_summary(value,self.root/'reports'))
        self.assertEqual(output.stat().st_mode&0o777,0o600)
        self.assertNotIn('/private',output.read_text())

    def test_damaged_index_does_not_hide_default_artifacts(self):
        path,_=saved_profile(self.root/'profiles')
        (self.root/'session-paths.json').write_text('{"schema":99}')
        result=discover(self.root)
        self.assertIn(str(path/'session.json'),[e['path'] for e in result['entries']])
        self.assertTrue(result['warnings'])

    def test_replay_window_has_a_byte_budget_without_dropping_original_samples(self):
        path=saved_recording(self.root/'record.jsonl',count=4);replay=Replay(path);self.addCleanup(replay.close)
        window=replay.window(3,max_bytes=replay.sizes[-1]+1)
        self.assertEqual(len(window),1);self.assertTrue(replay.window_truncated)
        self.assertEqual(len(replay.offsets),4)

    def test_interrupted_metadata_or_export_preserves_originals_and_leaves_no_partial_summary(self):
        path,_=saved_profile(self.root);row=entry(path)
        add_outcome(row,'p99 | ms | 12 | log')
        sidecar=path/'outcomes.json';before=sidecar.read_bytes()
        with patch('resourcelens.sessions.os.replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):add_outcome(row,'p99 | ms | 10 | log')
        self.assertEqual(sidecar.read_bytes(),before)
        self.assertEqual(list(path.glob('.outcomes.json-*')),[])
        with patch('resourcelens.redaction.os.link',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):export_summary({'metrics':{'cpu':1}},self.root/'reports')
        self.assertEqual(list((self.root/'reports').iterdir()),[])

    def test_same_timestamp_same_size_record_rewrite_is_detected_by_content(self):
        path=saved_recording(self.root/'record.jsonl');replay=Replay(path);self.addCleanup(replay.close)
        data=path.read_bytes();self.assertIn(b'"avg10":99',data)
        path.write_bytes(data.replace(b'"avg10":99',b'"avg10":98',1))
        os.utime(path,ns=(path.stat().st_atime_ns,replay.identity[3]))
        with self.assertRaisesRegex(ValueError,'content changed'):replay.sample_at(0)


if __name__=='__main__':unittest.main()
