import unittest

from resourcelens.ui import View


GROUP = {"id": "/demo.slice/job.service", "unit": "job.service", "path": "/demo.slice/job.service",
         "manager": "system", "inode": 123, "controllable": True, "cpu_pct": 20}


class ControlUITests(unittest.TestCase):
    def setUp(self):
        self.view = View(control=True)
        self.view.page = 7
        self.view.snapshot = {"workloads": [GROUP]}
        self.view.screen_size = (30, 100)
        self.jobs = []
        self.view.job = lambda kind, function, *args: self.jobs.append((kind, args))

    def test_disabled_controls_never_start_job(self):
        self.view.control = False
        self.view.key(ord("w"))
        self.assertIn("disabled", self.view.modal["message"])
        self.assertFalse(self.jobs)

    def test_edit_preview_and_apply_are_separate_actions(self):
        self.view.key(ord("l"))
        self.assertEqual(self.view.modal["stage"], "input")
        for c in "25":
            self.view.key(ord(c))
        self.assertFalse(self.jobs)
        self.view.key(10)
        self.assertEqual(self.jobs[0][0], "prepare")
        self.assertEqual(self.jobs[0][1][-1], "25")
        plan = {"target": GROUP, "property": "quota", "before": {"quota": None}, "value": 25}
        self.view.modal = {"stage": "confirm", "plan": plan}
        self.view.key(10)
        self.assertEqual(self.jobs[-1][0], "apply")

    def test_cancel_preview_never_applies(self):
        self.view.modal = {"stage": "confirm"}
        self.view.key(27)
        self.assertIsNone(self.view.modal)
        self.assertFalse(self.jobs)

    def test_too_small_preview_cannot_apply(self):
        self.view.screen_size = (10, 40)
        self.view.modal = {"stage": "confirm", "plan": {
            "target": GROUP, "property": "quota", "before": {"quota": None}, "value": 25}}
        self.view.key(10)
        self.assertFalse(self.jobs)

    def test_quit_with_live_settings_is_explicit(self):
        self.view.session.events = [{"status": "confirmed"}]
        self.assertTrue(self.view.key(ord("q")))
        self.assertEqual(self.view.modal["stage"], "quit")
        self.assertTrue(self.view.key(27))
        self.assertIsNone(self.view.modal)
        self.view.key(ord("q"))
        self.assertFalse(self.view.key(10))

    def test_workload_selection_survives_cpu_sort_changes_and_filtering_is_separate(self):
        other = {**GROUP, "id": "/demo.slice/other.service", "unit": "other.service", "cpu_pct": 10}
        self.view.snapshot["workloads"].append(other)
        self.view.workload_rows()
        self.assertEqual(self.view.workload_id, GROUP["id"])
        other["cpu_pct"] = 90
        self.view.workload_rows()
        self.assertEqual(self.view.workload_id, GROUP["id"])
        self.view.key(ord("/"))
        for c in "other":
            self.view.key(ord(c))
        self.view.key(10)
        self.assertEqual(self.view.workload_rows()[0]["unit"], "other.service")
        self.assertEqual(self.view.filter, "")


if __name__ == "__main__":
    unittest.main()
