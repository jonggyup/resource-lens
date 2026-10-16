from copy import deepcopy
import subprocess
import unittest

from resourcelens.controls import ControlError, ControlSession, SystemdBackend, duration_usec, validate_value


TARGET = {"unit": "owned-test.service", "path": "/demo.slice/owned-test.service", "manager": "system", "inode": 123, "controllable": True}


class FakeBackend:
    def __init__(self):
        self.info = {"path": TARGET["path"], "inode": 123, "invocation": "instance-a", "weight": 100,
                     "quota": None, "kernel_weight": 100, "kernel_quota": None, "kernel_max": "max 100000"}
        self.calls = []
        self.deny = False

    def snapshot(self, target):
        return deepcopy(self.info)

    def set(self, target, property, value):
        self.calls.append((property, value))
        if self.deny:
            raise ControlError("Access denied")
        self.info[property] = value
        if property == "weight":
            self.info["kernel_weight"] = value if value is not None else 100
        else:
            self.info["kernel_quota"] = value
            self.info["kernel_max"] = f"{int(value * 1000)} 100000" if value is not None else "max 100000"


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.session = ControlSession(self.backend)

    def test_preview_never_changes_resources(self):
        plan = self.session.prepare(TARGET, "weight", "400")
        self.assertEqual(plan["value"], 400)
        self.assertEqual(self.backend.calls, [])

    def test_apply_and_lifo_undo_restore_previous_values(self):
        self.session.apply(self.session.prepare(TARGET, "weight", "400"))
        self.session.apply(self.session.prepare(TARGET, "quota", "25%"))
        self.assertEqual(self.backend.info["quota"], 25)
        self.session.undo()
        self.assertIsNone(self.backend.info["quota"])
        self.assertEqual(self.backend.info["weight"], 400)
        self.session.undo()
        self.assertEqual(self.backend.info["weight"], 100)
        self.assertTrue(all(event["status"] == "undone" for event in self.session.events))

    def test_external_change_after_preview_refuses_mutation(self):
        plan = self.session.prepare(TARGET, "weight", "400")
        self.backend.info["weight"] = 300
        with self.assertRaisesRegex(ControlError, "changed after preview"):
            self.session.apply(plan)
        self.assertEqual(self.backend.calls, [])

    def test_direct_kernel_change_after_preview_refuses_mutation(self):
        plan = self.session.prepare(TARGET, "weight", "400")
        self.backend.info["kernel_weight"] = 300
        with self.assertRaisesRegex(ControlError, "Kernel CPU settings changed"):
            self.session.apply(plan)

    def test_existing_untracked_kernel_setting_is_not_overwritten(self):
        self.backend.info["kernel_max"] = "20000 100000"
        self.backend.info["kernel_quota"] = 20
        with self.assertRaisesRegex(ControlError, "untracked"):
            self.session.prepare(TARGET, "weight", "400")
        self.assertFalse(self.backend.calls)

    def test_undo_does_not_overwrite_another_actor(self):
        self.session.apply(self.session.prepare(TARGET, "weight", "400"))
        self.backend.info["weight"] = 500
        self.backend.info["kernel_weight"] = 500
        with self.assertRaisesRegex(ControlError, "another actor"):
            self.session.undo()
        self.assertEqual(self.backend.info["weight"], 500)
        self.assertEqual(len(self.backend.calls), 1)

    def test_restarted_unit_refuses_apply_and_undo(self):
        plan = self.session.prepare(TARGET, "quota", "20")
        self.backend.info["invocation"] = "instance-b"
        with self.assertRaises(ControlError):
            self.session.apply(plan)
        self.backend.info["invocation"] = "instance-a"
        self.session.apply(plan)
        self.backend.info["inode"] = 999
        with self.assertRaisesRegex(ControlError, "replaced"):
            self.session.undo()

    def test_permission_denial_is_not_success_and_can_be_cleared_without_retry(self):
        plan = self.session.prepare(TARGET, "quota", "20")
        self.backend.deny = True
        with self.assertRaisesRegex(ControlError, "denied"):
            self.session.apply(plan)
        self.assertEqual(self.session.events[0]["status"], "unconfirmed")
        self.session.undo()
        self.assertEqual(self.session.events[0]["status"], "undone")
        self.assertEqual(len(self.backend.calls), 1)

    def test_manager_only_partial_change_can_be_undone_if_kernel_is_unchanged(self):
        plan = self.session.prepare(TARGET, "weight", "400")
        def manager_only(target, property, value):
            self.backend.info[property] = value
        self.backend.set = manager_only
        with self.assertRaises(ControlError):
            self.session.apply(plan)
        self.session.undo()
        self.assertEqual(self.backend.info["weight"], 100)
        self.assertEqual(self.session.events[-1]["status"], "undone")

    def test_false_success_is_unconfirmed_when_kernel_disagrees(self):
        plan = self.session.prepare(TARGET, "weight", "400")
        def lie(target, property, value):
            self.backend.info[property] = value
        self.backend.set = lie
        with self.assertRaisesRegex(ControlError, "not confirmed"):
            self.session.apply(plan)
        self.assertEqual(self.session.events[0]["status"], "unconfirmed")

    def test_unchanged_setting_is_a_noop(self):
        with self.assertRaisesRegex(ControlError, "already"):
            self.session.prepare(TARGET, "weight", "100")
        self.assertFalse(self.backend.calls)

    def test_unset_weight_is_restored_after_controller_activation(self):
        self.backend.info.update(weight=None, kernel_weight=None, kernel_max=None)
        self.session.apply(self.session.prepare(TARGET, "weight", "400"))
        self.session.undo()
        self.assertIsNone(self.backend.info["weight"])
        self.assertEqual(self.backend.info["kernel_weight"], 100)

    def test_input_validation(self):
        for property, value in (("weight", "0"), ("weight", "10001"), ("quota", "nan"),
                                ("quota", "-5"), ("quota", "20; reboot"), ("quota", "20.5"), ("io", "1")):
            with self.assertRaises(ControlError):
                validate_value(property, value)
        self.assertIsNone(validate_value("quota", "max"))
        self.assertIsNone(validate_value("weight", "default"))

    def test_systemd_duration_units(self):
        self.assertIsNone(duration_usec("infinity"))
        self.assertEqual(duration_usec("250ms"), 250000)
        self.assertEqual(duration_usec("1s 500ms"), 1500000)
        self.assertEqual(duration_usec("1.5s"), 1500000)
        with self.assertRaises(ControlError):
            duration_usec("unknown")

    def test_systemctl_is_runtime_only_and_uses_argument_vector(self):
        commands = []
        def runner(command, **kwargs):
            commands.append(command)
            self.assertNotIn("shell", kwargs)
            return subprocess.CompletedProcess(command, 0, "", "")
        backend = SystemdBackend(runner=runner)
        backend.binary = "/fixture/systemctl"
        backend.set(TARGET, "quota", 25)
        self.assertIn("--runtime", commands[0])
        self.assertIn("--no-ask-password", commands[0])
        self.assertEqual(commands[0][-2:], [TARGET["unit"], "CPUQuota=25%"])
        backend.set({**TARGET, "manager": "user"}, "weight", None)
        self.assertIn("--user", commands[-1])
        self.assertEqual(commands[-1][-1], "CPUWeight=")

    def test_invalid_unit_never_reaches_systemctl(self):
        backend = SystemdBackend(runner=lambda *a, **k: self.fail("must not execute"))
        for name in ("--all.service", "../../danger.service", "bad\n.service"):
            with self.assertRaises(ControlError):
                backend.set({**TARGET, "unit": name}, "weight", 100)


if __name__ == "__main__":
    unittest.main()
