from pathlib import Path
import tempfile
import unittest

from resourcelens.workloads import WorkloadCollector, attribution, membership


class WorkloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.collector = WorkloadCollector(self.root)
        for group, quota, memory in (("demo.slice", "50000 100000", "1000"),
                                      ("demo.slice/job.service", "80000 100000", "2000")):
            self.put(group, "cpu.max", quota)
            self.put(group, "cpu.weight", "100")
            self.put(group, "memory.max", memory)
            self.put(group, "cpu.stat", "usage_usec 1000\nnr_throttled 1\nthrottled_usec 2000\n")
        self.put("demo.slice/job.service", "memory.current", "700")
        self.members = [{"pid": n, "name": f"worker{n}", "cgroup": "/demo.slice/job.service/child"}
                        for n in (1, 2, 3)]

    def put(self, group, name, value):
        path = self.root / group / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def test_children_grouped_once_and_accounting_not_summed_from_processes(self):
        first = self.collector.sample(self.members, 1)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["pids"], [1, 2, 3])
        self.put("demo.slice/job.service", "cpu.stat", "usage_usec 501000\nthrottled_usec 12000\n")
        row = self.collector.sample(self.members, 1)[0]
        self.assertEqual(row["cpu_pct"], 50)
        self.assertEqual(row["memory_current"], 700)
        self.assertEqual(row["throttled_usec_per_s"], 10000)

    def test_tighter_parent_is_reported_with_source(self):
        limits = self.collector.limits("/demo.slice/job.service")
        self.assertEqual(limits["quota_pct"], 50)
        self.assertEqual(limits["quota_source"], "/demo.slice")
        self.assertEqual(limits["memory_max"], 1000)
        self.assertEqual(limits["memory_source"], "/demo.slice")
        self.assertTrue(limits["quota_complete"])

    def test_missing_ancestor_is_unknown_not_unlimited(self):
        (self.root / "demo.slice/cpu.max").unlink()
        limits = self.collector.limits("/demo.slice/job.service")
        self.assertEqual(limits["quota_pct"], 80)
        self.assertFalse(limits["quota_complete"])
        (self.root / "demo.slice/job.service/cpu.max").unlink()
        limits = self.collector.limits("/demo.slice/job.service")
        self.assertIsNone(limits["quota_pct"])
        self.assertFalse(limits["cpu_controller_active"])

    def test_unlimited_is_explicit_and_zero_memory_is_finite(self):
        for group in ("demo.slice", "demo.slice/job.service"):
            self.put(group, "cpu.max", "max 100000")
            self.put(group, "memory.max", "max")
        self.put("demo.slice", "memory.max", "0")
        limits = self.collector.limits("/demo.slice/job.service")
        self.assertTrue(limits["quota_complete"])
        self.assertIsNone(limits["quota_pct"])
        self.assertEqual(limits["memory_max"], 0)

    def test_io_is_per_device_and_rate_is_interval_delta(self):
        self.put("demo.slice/job.service", "io.stat", "8:0 rbytes=100 wbytes=200\n8:16 rbytes=0 wbytes=1000\n")
        self.collector.sample(self.members, 1)
        self.put("demo.slice/job.service", "io.stat", "8:0 rbytes=500 wbytes=1000\n8:16 rbytes=0 wbytes=900\n")
        row = self.collector.sample(self.members, 2)[0]
        self.assertEqual(len(row["io_devices"]), 2)
        self.assertEqual(row["io_devices"][0]["read_bps"], 200)
        self.assertIsNone(row["io_devices"][1]["write_bps"])

    def test_v1_unknown_and_missing_cgroup_mount(self):
        self.assertIsNone(membership("2:cpu:/legacy\n"))
        self.assertEqual(membership("0::/demo.slice/job.service\n"), "/demo.slice/job.service")
        self.assertIsNone(membership("0::/../escape\n"))
        row = self.collector.sample([{"pid": 1, "name": "unknown", "cgroup": None}], 1)[0]
        self.assertFalse(row["controllable"])
        self.assertIsNone(row["memory_current"])
        self.assertFalse(row["limits"]["quota_complete"])

    def test_path_escape_and_outside_symlink_are_rejected(self):
        self.assertIsNone(self.collector.directory("/../outside"))
        with tempfile.TemporaryDirectory() as outside:
            (self.root / "escape").symlink_to(outside)
            self.assertIsNone(self.collector.directory("/escape"))

    def test_manager_boundary_does_not_misattribute_user_services(self):
        info = attribution("/user.slice/user-1000.slice/user@1000.service/app.slice/editor.scope/child")
        self.assertEqual(info["unit"], "editor.scope")
        self.assertEqual(info["manager"], "user")
        self.assertEqual(info["owner_uid"], 1000)
        manager = attribution("/user.slice/user-1000.slice/user@1000.service")
        self.assertEqual(manager["manager"], "system")

    def test_deleted_group_and_reset_do_not_create_false_cpu_rates(self):
        self.collector.sample(self.members, 1)
        self.put("demo.slice/job.service", "cpu.stat", "usage_usec 10\n")
        self.assertIsNone(self.collector.sample(self.members, 1)[0]["cpu_pct"])


if __name__ == "__main__":
    unittest.main()
