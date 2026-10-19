import curses
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from resourcelens.memory_sharing import inspect_memory,backing_index,common_backings,mappings,MemorySharingView,totals
from resourcelens.ui import View
from test_navigation import Screen


class MemorySharingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.target={'pid':1,'starttime':100,'name':'target'}
        self.peers=[self.target,{'pid':2,'starttime':200,'name':'peer'}]
        for pid,start in [(1,100),(2,200)]:
            path=self.root/str(pid);path.mkdir()
            (path/'stat').write_text(f'{pid} (name with spaces) '+' '.join(['S']+['0']*18+[str(start)]))
            (path/'maps').write_text('1000-2000 rw-s 00000000 08:01 10 /dev/shm/demo\n')
        (self.root/'1/smaps_rollup').write_text('Rss: 512 kB\nPss: 384 kB\nPrivate_Clean: 128 kB\nPrivate_Dirty: 128 kB\nShared_Clean: 256 kB\nShared_Dirty: 0 kB\n')
        for tid,tgid in [(1,1),(11,1),(12,9)]:
            path=self.root/f'1/task/{tid}';path.mkdir(parents=True)
            (path/'status').write_text(f'Name:\tworker\nTgid:\t{tgid}\nState:\tS (sleeping)\n')

    def test_resident_totals_threads_and_shared_mode_peer(self):
        result=inspect_memory(self.target,self.peers,self.root)
        self.assertEqual(result['totals']['Pss'],384*1024)
        self.assertEqual([t['tid'] for t in result['threads']],[1,11])
        self.assertEqual(result['peers'][0]['pid'],2)
        self.assertEqual(result['peers'][0]['shared_count'],1)

    def test_file_identity_offsets_and_private_mapping_semantics(self):
        target=mappings((self.root/'1/maps').read_text())
        index=backing_index(target)
        for text in ['1000-2000 rw-s 00001000 08:01 10 /dev/shm/demo',
                     '1000-2000 rw-s 00000000 08:01 11 /dev/shm/demo',
                     '1000-2000 rw-s 00000000 00:00 0 [anon]']:
            self.assertEqual(common_backings(index,mappings(text)),{})
        (self.root/'2/maps').write_text('1000-2000 rw-p 00000000 08:01 10 /renamed\n')
        result=inspect_memory(self.target,self.peers,self.root)
        self.assertEqual(result['peers'][0]['common_count'],1)
        self.assertEqual(result['peers'][0]['shared_count'],0)

    def test_missing_fields_are_unknown_and_peer_limit_is_partial(self):
        (self.root/'1/smaps_rollup').unlink()
        result=inspect_memory(self.target,self.peers,self.root,peer_limit=0)
        self.assertEqual(result['totals'],{})
        self.assertTrue(result['warnings'])
        self.assertTrue(result['partial'])
        self.assertEqual(result['checked'],0)
        self.assertEqual(totals('Rss: unavailable\nPss: 0 kB'),{'Pss':0})

    def test_pid_reuse_discards_results(self):
        with patch('resourcelens.memory_sharing.identity',side_effect=[100,999]):
            with self.assertRaisesRegex(ValueError,'changed identity'):
                inspect_memory(self.target,[self.target],self.root)
        with self.assertRaisesRegex(ValueError,'reused'):
            inspect_memory({**self.target,'starttime':99},[],self.root)

    def test_memory_view_does_not_block_escape_and_discards_cancelled_result(self):
        release=threading.Event();started=threading.Event()
        def slow(*args):started.set();release.wait(2);return {'test':True}
        with patch('resourcelens.memory_sharing.inspect_memory',side_effect=slow):
            view=MemorySharingView(self.target,[],self.root)
            self.assertTrue(started.wait(1))
            before=time.monotonic();self.assertFalse(view.key(27,24))
            self.assertLess(time.monotonic()-before,.1)
            release.set()
        self.assertIsNone(view.result)

    def test_replay_never_inspects_live_pid(self):
        view=View();view.replay=object()
        with patch('resourcelens.ui.MemorySharingView') as worker:
            view.open_memory_sharing();worker.assert_not_called()
        self.assertIn('disabled during replay',view.modal['message'])

    def test_ui_open_tabs_refresh_and_return(self):
        view=View();view.page=7;view.proc_root=str(self.root)
        view.snapshot={'processes':[dict(self.target,cpu_pct=0,memory=0,cgroup='/')]}
        view.key(ord('V'))
        deadline=time.monotonic()+2
        while view.memory_sharing.result is None and view.memory_sharing.error is None and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertIsNotNone(view.memory_sharing.result)
        self.assertIn('Private resident', '\n'.join(view.memory_sharing.lines(100)))
        view.key(ord(']'));self.assertEqual(view.memory_sharing.tab,1)
        screen=Screen(24,100);view.memory_sharing.draw(screen,100,24)
        x,end,y,tab=view.memory_sharing.regions[2]
        view.mouse(x,y,curses.BUTTON1_CLICKED)
        self.assertEqual(view.memory_sharing.tab,2)
        self.assertIn('not proof','\n'.join(view.memory_sharing.lines(100)))
        view.key(27);self.assertIsNone(view.memory_sharing)
        self.assertEqual(view.page,7)
