import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess

from resourcelens.collect import Collector, delta, parse_nvidia
from resourcelens.ui import View, clean, ratio, spark


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc, self.sys = self.root / "proc", self.root / "sys"
        self.now = 100.0
        self.write("proc/meminfo", "MemTotal: 1000 kB\nMemAvailable: 600 kB\nCached: 500 kB\n"
                   "SReclaimable: 50 kB\nShmem: 10 kB\nSwapTotal: 100 kB\nSwapFree: 70 kB\n")
        self.write("proc/stat", "cpu 100 0 20 800 30 0 0 50 70 0\ncpu0 100 0 20 800 30 0 0 50 70 0\n")
        self.write("proc/loadavg", "1.00 2.00 3.00 1/10 42")
        self.write("proc/pressure/io", "some avg10=12.50 avg60=5.00 avg300=1.00 total=12345\n")
        (self.sys / "block/sda").mkdir(parents=True)
        self.write("proc/diskstats", "8 0 sda 10 0 100 0 20 0 200 0 2 100 150\n"
                   "8 1 sda1 10 0 100 0 20 0 200 0 2 100 150\n")
        self.process(42, "a (weird) name", 100, 20, start=10)
        self.collector = Collector(self.proc, self.sys, nvidia=False, clock=lambda: self.now)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def process(self, pid, name, ticks, rss, start):
        v = ["0"] * 22
        v[0], v[11], v[12], v[16], v[19], v[21] = "S", str(ticks), "0", "5", str(start), str(rss)
        self.write(f"proc/{pid}/stat", f"{pid} ({name}) " + " ".join(v))

    def test_first_sample_has_unknown_rates(self):
        s = self.collector.sample()
        self.assertIsNone(s["cpu"][0]["busy_pct"])
        self.assertIsNone(s["disks"][0]["read_bps"])
        self.assertIsNone(s["processes"][0]["cpu_pct"])
        self.assertEqual(s["memory"]["used"], 400 * 1024)

    def test_cpu_excludes_guest_double_counting_and_iowait(self):
        self.collector.sample()
        self.now += 2
        self.write("proc/stat", "cpu 140 0 40 920 50 0 0 50 100 0\n")
        s = self.collector.sample()
        # 60 busy + 120 idle + 20 iowait; guest must not be added again.
        self.assertAlmostEqual(s["cpu"][0]["busy_pct"], 30)
        self.assertAlmostEqual(s["cpu"][0]["iowait_pct"], 10)

    def test_disk_sector_units_elapsed_and_partition_exclusion(self):
        self.collector.sample()
        self.now += 2
        self.write("proc/diskstats", "8 0 sda 14 0 120 0 26 0 240 0 0 1100 2150\n"
                   "8 1 sda1 14 0 120 0 26 0 240 0 0 1100 2150\n")
        disks = self.collector.sample()["disks"]
        self.assertEqual(len(disks), 1)
        self.assertEqual(disks[0]["read_bps"], 5120)
        self.assertEqual(disks[0]["write_bps"], 10240)
        self.assertEqual(disks[0]["active_pct"], 50)
        self.assertEqual(disks[0]["queue_depth"], 1)
        self.assertEqual(disks[0]["read_iops"], 2)

    def test_reset_is_unknown_not_negative_or_zero(self):
        self.collector.sample()
        self.now += 1
        self.write("proc/stat", "cpu 1 0 0 2 0 0 0 0\n")
        self.write("proc/diskstats", "8 0 sda 1 0 1 0 1 0 1 0 0 1 1\n")
        s = self.collector.sample()
        self.assertIsNone(s["cpu"][0]["busy_pct"])
        self.assertIsNone(s["disks"][0]["read_bps"])

    def test_process_names_and_multicore_cpu(self):
        self.collector.sample()
        self.now += 2
        self.process(42, "a (weird) name", 100 + self.collector.ticks * 3, 20, start=10)
        p = self.collector.sample()["processes"][0]
        self.assertEqual(p["name"], "a (weird) name")
        self.assertEqual(p["cpu_pct"], 150)
        self.assertEqual(p["nice"], 5)
        self.assertEqual(p["memory"], 20 * self.collector.pages)

    def test_reused_pid_does_not_inherit_old_cpu(self):
        self.collector.sample()
        self.now += 1
        self.process(42, "new", 2000, 20, start=99)
        self.assertIsNone(self.collector.sample()["processes"][0]["cpu_pct"])

    def test_missing_and_exited_process_do_not_crash(self):
        self.write("proc/99/stat", "malformed")
        s = self.collector.sample()
        self.assertEqual(len(s["processes"]), 1)
        self.assertEqual(s["processes_skipped"], 1)

    def test_psi_missing_is_distinct_from_zero(self):
        s = self.collector.sample()
        self.assertEqual(s["pressure"]["cpu"], {})
        self.assertEqual(s["pressure"]["io"]["some"]["avg10"], 12.5)

    def test_memory_accounting(self):
        m = self.collector.sample()["memory"]
        self.assertEqual(m["cache"], 540 * 1024)
        self.assertEqual(m["swap_used"], 30 * 1024)
        self.assertEqual(ratio(m["used"], m["total"]), 40)

    def test_missing_memory_is_unknown(self):
        (self.proc / "meminfo").unlink()
        s = self.collector.sample()
        self.assertIsNone(s["memory"]["used"])
        self.assertIn("Memory counters unavailable", s["warnings"])

    def test_cpu_frequency_is_khz_to_mhz(self):
        self.write("sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "2500000")
        self.assertEqual(self.collector.sample()["cpu"][1]["frequency_mhz"], 2500)

    def test_amd_gpu_sysfs(self):
        base = "sys/class/drm/card0/device/"
        for name, value in {"vendor": "0x1002", "device": "0x1234", "gpu_busy_percent": "75",
                            "mem_info_vram_total": "1000", "mem_info_vram_used": "400",
                            "hwmon/hwmon0/temp1_input": "55000",
                            "hwmon/hwmon0/power1_average": "12000000"}.items():
            self.write(base + name, value)
        gpu = self.collector.sample()["gpus"][0]
        self.assertEqual(gpu["busy_pct"], 75)
        self.assertEqual(gpu["temperature_c"], 55)
        self.assertEqual(gpu["power_w"], 12)

    def test_unsupported_gpu_is_detected_not_faked(self):
        self.write("sys/class/drm/card0/device/vendor", "0x8086")
        self.write("sys/class/drm/card0-VGA-1/status", "connected")
        gpus = self.collector.sample()["gpus"]
        self.assertEqual(len(gpus), 1)
        self.assertIsNone(gpus[0]["busy_pct"])
        self.assertIn("no GPU utilization", gpus[0]["status"])

    def test_nvidia_csv_and_unsupported_values(self):
        g = parse_nvidia('"NVIDIA, Test", 0000:01:00.0, 42, 100, 200, 55, [N/A], 1200\n')[0]
        self.assertEqual(g["name"], "NVIDIA, Test")
        self.assertEqual(g["memory_used"], 100 * 1024**2)
        self.assertIsNone(g["power_w"])
        self.assertEqual(parse_nvidia("garbage"), [])

    def test_nvidia_timeout_does_not_lose_other_metrics(self):
        self.collector.nvidia = "/fixture/nvidia-smi"
        self.write("sys/class/drm/card0/device/vendor", "0x10de")
        target = self.sys / "bus/drivers/nvidia"
        target.mkdir(parents=True)
        (self.sys / "class/drm/card0/device/driver").symlink_to(target)
        with patch.object(self.collector, "run_query", side_effect=subprocess.TimeoutExpired("nvidia-smi", 0.8)):
            sample = self.collector.sample()
        self.assertIsNotNone(sample["memory"]["total"])
        self.assertIn("timed out", sample["gpus"][0]["status"])
        self.assertIsNone(sample["gpus"][0]["busy_pct"])

    def test_nvidia_backend_without_drm_and_nonzero_exit(self):
        self.collector.nvidia = "/fixture/nvidia-smi"
        completed = subprocess.CompletedProcess([], 0, "Test, PCI:0, 90, 50, 100, 40, 10, 900\n", "")
        with patch.object(self.collector, "run_query", return_value=completed):
            self.assertEqual(self.collector.sample()["gpus"][0]["busy_pct"], 90)
        completed.returncode = 1
        with patch.object(self.collector, "run_query", return_value=completed):
            self.assertEqual(self.collector.sample()["gpus"], [])

    def test_json_is_finite(self):
        encoded = json.dumps(self.collector.sample(), allow_nan=False)
        self.assertEqual(json.loads(encoded)["schema_version"], 1)

    def test_freeze_and_terminal_content(self):
        view = View()
        first = self.collector.sample()
        view.update(first)
        view.key(ord(" "))
        self.now += 1
        view.update(self.collector.sample())
        self.assertIs(view.snapshot, first)
        self.assertEqual(len(view.history["cpu"]), 1)
        self.assertEqual(clean("bad\x1b[2J\nname"), "bad?[2J?name")

    def test_filter_and_sort(self):
        view = View()
        view.update(self.collector.sample())
        view.key(ord("/"))
        for char in "weird":
            view.key(ord(char))
        view.key(10)
        self.assertTrue(any("weird" in line for line in view.process_lines()))
        view.key(ord("m"))
        self.assertEqual(view.sort, "memory")
        view.key(ord("/"))
        view.key(27)
        self.assertEqual(view.filter, "")

    def test_nonpositive_time_is_not_a_rate(self):
        self.assertIsNone(delta(10, 5, 0))
        self.assertIsNone(delta(10, 5, -1))

    def test_missing_history_is_explicit_instead_of_question_marks(self):
        self.assertEqual(spark([]), "N/A (no measured data)")
        self.assertEqual(spark([None] * 120), "N/A (no measured data)")
        self.assertLessEqual(len(spark([None] * 120, width=8)), 8)

    def test_history_gaps_are_distinct_from_measured_zero(self):
        self.assertEqual(spark([0, None, 100]), ". @")
        self.assertEqual(spark([0, 50, 100], width=2), "=@")
        self.assertTrue(spark([100] + [None] * 8, width=8).startswith("N/A"))

    def test_live_measured_history_and_unsupported_gpu_history(self):
        self.write("sys/class/drm/card0/device/vendor", "0x102b")
        view = View()
        view.update(self.collector.sample())
        self.now += 1
        self.write("proc/stat", "cpu 140 0 40 920 50 0 0 50 100 0\n")
        view.update(self.collector.sample())
        self.assertEqual(spark(view.history["cpu"]), " -")
        self.assertNotIn("N/A", spark(view.history["memory"]))
        self.assertNotIn("N/A", spark(view.history["disk"]))
        self.assertIn("History N/A (no measured data)", view.gpu_lines())

    def net(self, rx=1000, tx=2000, ifindex=2):
        self.write("proc/net/dev", "Inter-| Receive | Transmit\n face | bytes packets errs drop | bytes packets errs drop\n"
                   f" eth0: {rx} 10 2 3 0 0 0 0 {tx} 20 4 5 0 0 0 0\n")
        self.write("sys/class/net/eth0/ifindex", str(ifindex))
        self.write("sys/class/net/eth0/operstate", "up")
        self.write("sys/class/net/eth0/speed", "1000")

    def test_network_rate_units_and_link_metadata(self):
        self.net()
        first = self.collector.sample()["network"][0]
        self.assertIsNone(first["rx_bps"])
        self.now += 2
        self.net(rx=3000, tx=6000)
        entry = self.collector.sample()["network"][0]
        self.assertEqual(entry["rx_bps"], 1000)
        self.assertEqual(entry["tx_bps"], 2000)
        self.assertEqual(entry["rx_bytes"], 3000)
        self.assertEqual(entry["state"], "up")
        self.assertEqual(entry["speed_mbps"], 1000)
        self.assertEqual(entry["rx_errors"], 2)
        self.assertEqual(entry["tx_dropped"], 5)

    def test_network_counter_reset_and_interface_replacement(self):
        self.net()
        self.collector.sample()
        self.now += 1
        self.net(rx=100, tx=100)
        reset = self.collector.sample()["network"][0]
        self.assertIsNone(reset["rx_bps"])
        self.assertIsNone(reset["tx_bps"])
        self.now += 1
        self.net(rx=90000, tx=90000, ifindex=3)
        self.assertIsNone(self.collector.sample()["network"][0]["rx_bps"])

    def test_network_missing_malformed_and_removed(self):
        self.assertEqual(self.collector.sample()["network"], [])
        self.net()
        self.collector.sample()
        self.write("proc/net/dev", "bad: nonsense\nshort: 1 2\n")
        self.now += 1
        self.assertEqual(self.collector.sample()["network"], [])
        self.net(rx=90000)
        self.now += 1
        self.assertIsNone(self.collector.sample()["network"][0]["rx_bps"])

    def test_network_history_is_scaled_to_rate_and_has_separate_directions(self):
        self.net()
        view = View()
        view.update(self.collector.sample())
        self.now += 1
        self.net(rx=1000, tx=3000)
        view.update(self.collector.sample())
        self.now += 1
        self.net(rx=2000, tx=3000)
        view.update(self.collector.sample())
        self.assertEqual(spark(view.history[("eth0", 2, "rx")], maximum=1000), " .@")
        self.assertEqual(spark(view.history[("eth0", 2, "tx")], maximum=1000), " @.")
        view.key(ord("7"))
        self.assertEqual(view.names[view.page], "Network")
        lines = view.lines(100)
        self.assertTrue(any("RX history" in line for line in lines))
        self.assertTrue(any("peak 1000.0B/s" in line for line in lines))
        self.write("proc/net/dev", "")
        view.update(self.collector.sample())
        self.assertFalse(any(isinstance(key, tuple) for key in view.history))

    def test_network_zero_traffic_and_unknown_link_speed(self):
        self.net()
        self.write("sys/class/net/eth0/speed", "-1")
        self.collector.sample()
        self.now += 1
        entry = self.collector.sample()["network"][0]
        self.assertEqual(entry["rx_bps"], 0)
        self.assertIsNone(entry["speed_mbps"])

    def test_network_overview_favors_live_link_and_summarizes_other_interfaces(self):
        self.net()
        snapshot = self.collector.sample()
        snapshot["network"].insert(0, {**snapshot["network"][0], "name": "aaa", "state": "down"})
        view = View()
        view.update(snapshot)
        lines = view.network_lines(100)
        self.assertTrue(lines[0].startswith("eth0 (up)"))
        self.assertIn("1 other interfaces", lines[-1])


if __name__ == "__main__":
    unittest.main()
