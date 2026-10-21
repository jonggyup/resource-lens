from pathlib import Path
import tempfile
import unittest
import curses
from resourcelens.resource_topology import topology
from resourcelens.resource_panels import panels, resource_lines, expanded_overview
from resourcelens.collect import Collector
from resourcelens.ui import View


class ResourcePanelsTests(unittest.TestCase):
    def test_panels_wrap_without_loss_or_overflow(self):
        cards = [('Long title', ['word ' * 40, 'detail']), ('Second', ['tail'])]
        for width in (37, 77, 117, 200):
            lines = panels(cards, width)
            self.assertTrue(all(len(line) <= width for line in lines))
            self.assertEqual(' '.join(lines).count('word'), 40)
            self.assertIn('tail', '\n'.join(lines))

    def test_sysfs_nodes_and_stacked_relationships(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def put(path, value):
                file = root / path
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(value)
            put('devices/system/node/node2/meminfo', 'Node 2 MemTotal: 1024 kB\nNode 2 MemFree: 256 kB\n')
            put('devices/system/node/node2/cpulist', '2-3,6-7')
            put('block/dm-0/size', '100')
            put('block/dm-0/slaves/sda/placeholder', '')
            put('block/sda/holders/dm-0/placeholder', '')
            put('block/sda/sda1/partition', '1')
            put('class/net/br0/lower_eth0/placeholder', '')
            put('class/net/eth0/placeholder', '')
            (root / 'class/net/eth0/master').symlink_to('../br0')
            data = topology(root)
            self.assertEqual(data['memory_nodes'][0]['cpus'], [2, 3, 6, 7])
            self.assertEqual(data['memory_nodes'][0]['free'], 256 * 1024)
            self.assertEqual(data['disks']['dm-0']['capacity'], 51200)
            self.assertEqual(data['disks']['dm-0']['backing'], ['sda'])
            self.assertEqual(data['disks']['sda']['partitions'], ['sda1'])
            self.assertEqual(data['network']['eth0']['master'], 'br0')
            self.assertEqual(data['network']['br0']['lower'], ['eth0'])
            self.assertIsNone(data['network']['eth0']['pci'])

    def test_missing_topology_is_not_fabricated(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(topology(tmp), dict(memory_nodes=[], disks={}, network={}, gpus={}))

    def test_resource_panels_and_expansion_on_live_readonly_sample(self):
        view = View()
        view.update(Collector(nvidia=False).sample())
        for kind in ('memory', 'disk', 'gpu', 'network'):
            for width in (37, 77, 117):
                lines = resource_lines(view, kind, width)
                self.assertTrue(lines)
                self.assertTrue(all(len(line) <= width for line in lines), (kind, width))
        lines, links = expanded_overview(view, 77)
        self.assertEqual(set(links.values()), {1, 2, 3, 4, 5, 6, 7})
        self.assertIn('CPU TOPOLOGY', lines[0])
        self.assertFalse(view.overview_expanded)
        view.key(ord('e'))
        self.assertTrue(view.overview_expanded)
        view.scroll = 20
        view.mouse(5, 2, curses.BUTTON1_PRESSED)
        self.assertFalse(view.overview_expanded)
        self.assertEqual(view.scroll, 0)
        view.overview_links = {10: 4}
        view.mouse(5, 10, curses.BUTTON1_PRESSED)
        self.assertEqual(view.page, 4)

    def test_memory_gauges_do_not_conflate_free_and_available(self):
        view = View()
        view.update(Collector(nvidia=False).sample())
        view.snapshot['resource_topology']['memory_nodes'] = [dict(id='node0', total=1000, free=100, cpus=[0])]
        text = '\n'.join(resource_lines(view, 'memory', 117))
        self.assertIn('including reclaimable', text)
        self.assertIn('Cache overlaps available RAM', text)
        self.assertIn('90.0%', text)
