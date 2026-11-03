from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from resourcelens.diagnostics import context_for
from resourcelens.memory_observation import memory_lines,scalar,boundary
from resourcelens.targets import TargetResolver
import test_targets as fixtures


class MemoryObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'cg';self.root.mkdir()
        self.proc=Path(self.tmp.name)/'proc';self.proc.mkdir()
        self.clock=[10.]
        self.mount=dict(root='/',ancestry_visibility='to_system_root',memory_events_scope='subtree')
        self.resolver=TargetResolver(self.proc,self.root,'scope',mount=self.mount,clock=lambda:self.clock[0])

    def group(self,path,**changes):
        directory=self.root/path.lstrip('/');directory.mkdir(parents=True,exist_ok=True)
        for name,value in {**fixtures.DEFAULTS,**changes}.items():
            if value is not None:(directory/name).write_text(value)
        return directory

    def resolve(self,directory):
        info=directory.stat();path='/'+str(directory.relative_to(self.root))
        return self.resolver.resolve(('cgroup',path,info.st_ino,info.st_dev)).value

    def test_exact_memory_zero_swap_peak_and_categories_keep_scope(self):
        self.group('/')
        directory=self.group('/demo.service',**{'memory.current':'0','memory.swap.current':'0','memory.peak':'4096',
            'memory.high':'8192','memory.max':'16384','memory.stat':'anon 0\nfile 1024\nshmem 512\nkernel 1024\nslab 512\n'})
        context=self.resolve(directory);node=context['ancestors'][0]
        self.assertEqual(node['counters']['memory.current'],0)
        self.assertEqual(node['counters']['memory.swap.current'],0)
        self.assertEqual(node['counters']['memory.peak'],4096)
        text='\n'.join(memory_lines(context,context['observed_at_wall']))
        self.assertIn('0 bytes',text);self.assertIn('Categories overlap',text)
        self.assertIn('not process RSS or PSS',text);self.assertIn('cgroup-reported peak',text)
        self.assertIn('local accounting',text);self.assertIn('subtree accounting',text)

    def test_missing_and_malformed_memory_sources_never_become_zero_or_unlimited(self):
        self.group('/')
        directory=self.group('/demo.service',**{'memory.swap.current':None,'memory.peak':'bad','memory.high':None})
        context=self.resolve(directory);node=context['ancestors'][0]
        self.assertEqual(scalar(node,'memory.swap.current')['state'],'unavailable')
        self.assertEqual(scalar(node,'memory.peak')['reason'],'parse_error')
        self.assertEqual(boundary(node,'memory.high')['state'],'unavailable')
        text='\n'.join(memory_lines(context))
        self.assertIn('memory.high: unknown',text);self.assertIn('memory.swap.current: unknown',text)
        self.assertIn('memory.peak: unknown (parse error)',text)

    def test_high_max_and_swap_summaries_are_recomputed_for_ancestor_navigation(self):
        self.group('/')
        parent=self.group('/demo.service',**{'memory.high':'32768','memory.max':'65536','memory.swap.max':'8192'})
        child=self.group('/demo.service/jobs',**{'memory.high':'4096','memory.max':'16384','memory.swap.max':'4096'})
        context=self.resolve(child)
        self.assertEqual(context['limits']['memory_high'],4096)
        info=parent.stat();request=('cgroup','/demo.service',info.st_ino,info.st_dev)
        adapted=context_for({'target_context':context},request)
        self.assertEqual(adapted['limits']['memory_high'],32768)
        self.assertEqual(adapted['limits']['memory_max'],65536)
        self.assertEqual(adapted['limits']['swap_max'],8192)

    def test_event_intervals_resets_and_stale_source_age_are_visible(self):
        self.group('/');directory=self.group('/demo.service')
        self.resolve(directory);self.clock[0]+=1
        (directory/'memory.events.local').write_text('high 2\nmax 0\noom 0\noom_kill 0\n')
        context=self.resolve(directory)
        text='\n'.join(memory_lines(context,context['observed_at_wall']+10))
        self.assertIn('+2 in 1.000s',text);self.assertIn('(stale)',text)
        self.clock[0]+=1;(directory/'memory.events.local').write_text('high 0\n')
        text='\n'.join(memory_lines(self.resolve(directory)))
        self.assertIn('interval unavailable',text)

    def test_legacy_recorded_raw_scalars_remain_readable_offline(self):
        self.group('/');context=self.resolve(self.group('/demo.service'))
        for name in ('memory.current','memory.swap.current','memory.peak'):context['ancestors'][0]['counters'].pop(name)
        self.assertEqual(scalar(context['ancestors'][0],'memory.current')['value'],0)
        self.assertIn('memory.high: max','\n'.join(memory_lines(context)))


if __name__=='__main__':unittest.main()
