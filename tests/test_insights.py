import curses
import unittest
from copy import deepcopy
from unittest.mock import patch
from resourcelens.ui import View, draw
from resourcelens.insights import health_rows, search_entries
from resourcelens.impact import impact_lines, impact_summary
from resourcelens.investigation import Investigation
from resourcelens.collect import Collector
from test_navigation import Screen


class InsightsTests(unittest.TestCase):
    def setUp(self):
        self.sample = Collector(nvidia=False).sample()
        self.sample['processes'] = [dict(pid=123, starttime=77, name='demo-worker', cgroup='/demo', cpu_pct=0, memory=100, state='S', ppid=1)]
        self.sample['workloads'] = []
        self.view = View()
        self.view.update(self.sample)
        self.view.screen_size = (35, 110)

    def search(self, text):
        self.view.key(ord('s'))
        for char in text:self.view.key(ord(char))

    def test_start_here_click_and_task_navigation_never_change_settings(self):
        draw(Screen(24,80),self.view,1)
        self.assertIsNotNone(self.view.guide_region)
        with patch.object(self.view,'job') as job:
            self.view.mouse(self.view.guide_region[0]+1,2,curses.BUTTON1_CLICKED)
            self.assertEqual(self.view.modal['mode'],'guide')
            draw(Screen(24,80),self.view,1)
            self.view.key(10)
            self.assertIn('1. Start', '\n'.join(self.view.modal['detail']))
            self.view.key(10)
            self.assertEqual(self.view.page,0)
            self.assertFalse(self.view.control)
            job.assert_not_called()

    def test_named_tabs_fit_at_80_columns_and_click(self):
        screen=Screen(24,80)
        draw(screen,self.view,1)
        text=' '.join(value for (y,x),value in screen.lines.items() if y==1)
        self.assertIn('2:CPU',text)
        self.assertIn('8:Apps',text)
        self.assertEqual(len(self.view.tab_regions),10)
        self.assertTrue(all(end<80 for start,end,page in self.view.tab_regions))
        start,end,page=self.view.tab_regions[6]
        self.view.mouse(start,1,curses.BUTTON1_CLICKED)
        self.assertEqual(self.view.page,6)

    def test_search_process_identity_and_reuse(self):
        self.search('demo-worker')
        self.view.key(10)
        self.assertEqual(self.view.browser.process, (123,77))
        self.search('demo-worker')
        self.sample['processes'][0]['starttime'] = 78
        self.view.key(10)
        self.assertEqual(self.view.modal['stage'], 'result')
        self.assertIsNone(self.view.browser)

    def test_search_devices_views_and_cores(self):
        entries=search_entries(self.sample,self.view.names)
        self.assertTrue(any(e[0]=='view' for e in entries))
        self.assertTrue(any(e[0]=='core' for e in entries))
        self.assertTrue(any(e[0]=='resource' for e in entries))
        self.search('network')
        self.view.key(10)
        self.assertEqual(self.view.page,6)

    def test_search_query_never_runs_actions(self):
        self.search('qCwls')
        self.assertFalse(self.view.control)
        self.assertEqual(self.view.modal['query'],'qCwls')
        self.view.key(27)
        self.assertIsNone(self.view.modal)

    def test_metric_mouse_select_and_related_view(self):
        self.view.key(ord('E'))
        draw(Screen(35,110),self.view,1)
        row, region=next(iter(self.view.modal_regions.items()))
        self.view.mouse(region[0]+2,row,curses.BUTTON1_CLICKED)
        self.assertIn('Source:', '\n'.join(self.view.modal['detail']))
        self.assertIn('Limits:', '\n'.join(self.view.modal['detail']))
        self.view.key(10)
        self.assertEqual(self.view.page,1)

    def test_health_zero_missing_stale_replay_and_explicit_permissions(self):
        sample=deepcopy(self.sample);sample['timestamp']=100
        sample['cpu']=[{'id':'cpu','busy_pct':0}]
        self.assertEqual(health_rows(sample,now=101)[0][1],'live')
        self.assertEqual(health_rows(sample,now=110)[0][1],'stale')
        self.assertEqual(health_rows(sample,replay=True,now=110)[0][1],'recorded')
        sample['cpu'][0]['busy_pct']=None
        self.assertEqual(health_rows(sample,now=101)[0][1],'unavailable')
        sample['gpus']=[dict(id='g0',busy_pct=None,error='Permission denied')]
        self.assertEqual(next(r for r in health_rows(sample,now=101) if r[0]=='GPU g0')[1],'permission denied')

    def test_impact_offline_reports_empty_and_unknown_sets(self):
        sample={'workloads':[dict(id='/a',limits={'ancestors':[{'cpuset.cpus.effective':'0'}]}),dict(id='/b')]}
        plan=dict(setting='online',value='0',affected=[0],before={'cpus':[dict(id=0,online=True),dict(id=1,online=True)]})
        text='\n'.join(impact_lines(sample,plan,'hardware'))
        self.assertIn('NONE in sampled set',text)
        self.assertIn('unknown placement: 1',text)
        self.assertIn('1 unknown', ' '.join(impact_summary(sample,plan,'hardware')))

    def test_impact_dialog_returns_without_applying(self):
        plan=dict(target={'id':'/a','unit':'a.service','manager':'system','path':'/a'},property='quota',before={'quota':None},value=50)
        self.view.modal=dict(stage='confirm',plan=plan)
        preview=self.view.modal
        with patch.object(self.view,'job') as job:
            self.view.key(ord('i'))
            self.assertEqual(self.view.modal['stage'],'impact')
            self.view.key(10)
            self.assertIs(self.view.modal,preview)
            job.assert_not_called()

    def test_health_mouse_indicator_and_replay_do_not_enable_controls(self):
        draw(Screen(35,110),self.view,1)
        self.view.mouse(self.view.health_region[0]+1,33,curses.BUTTON1_CLICKED)
        self.assertEqual(self.view.modal['mode'],'health')
        self.assertFalse(self.view.control)

    def test_comparison_separates_devices_energy_and_missing_values(self):
        investigation=Investigation()
        for stamp,value,ifindex in ((90,0,1),(110,20,2)):
            sample=deepcopy(self.sample);sample['timestamp']=stamp
            sample['network']=[dict(name='net0',ifindex=ifindex,rx_bps=value,tx_bps=None)]
            sample['energy']={'domains':[dict(id='package',watts=value),dict(id='core',watts=value/2)]}
            investigation.observe(sample)
        event=investigation.mark('change',timestamp=100)
        pair,counts=investigation.compare(event)
        self.assertEqual(counts,[1,1])
        self.assertIn('Energy (hardware) package W',pair[0])
        self.assertIn('Energy (hardware) core W',pair[0])
        self.assertNotIn('network:net0:2 rx B/s',pair[0])
        text='\n'.join(investigation.lines())
        self.assertIn('baseline is zero',text)
        self.assertIn('one window has no usable values',text)
        self.assertIn('Before [',text)


if __name__ == '__main__':unittest.main()
