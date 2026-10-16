"""Launch the actual CLI in a pseudo-terminal, including resize and navigation."""

import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
import unittest


class TerminalTests(unittest.TestCase):
    def test_live_terminal_navigation_resize_and_exit(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 35, 120, 0, 0))
        child = subprocess.Popen([sys.executable, "-m", "resourcelens", "--interval", "0.2", "--no-nvidia"],
                                 stdin=slave, stdout=slave, stderr=slave,
                                 env={**os.environ, "TERM": "xterm-256color"})
        self.addCleanup(lambda: child.poll() is None and child.kill())
        output = bytearray()

        def drain(duration=0.25):
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.03)
                if ready:
                    output.extend(os.read(master, 65536))

        drain(0.7)
        for key in (b"2", b"3", b"4", b"5", b"6", b"7", b"8", b"w", b"\r", b"/ssh\r", b"1",
                    b"/python\r", b"m", b"?", b"?", b" ", b" ", b"1"):
            os.write(master, key)
            drain()
        for height, width in ((24, 80), (12, 40), (35, 120)):
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0))
            drain()
        os.write(master, b"q")
        drain()
        child.wait(timeout=3)
        text = output.decode(errors="replace")
        self.assertEqual(child.returncode, 0, text[-3000:])
        self.assertIn("RESOURCE LENS", text)
        self.assertIn("Read-only", text)
        self.assertIn("Filter:", text)
        self.assertIn("RX history", text)
        self.assertNotIn("Traceback", text)

    def test_json_cli_measured_sample(self):
        output = subprocess.check_output([sys.executable, "-m", "resourcelens", "--json", "--count", "2",
                                          "--interval", "0.2", "--no-nvidia"], text=True, timeout=5)
        samples = [json.loads(line) for line in output.splitlines()]
        self.assertEqual(len(samples), 2)
        self.assertGreater(samples[0]["elapsed_s"], 0)
        self.assertIn("disks", samples[0])
        self.assertIn("network", samples[0])
        self.assertIn("workloads", samples[0])

    def test_bad_interval_rejected(self):
        for value in ("nan", "inf", "0", "-1", "61"):
            result = subprocess.run([sys.executable, "-m", "resourcelens", "--interval", value],
                                    capture_output=True, text=True, timeout=3)
            self.assertEqual(result.returncode, 2)

    def test_nonterminal_has_actionable_message(self):
        result = subprocess.run([sys.executable, "-m", "resourcelens"], capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--once", result.stderr)


if __name__ == "__main__":
    unittest.main()
