from copy import deepcopy
import curses
import unittest
from resourcelens.collect import Collector
from resourcelens.ui import View
from resourcelens.workspaces import allowed_cpus, related_groups, io_rate


def group(name,inode=1):
    return dict(id='/'+name,path='/'+name,unit=name,kind='system service',manager='system',owner_uid=None,
                inode=inode,cpu_pct=15,memory_current=100,pids=[],pressure={},throttled_usec_per_s=None,
                io_devices=[dict(device='8:0',read_bps=12,write_bps=0)],limits={'ancestors':[{'path':'/'+name,'cpuset.cpus.effective':'0,2'}]},controllable=False)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.view=View()
        self.view.application_mode = 'cgroups'
        self.sample=Collector(nvidia=False).sample()
        self.sample['workloads']=[group('demo.service')]
        self.sample['disks']=[dict(name='sda',identity='8:0:55',active_pct=0,read_bps=12,write_bps=0)]
        self.view.update(self.sample)

    def test_existing_navigation_preserves_overview_state(self):
        self.view.overview_expanded=True
        self.view.key(ord('H'));self.assertEqual(self.view.page,0)
        self.assertEqual(self.view.modal['mode'],'health');self.view.key(27)
        self.view.key(ord('A'));self.assertEqual(self.view.page,0)
        self.view.key(ord('8'));self.assertEqual(self.view.page,7)
        self.view.key(ord('1'));self.assertEqual(self.view.page,0)
        self.assertTrue(self.view.overview_expanded)
        self.view.key(ord('0'));self.view.key(9)
        self.assertEqual(self.view.page,0)
        self.assertEqual(len(self.view.names),10)

    def test_cpu_relation_means_allowed_not_actual_use(self):
        self.view.page=1;self.view.hardware_selection=(2,)
        self.view.key(ord('a'))
        self.assertEqual(len(self.view.workload_rows()),1)
        self.assertIn('not measured execution',self.view.relation_label)
        self.view.relation=('cpu',(1,));self.assertEqual(self.view.workload_rows(),[])
        self.view.key(ord('x'));self.assertEqual(len(self.view.workload_rows()),1)
        self.view.page=1
        self.view.topology_regions={12:[(0,20,'core',(2,))]}
        self.view.mouse(2,12,curses.BUTTON1_CLICKED | curses.BUTTON_SHIFT)
        self.assertEqual(self.view.page,7)
        self.assertEqual(self.view.relation,('cpu',(2,)))
        self.assertIsNone(self.view.hardware_selection)

    def test_disk_relation_requires_active_matching_device_identity(self):
        self.view.page=3;self.view.focus=('disk','sda');self.view.key(ord('a'))
        self.assertEqual(len(self.view.workload_rows()),1)
        self.sample['disks'][0]['identity']='8:0:99'
        self.assertEqual(self.view.workload_rows(),[])
        self.sample['disks'][0]['identity']='8:0:55'
        self.sample['workloads'][0]['io_devices'][0]['read_bps']=None
        self.assertEqual(self.view.workload_rows(),[])

    def test_application_tabs_and_missing_attribution(self):
        self.view.page=7
        for index in range(5):
            self.assertTrue(self.view.application_lines(60))
            self.view.key(ord(']'))
        self.assertEqual(self.view.app_tab,0)
        text='\n'.join(self.view.application_lines(80))
        self.assertIn('GPU / network per-application usage: unavailable',text)
        self.assertIn('Group type:',text)
        self.assertIn('CPU history',text)

    def test_history_reset_on_cgroup_replacement(self):
        sample=deepcopy(self.sample)
        sample['workloads'][0]['inode']=2;sample['workloads'][0]['cpu_pct']=33
        self.view.update(sample)
        self.assertEqual(list(self.view.history['workload:/demo.service']),[33])

    def test_unavailable_attribution_stays_unknown(self):
        self.assertIsNone(allowed_cpus({'limits':{'ancestors':[]}}))
        self.assertIsNone(io_rate({'io_devices':[]},'read'))
        self.view.page=4;self.view.key(ord('a'))
        self.assertIn('unavailable',self.view.modal['message'])

    def test_split_pane_regions_fit_and_click_selects(self):
        class Screen:
            def getmaxyx(self):return 24,100
            def addnstr(self,*args):pass
        self.view.page=7;self.view.screen_size=(24,100)
        self.view.draw_applications(Screen(),100,24)
        self.assertTrue(self.view.app_regions)
        for start,end,y,identity,inode in self.view.app_regions:
            self.assertLess(y,22)
        x,end,y,tab=self.view.app_tab_regions[1]
        self.view.mouse(x,y,curses.BUTTON1_CLICKED)
        self.assertEqual(self.view.app_tab,tab)


if __name__=='__main__':unittest.main()
