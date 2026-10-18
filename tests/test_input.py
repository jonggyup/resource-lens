from collections import deque
import curses
import unittest
from unittest.mock import patch
from resourcelens.ui import clean, read_input
from resourcelens.presentation import GLYPHS


class Screen:
    def __init__(self, keys):
        self.keys=deque(keys)
        self.timeout_ms=100
    def getch(self):return self.keys.popleft() if self.keys else -1
    def getmaxyx(self):return 24,100
    def timeout(self,ms):self.timeout_ms=ms


class InputTests(unittest.TestCase):
    def test_bounded_batch_preserves_every_key(self):
        screen=Screen(range(200))
        received=[]
        class View:
            def key(self,key,height):received.append(key);return True
        with patch('resourcelens.ui.time.monotonic',return_value=0):
            self.assertTrue(read_input(screen,View()))
            self.assertEqual(received,list(range(64)))
            self.assertEqual(screen.keys[0],64)
            while screen.keys:read_input(screen,View())
        self.assertEqual(received,list(range(200)))
        self.assertEqual(screen.timeout_ms,100)

    def test_mouse_waits_for_new_layout(self):
        screen=Screen([ord('2'),curses.KEY_MOUSE,ord('q')]);received=[]
        class View:
            def key(self,key,height):received.append(key);return True
        with patch('resourcelens.ui.curses.ungetch',side_effect=screen.keys.appendleft):
            read_input(screen,View())
        self.assertEqual(received,[ord('2')])
        read_input(screen,View())
        self.assertEqual(received,[ord('2'),curses.KEY_MOUSE])
        self.assertEqual(screen.keys[0],ord('q'))

    def test_quit_stops_batch_and_restores_timeout(self):
        screen=Screen([ord('q'),ord('x')])
        class View:
            def key(self,key,height):return False
        self.assertFalse(read_input(screen,View()))
        self.assertEqual(screen.keys[0],ord('x'))
        self.assertEqual(screen.timeout_ms,100)

    def test_sanitization_matches_previous_allowlist(self):
        text=''.join(chr(i) for i in range(65536)) + ''.join(GLYPHS)
        expected=''.join(c if 32<=ord(c)<127 or c in GLYPHS else '?' for c in text)
        self.assertEqual(clean(text),expected)


if __name__=='__main__':unittest.main()
