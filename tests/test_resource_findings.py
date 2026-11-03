import json
from pathlib import Path
import tempfile
import unittest

from resourcelens.diagnostics import DiagnosticHistory,explain
from resourcelens.incidents import IncidentCapture
from resourcelens.recording import Replay
from test_diagnostics import snapshot,REQUEST


def resource_sample(index,psi=30,events=0,local=True):
    sample=snapshot(index,local_delta=0)
    sample['pressure']={}
    for offset,node in enumerate(sample['target_context']['ancestors']):
        for resource in ('cpu','memory','io'):
            name=resource+'.pressure'
            node['files'][name]=dict(value=f'some avg10={psi} avg60=0 avg300=0 total={index*100}',reason=None)
            node['counters'][name]={'some':dict(avg10=psi,total=index*100)}
        node['memory_events_scope']='subtree'
        for name in ('memory.events.local','memory.events'):
            node['files'][name]=dict(value=f'high {index*events}',reason=None if local or name=='memory.events' else 'permission_denied')
            node['counters'][name]=dict(high=index*events,max=0,oom=0,oom_kill=0)
            node['deltas'][name]=dict(high=events if index else None,max=0,oom=0,oom_kill=0)
    return sample


class ResourceFindingTests(unittest.TestCase):
    def history(self,count=4,**kwargs):
        history=DiagnosticHistory()
        for i in range(count):history.observe(resource_sample(i,**kwargs))
        return history,resource_sample(count-1,**kwargs)

    def test_scoped_cpu_memory_io_pressure_are_persistent_and_have_units(self):
        history,sample=self.history()
        findings=explain(history,sample,REQUEST)
        for resource in ('cpu','memory','io'):
            finding=next(f for f in findings if f.rule_id=='local_'+resource+'_pressure')
            self.assertEqual(finding.window['covered_seconds'],3)
            self.assertEqual(finding.window['unit'],'PSI some.avg10 percent')
            self.assertEqual(finding.evidence[0]['file'],resource+'.pressure')
            self.assertIn('not per-process',' '.join(finding.limitations))
            ancestor=next(f for f in findings if f.rule_id=='ancestor_'+resource+'_pressure')
            self.assertEqual(ancestor.relevance,'possible')

    def test_host_pressure_does_not_imply_selected_target_pressure(self):
        history=DiagnosticHistory()
        for i in range(4):
            sample=snapshot(i,local_delta=0);sample['pressure']={'cpu':{'some':{'avg10':99}}};history.observe(sample)
        self.assertFalse(any('pressure' in f.rule_id for f in explain(history,sample,REQUEST)))

    def test_duplicate_missing_stale_and_short_observations_do_not_become_persistent(self):
        history,sample=self.history(count=2)
        for _ in range(20):history.observe(sample)
        self.assertFalse(any(f.rule_id=='local_io_pressure' for f in explain(history,sample,REQUEST)))
        history,sample=self.history()
        self.assertFalse(any('pressure' in f.rule_id for f in explain(history,sample,REQUEST,now=200)))
        sample['target_context']['ancestors'][0]['files']['io.pressure']['reason']='permission_denied'
        history.observe(sample)
        self.assertFalse(any(f.rule_id=='local_io_pressure' for f in explain(history,sample,REQUEST)))

    def test_gaps_group_generation_movement_and_scope_break_pressure_runs(self):
        for change in ('gap','generation','scope','moved'):
            history,sample=self.history(count=3)
            sample=resource_sample(3)
            if change=='gap':sample=resource_sample(8)
            elif change=='scope':sample['collection_scope']['id']='other'
            elif change=='moved':sample['processes'][0]['cgroup']='/moved'
            else:
                for identity in (sample['target_context']['selected_group'],sample['target_context']['ancestors'][0]['identity']):identity['identity']['generation']=1
            history.observe(sample)
            self.assertFalse(any(f.rule_id=='local_io_pressure' for f in explain(history,sample,REQUEST)),change)

    def test_memory_counter_increase_keeps_local_subtree_and_reset_semantics(self):
        for local,scope in ((True,'local'),(False,'subtree')):
            history,sample=self.history(count=2,psi=0,events=2,local=local)
            finding=next(f for f in explain(history,sample,REQUEST) if f.rule_id=='local_memory_high')
            self.assertEqual(finding.window['events_delta'],2)
            self.assertEqual(finding.evidence[0]['accounting_scope'],scope)
            self.assertEqual(finding.evidence[0]['before'],0)
            self.assertEqual(finding.evidence[0]['after'],2)
            filename=finding.evidence[0]['file']
            sample['target_context']['ancestors'][0]['interval_s']=10
            self.assertTrue(any(f.rule_id=='local_memory_high' for f in explain(history,sample,REQUEST)))
            sample['target_context']['ancestors'][0]['deltas'][filename]['high']=None
            self.assertFalse(any(f.rule_id=='local_memory_high' for f in explain(history,sample,REQUEST)))

    def test_memory_rules_do_not_require_successful_cpu_quota_parsing(self):
        history,sample=self.history(count=2,events=1)
        sample['target_context']['ancestors'][0]['limits']['cpu.max']=[]
        findings=explain(history,sample,REQUEST)
        self.assertTrue(any(f.rule_id=='insufficient_evidence' for f in findings))
        self.assertTrue(any(f.rule_id=='local_memory_high' for f in findings))

    def test_memory_finding_identity_includes_source_cgroup_lifetime(self):
        history,sample=self.history(count=2,events=1)
        first=next(f for f in explain(history,sample,REQUEST) if f.rule_id=='local_memory_high')
        for ref in (sample['target_context']['selected_group'],sample['target_context']['ancestors'][0]['identity']):ref['identity']['generation']=1
        second=next(f for f in explain(history,sample,REQUEST) if f.rule_id=='local_memory_high')
        self.assertNotEqual(first.id,second.id)

    def test_incident_captures_versioned_findings_and_replay_reproduces_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            capture=IncidentCapture(directory,duration=3,post=1,cooldown=10)
            for i in range(3):self.assertIsNone(capture.observe(resource_sample(i)))
            event=capture.observe(resource_sample(3))
            self.assertEqual(event['unit'],'PSI some.avg10 percent')
            self.assertEqual(event['findings'][0]['rule_version'],1)
            capture.observe(resource_sample(4));capture.close()
            replay=Replay(next(Path(directory).glob('*.jsonl')))
            try:
                history=DiagnosticHistory()
                for sample in replay.window(3):history.observe(sample)
                sample=replay.sample_at(3)
                findings=[f.to_dict() for f in explain(history,sample,REQUEST)]
                recorded=replay.events[0]['findings'][0]
                matching=next(f for f in findings if f['id']==recorded['id'])
                self.assertEqual(json.loads(json.dumps(matching)),recorded)
            finally:replay.close()

    def test_cached_memory_incident_is_deduplicated_and_file_budget_never_deletes(self):
        with tempfile.TemporaryDirectory() as directory:
            capture=IncidentCapture(directory,post=0,cooldown=0,max_files=1)
            capture.observe(resource_sample(0,psi=0,events=1))
            sample=resource_sample(1,psi=0,events=1)
            event=capture.observe(sample);self.assertIsNotNone(event)
            capture.observe(sample)
            self.assertIsNone(capture.observe(sample))
            self.assertEqual(len(list(Path(directory).glob('*.jsonl'))),1)
            self.assertIsNone(capture.observe(resource_sample(2,psi=0,events=2)))
            self.assertIn('budget reached',capture.status);capture.close()

    def test_resource_history_is_bounded_and_missing_frames_break_runs(self):
        history=DiagnosticHistory(max_frames=2,max_bytes=4096)
        for i in range(20):history.observe(resource_sample(i))
        self.assertLessEqual(len(history.resources.frames),2)
        self.assertLessEqual(history.resources.bytes,4096)
        history.observe({})
        self.assertEqual(history.resources.frames[-1]['nodes'],[])


if __name__=='__main__':unittest.main()
