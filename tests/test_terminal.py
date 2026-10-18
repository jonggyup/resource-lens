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
    def test_escape_exit_latency_with_live_and_stalled_collection(self):
        for slow in (False, True):
            with self.subTest(stalled_collector=slow):
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 100, 0, 0))
                code = ('from resourcelens.ui import run; import time\n'
                        'class Slow:\n def sample(self): time.sleep(5); return {}\n'
                        'run(Slow(), 1)')
                command = ([sys.executable, '-c', code] if slow else
                           [sys.executable, '-m', 'resourcelens', '--no-nvidia'])
                env = {**os.environ, 'TERM': 'xterm-256color'}
                env.pop('ESCDELAY', None)
                child = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, env=env)
                output = bytearray()
                try:
                    deadline = time.monotonic() + 5
                    while b'RESOURCE LENS' not in output and time.monotonic() < deadline:
                        if select.select([master], [], [], .02)[0]:
                            output.extend(os.read(master, 65536))
                    self.assertIn(b'RESOURCE LENS', output)
                    started = time.monotonic()
                    os.write(master, b'\x1b')
                    while child.poll() is None and time.monotonic() - started < .25:
                        if select.select([master], [], [], .005)[0]:
                            output.extend(os.read(master, 65536))
                    self.assertEqual(child.poll(), 0, 'Escape exit exceeded 250 ms')
                    self.assertNotIn(b'Traceback', output)
                finally:
                    if child.poll() is None:
                        child.kill()
                    child.wait()
                    os.close(master)
                    os.close(slave)

    def test_escape_prefixed_keys_still_decode(self):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 100, 0, 0))
        code = ("import json; from resourcelens.ui import View, run; "
                "from resourcelens.collect import Collector\n"
                "keys=[]; original=View.key\n"
                "def capture(self,key,height=24):\n"
                " if key!=-1: keys.append(key)\n"
                " return original(self,key,height)\n"
                "View.key=capture\n"
                "run(Collector(nvidia=False),1)\n"
                "print('DECODED='+json.dumps(keys))")
        child = subprocess.Popen([sys.executable, '-c', code], stdin=slave, stdout=slave,
                                 stderr=slave, env={**os.environ, 'TERM': 'xterm-256color', 'ESCDELAY': '150'})
        output = bytearray()
        try:
            deadline = time.monotonic() + 5
            while b'RESOURCE LENS' not in output and time.monotonic() < deadline:
                if select.select([master], [], [], .02)[0]:
                    output.extend(os.read(master, 65536))
            self.assertIn(b'RESOURCE LENS', output)
            # A split arrow tests the explicit SSH override beyond the default 1 ms.
            os.write(master, b'\x1b')
            time.sleep(.06)
            os.write(master, b'OA')
            os.write(master, b'\x1bOP\x1bOPq')  # F1 opens/closes help, q exits.
            deadline = time.monotonic() + 3
            while child.poll() is None and time.monotonic() < deadline:
                if select.select([master], [], [], .02)[0]:
                    output.extend(os.read(master, 65536))
            while select.select([master], [], [], 0)[0]:
                output.extend(os.read(master, 65536))
            self.assertEqual(child.poll(), 0, output[-2000:])
            import curses
            decoded = json.loads(output.split(b'DECODED=')[-1].strip())
            self.assertEqual(decoded, [curses.KEY_UP, curses.KEY_F1, curses.KEY_F1, ord('q')])
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()
            os.close(master)
            os.close(slave)

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
        for key in (b"e", b"e", b"2", b"3", b"4", b"5", b"6", b"7", b"8", b"w", b"\r", b"/ssh\r", b"1",
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
        self.assertIn("CPU TOPOLOGY", text)
        self.assertIn("Collapse overview", text)
        self.assertIn("RX history", text)
        self.assertNotIn("Traceback", text)

    def test_hardware_menu_in_real_terminal(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 35, 120, 0, 0))
        child = subprocess.Popen([sys.executable, "-m", "resourcelens", "--control", "--no-nvidia"],
                                 stdin=slave, stdout=slave, stderr=slave,
                                 env={**os.environ, "TERM": "xterm-256color"})
        self.addCleanup(lambda: child.poll() is None and child.kill())
        output = bytearray()
        def drain(duration=0.4):
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.03)
                if ready:
                    output.extend(os.read(master, 65536))
        drain(0.8)
        os.write(master, b"2g")
        drain()
        self.assertIn(b"CPU settings: All CPUs / all sockets", output)
        os.write(master, b"\x1b")
        drain(1.2)
        # SGR right-button press on the first logical CPU row.
        os.write(master, b"\x1b[<2;5;17M")
        drain()
        self.assertIn(b"CPU settings: ID 0", output)
        os.write(master, b"\x1b")
        drain(1.2)
        os.write(master, b"q")
        drain()
        child.wait(timeout=3)
        self.assertEqual(child.returncode, 0, output.decode(errors="replace")[-2000:])
        self.assertNotIn(b"Traceback", output)

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
