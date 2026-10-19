import curses
import unittest
from unittest.mock import patch
from resourcelens.ui import View,draw
from resourcelens.collect import Collector
from resourcelens.cgroup_browser import CgroupBrowser
from resourcelens.usability import preferences
from test_navigation import Screen


class ApplicationProcessTests(unittest.TestCase):
    def setUp(self):
        self.view=View(control=True)
        self.sample=Collector(nvidia=False).sample()
        self.sample['processes']=[dict(pid=pid,starttime=pid*10,name='p'+str(pid),cgroup='/demo',cpu_pct=cpu,memory=pid,ppid=1,state='S') for pid,cpu in [(1,0),(2,80),(3,None),(4,40)]]
        self.sample['cgroup_tree']={'nodes':[dict(path='/',inode=1),dict(path='/demo',inode=2),dict(path='/demo/child',inode=3,cpu_pct=60)],'errors':0,'truncated':False}
        self.view.update(self.sample);self.view.key(ord('8'))

    def test_default_process_view_cpu_order_unknown_last_and_top_tracks(self):
        self.assertEqual(self.view.application_mode,'processes')
        rows=self.view.application_process_rows()
        self.assertEqual([p['pid'] for p in rows],[2,4,1,3])
        self.view.selected_application_process(rows)
        self.sample['processes'][0]['cpu_pct']=100
        self.view.selected_application_process(self.view.application_process_rows())
        self.assertEqual(self.view.application_process,(1,10))
        draw(Screen(24,100),self.view,1)
        self.assertTrue(self.view.application_process_regions)

    def test_selection_stable_after_ranking_changes_and_pid_reuse(self):
        self.view.key(curses.KEY_DOWN)
        selected=self.view.application_process
        self.sample['processes'][1]['cpu_pct']=0
        self.view.selected_application_process(self.view.application_process_rows())
        self.assertEqual(self.view.application_process,selected)
        p=next(p for p in self.sample['processes'] if p['pid']==selected[0]);p['starttime']+=1
        self.assertIsNone(self.view.selected_application_process(self.view.application_process_rows()))
        self.view.key(10);self.assertIsNone(self.view.browser)

    def test_process_search_keeps_applications_page(self):
        self.view.key(ord('/'));self.view.key(ord('2'));self.view.key(10)
        self.assertEqual(self.view.page,7)
        self.assertEqual([p['pid'] for p in self.view.application_process_rows()],[2])

    def test_toggle_mouse_preferences_and_hidden_controls(self):
        with patch.object(self.view,'job') as job:
            self.view.key(ord('w'));self.assertEqual(self.view.modal['stage'],'result');job.assert_not_called()
        self.view.key(27)
        draw(Screen(24,100),self.view,1)
        x,end=self.view.application_mode_region
        self.view.mouse(x,2,curses.BUTTON1_CLICKED)
        self.assertEqual(self.view.application_mode,'cgroups')
        self.view.key(ord('g'));self.assertEqual(self.view.application_mode,'processes')
        self.assertEqual(preferences({'application_mode':'cgroups'})['application_mode'],'cgroups')

    def test_process_details_and_back_without_hierarchy_recording(self):
        del self.sample['cgroup_tree']
        self.view.key(10)
        screen=Screen(24,100);self.view.browser.draw(screen,self.sample,100,24)
        self.assertIn('PID 2: p2',' '.join(screen.lines.values()))
        self.view.key(27);self.assertIsNone(self.view.browser)
        self.assertEqual(self.view.application_mode,'processes')

    def test_children_and_direct_processes_rank_together(self):
        browser=CgroupBrowser(self.sample,'/demo')
        rows=browser.rows(self.sample)
        self.assertEqual([(r[0],r[1]) for r in rows],[('process',2),('group','/demo/child'),('process',4),('process',1),('process',3)])
        browser.selection(rows);selected=browser.selected
        self.sample['processes'][0]['cpu_pct']=100
        browser.selection(browser.rows(self.sample));self.assertEqual(browser.selected,selected)
        browser.key(ord('n'),self.sample,24);self.assertEqual(browser.sort,'name')
