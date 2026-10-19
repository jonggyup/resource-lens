import curses
from pathlib import Path
import tempfile
import unittest
from resourcelens.cgroup_browser import CgroupBrowser
from resourcelens.workloads import WorkloadCollector
from resourcelens.ui import View, draw
from test_navigation import Screen
from resourcelens.collect import Collector
from test_workspaces import group


class HierarchyTests(unittest.TestCase):
    def test_empty_children_exact_paths_bounded_scan_and_rates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for path in (root,root/'demo.service',root/'demo.service/child',root/'empty'):
                path.mkdir(exist_ok=True)
                (path/'cpu.stat').write_text('usage_usec 1000')
                (path/'memory.current').write_text('42')
            (root/'escape').symlink_to('/tmp',target_is_directory=True)
            collector=WorkloadCollector(root)
            first=collector.hierarchy(1)
            self.assertEqual({n['path'] for n in first['nodes']},{'/','/demo.service','/demo.service/child','/empty'})
            (root/'demo.service/child/cpu.stat').write_text('usage_usec 501000')
            child=next(n for n in collector.hierarchy(1)['nodes'] if n['path'].endswith('/child'))
            self.assertEqual(child['cpu_pct'],50)
            self.assertEqual(child['memory_current'],42)
            limited=collector.hierarchy(1,limit=2)
            self.assertLessEqual(len(limited['nodes']),2)
            self.assertTrue(limited['truncated'])


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.sample=Collector(nvidia=False).sample()
        self.sample['cgroup_tree']={'nodes':[{'path':p,'inode':i,'cpu_pct':0,'memory_current':10}
                                           for i,p in enumerate(('/','/demo.service','/demo.service/child','/empty'))],
                                    'truncated':False,'errors':0}
        self.sample['processes']=[dict(pid=10,starttime=20,ppid=1,name='parent',state='S',cpu_pct=0,memory=20,cgroup='/demo.service'),
                                  dict(pid=11,starttime=30,ppid=10,name='child',state='S',cpu_pct=0,memory=30,cgroup='/demo.service/child')]
        self.sample['workloads']=[group('demo.service')]
        self.browser=CgroupBrowser(self.sample,'/demo.service')

    def test_direct_members_children_and_back_navigation(self):
        rows=self.browser.rows(self.sample)
        self.assertEqual([(r[0],r[1]) for r in rows],[('group','/demo.service/child'),('process',10)])
        self.browser.key(10,self.sample,24)
        self.assertEqual(self.browser.path,'/demo.service/child')
        self.browser.key(10,self.sample,24)
        self.assertEqual(self.browser.process,(11,30))
        self.browser.key(27,self.sample,24)
        self.assertIsNone(self.browser.process)
        self.browser.key(curses.KEY_LEFT,self.sample,24)
        self.assertEqual(self.browser.path,'/demo.service')
        self.assertFalse(self.browser.key(27,self.sample,24))

    def test_identity_replacement_and_process_exit_never_reattach(self):
        self.browser.selected=('process',10,20)
        self.browser.enter(self.sample)
        self.sample['processes'][0]['starttime']=21
        screen=Screen(24,100);self.browser.draw(screen,self.sample,100,24)
        self.assertIn('PID was reused',' '.join(screen.lines.values()))
        self.sample['cgroup_tree']['nodes'][1]['inode']=99
        self.assertEqual(self.browser.rows(self.sample),[])
        self.browser.draw(screen,self.sample,100,24)
        self.assertIn('was replaced',' '.join(screen.lines.values()))

    def test_click_opens_child_and_parent_button_returns(self):
        screen=Screen(24,100);self.browser.draw(screen,self.sample,100,24)
        start,end,y,identity=self.browser.regions[0]
        self.browser.mouse(start,y,curses.BUTTON1_CLICKED,self.sample,24)
        self.assertEqual(self.browser.path,'/demo.service/child')
        self.browser.draw(screen,self.sample,100,24)
        self.browser.mouse(2,2,curses.BUTTON1_CLICKED,self.sample,24)
        self.assertEqual(self.browser.path,'/demo.service')

    def test_empty_and_legacy_recordings_are_explicit(self):
        self.browser=CgroupBrowser(self.sample,'/empty')
        screen=Screen(24,100);self.browser.draw(screen,self.sample,100,24)
        self.assertIn('No visible child',' '.join(screen.lines.values()))
        del self.sample['cgroup_tree']
        self.browser.draw(screen,self.sample,100,24)
        self.assertIn('no cgroup hierarchy',' '.join(screen.lines.values()))

    def test_enter_and_right_open_groups_even_from_metrics_or_replay(self):
        for key in (10,13,curses.KEY_RIGHT):
            view=View();view.update(self.sample);view.page=7;view.application_mode='cgroups'
            view.workload_detail=True
            view.replay=object()
            view.key(key)
            self.assertEqual(view.browser.path,'/demo.service')
            view.key(key)
            self.assertEqual(view.browser.path,'/demo.service/child')
            view.key(key)
            self.assertEqual(view.browser.process,(11,30))
            view.key(27);self.assertIsNone(view.browser.process)
            view.key(27);self.assertEqual(view.browser.path,'/demo.service')
            self.assertEqual(view.browser.selected,('group','/demo.service/child',2))
            view.key(27);self.assertIsNone(view.browser)

    def test_metrics_focus_uses_d_and_narrow_tabs_without_enter(self):
        view=View();view.update(self.sample);view.page=7;view.application_mode='cgroups'
        view.key(ord('D'));self.assertTrue(view.workload_detail)
        self.assertIsNone(view.browser)
        view.key(27);self.assertFalse(view.workload_detail)
        view.screen_size=(24,80)
        view.key(ord(']'));self.assertTrue(view.workload_detail)
        self.assertEqual(view.app_tab,1)
        self.assertIsNone(view.browser)

    def test_view_routes_browser_without_controls_or_replay_seeking(self):
        view=View(control=True);view.update(self.sample);view.page=7;view.application_mode='cgroups'
        view.key(ord('T'));self.assertEqual(view.browser.path,'/')
        view.replay=object()
        view.key(curses.KEY_RIGHT);self.assertEqual(view.browser.path,'/demo.service')
        view.key(ord('w'));self.assertIsNone(view.modal)
        view.key(27);self.assertEqual(view.browser.path,'/')
        view.key(27);self.assertIsNone(view.browser)
        self.assertEqual(view.page,7)
        view.replay=None
        view.key(ord('T'));draw(Screen(24,100),view,1)
        start,end,key=view.action_regions[0]
        view.mouse(start,23,curses.BUTTON1_CLICKED)
        self.assertIsNone(view.browser)
