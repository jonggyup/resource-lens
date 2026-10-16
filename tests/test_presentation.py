import curses
import unittest

from resourcelens.presentation import card_layout, chart
from resourcelens.ui import View, clean


class PresentationTests(unittest.TestCase):
    def test_chart_preserves_scale_zero_missing_and_peak(self):
        self.assertEqual(chart([0, None, 100], 3, 2, unicode=False), ["  #", ". #"])
        self.assertEqual(chart([0, None, 100], 3, 2, unicode=True), ["  █", "· █"])

    def test_chart_scales_network_rates_and_keeps_newest_points(self):
        self.assertEqual(chart([9000, 0, 1000], 2, 2, maximum=1000, unicode=False), [" #", ".#"])
        self.assertEqual(chart([1000], 3, 2, maximum=1000, unicode=True), ["  █", "  █"])
        self.assertEqual(chart([0, 1, 50, 100], 4, 1, unicode=False), ["..=@"])

    def test_missing_chart_is_a_message_not_a_zero_curve(self):
        rows = chart([None] * 100, 30, 3)
        self.assertIn("No measured data", rows[1])
        self.assertNotIn("█", "".join(rows))
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(len(row) == 30 for row in rows))

    def test_cards_fit_without_overlap_or_footer_collision(self):
        for height, width in ((35, 120), (24, 80), (23, 76), (50, 160)):
            boxes = card_layout(height, width)
            self.assertEqual(len(boxes), 6)
            used = set()
            for y, x, h, w in boxes:
                self.assertGreaterEqual(h, 6)
                self.assertGreaterEqual(y, 3)
                self.assertLessEqual(y + h, height - 2)
                self.assertLess(x + w, width)
                region = {(a, b) for a in range(y, y + h) for b in range(x, x + w)}
                self.assertFalse(used & region)
                used |= region
        self.assertEqual(card_layout(12, 40), [])
        self.assertEqual(card_layout(22, 120), [])

    def test_narrow_process_table_retains_name_and_resource_values(self):
        view = View()
        view.snapshot = {"processes": [{"pid": 1234, "name": "my-server", "cpu_pct": 50,
                                       "memory": 1024, "memory_pct": 1, "nice": 0, "state": "S"}],
                         "processes_skipped": 0}
        row = view.process_lines(37)[3]
        self.assertIn("my-server", row)
        self.assertIn("50.0%", row)
        self.assertIn("1.0KiB", row)
        self.assertLessEqual(len(row), 37)

    def test_mouse_selects_visible_tab_and_resets_scroll(self):
        view = View()
        view.tab_regions = [(1, 8, 0), (9, 16, 1)]
        view.scroll = 20
        view.mouse(10, 1, curses.BUTTON1_PRESSED)
        self.assertEqual((view.page, view.scroll), (1, 0))
        view.mouse(10, 2, curses.BUTTON1_PRESSED)
        self.assertEqual(view.page, 1)

    def test_graphics_survive_sanitization_but_escape_sequences_do_not(self):
        self.assertEqual(clean("┌─┐ ▁▂█"), "┌─┐ ▁▂█")
        self.assertEqual(clean("\x1b[2J\n"), "?[2J?")


if __name__ == "__main__":
    unittest.main()
