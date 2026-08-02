import unittest
import json
from pathlib import Path

from bc250.control import CommandResult, GovernorController, PrivilegedRunner


class ControlTests(unittest.TestCase):
    def test_cpu_unlock_uses_only_installed_privileged_helper_action(self):
        calls = []

        def runner(argv):
            calls.append(argv)
            payload = {
                "ok": True,
                "message_id": "helper.cpu_unlock_armed",
                "message_args": {},
                "message": "CPU unlock armed.",
                "reboot_required": True,
            }
            return CommandResult(True, json.dumps(payload), "", 0)

        privileged = PrivilegedRunner(Path("."), runner=runner)
        self.assertTrue(hasattr(privileged, "unlock_cpu"), "unlock_cpu is missing")
        command, payload = privileged.unlock_cpu()

        self.assertTrue(command.ok)
        self.assertTrue(payload["reboot_required"])
        self.assertEqual(
            calls,
            [["pkexec", "/usr/local/libexec/bc250-custom-pannel-privileged", "unlock-cpu"]],
        )

    def test_cpu_mode_toggle_uses_validated_on_or_off_helper_argument(self):
        for enabled, value in ((True, "on"), (False, "off")):
            calls = []

            def runner(argv):
                calls.append(argv)
                payload = {"ok": True, "message_id": "helper.cpu_mode_enabled", "message_args": {}, "message": "ok"}
                return CommandResult(True, json.dumps(payload), "", 0)

            privileged = PrivilegedRunner(Path("."), runner=runner)
            self.assertTrue(hasattr(privileged, "set_cpu_mode"), "set_cpu_mode is missing")

            command, payload = privileged.set_cpu_mode(enabled)

            with self.subTest(enabled=enabled):
                self.assertTrue(command.ok)
                self.assertTrue(payload["ok"])
                self.assertEqual(
                    calls,
                    [[
                        "pkexec",
                        "/usr/local/libexec/bc250-custom-pannel-privileged",
                        "set-cpu-mode",
                        "--enabled",
                        value,
                    ]],
                )

    def test_missing_helper_install_and_cpu_mode_use_one_authentication(self):
        calls = []

        def runner(argv):
            calls.append(argv)
            payload = {"ok": True, "message_id": "helper.cpu_unlock_armed", "message_args": {}, "message": "ok"}
            return CommandResult(True, json.dumps(payload), "", 0)

        privileged = PrivilegedRunner(Path("/project"), runner=runner)
        self.assertTrue(hasattr(privileged, "install_then_set_cpu_mode"), "install_then_set_cpu_mode is missing")

        command, payload = privileged.install_then_set_cpu_mode(True)

        self.assertTrue(command.ok)
        self.assertEqual(payload["message_id"], "helper.cpu_unlock_armed")
        self.assertEqual(len(calls), 1)
        self.assertIn("bc250_install.py", calls[0][2])
        self.assertEqual(calls[0][-2:], ["--cpu-mode", "on"])

    def test_failed_component_install_does_not_attempt_cpu_mode_change(self):
        calls = []

        def runner(argv):
            calls.append(argv)
            payload = {"ok": False, "message_id": "install.service_failed", "message_args": {}, "message": "failed"}
            return CommandResult(False, json.dumps(payload), "failed", 1)

        privileged = PrivilegedRunner(Path("/project"), runner=runner)
        self.assertTrue(hasattr(privileged, "install_then_set_cpu_mode"), "install_then_set_cpu_mode is missing")

        command, payload = privileged.install_then_set_cpu_mode(True)

        self.assertFalse(command.ok)
        self.assertFalse(payload["ok"])
        self.assertEqual(len(calls), 1)

    def test_runtime_apply_uses_validated_dbus_calls(self):
        calls = []

        def runner(argv):
            calls.append(argv)
            return CommandResult(True, "", "", 0)

        result = GovernorController(runner=runner).apply_runtime("eco", 85, 75)
        self.assertTrue(result.ok)
        self.assertEqual(calls[0][-3:], ["uu", "500", "1500"])
        self.assertEqual(calls[1][-3:], ["uu", "85", "75"])

    def test_custom_runtime_apply_uses_user_frequency_range(self):
        calls = []

        def runner(argv):
            calls.append(argv)
            return CommandResult(True, "", "", 0)

        controller = GovernorController(runner=runner)
        self.assertTrue(hasattr(controller, "apply_custom"), "apply_custom is missing")
        result = controller.apply_custom(600, 1750, 85, 75)

        self.assertTrue(result.ok)
        self.assertEqual(calls[0][-3:], ["uu", "600", "1750"])
        self.assertEqual(calls[1][-3:], ["uu", "85", "75"])

    def test_custom_persistent_settings_use_narrow_helper_arguments(self):
        calls = []

        def runner(argv):
            calls.append(argv)
            payload = {"ok": True, "message_id": "helper.governor_saved", "message_args": {}, "message": "saved"}
            return CommandResult(True, json.dumps(payload), "", 0)

        privileged = PrivilegedRunner(Path("."), runner=runner)
        self.assertTrue(hasattr(privileged, "save_custom_settings"), "save_custom_settings is missing")
        privileged.save_custom_settings(600, 1750, 85, 75)

        self.assertEqual(
            calls[0],
            [
                "pkexec", "/usr/local/libexec/bc250-custom-pannel-privileged",
                "save-governor-custom", "--min-mhz", "600", "--max-mhz", "1750",
                "--throttle", "85", "--recovery", "75",
            ],
        )

    def test_invalid_temperature_executes_no_command(self):
        calls = []
        controller = GovernorController(runner=lambda argv: calls.append(argv))
        with self.assertRaises(ValueError):
            controller.apply_runtime("performance", 95, 75)
        self.assertEqual(calls, [])

    def test_first_dbus_failure_stops_second_call(self):
        calls = []

        def runner(argv):
            calls.append(argv)
            return CommandResult(False, "", "denied", 1)

        result = GovernorController(runner=runner).apply_runtime("balanced", 85, 75)
        self.assertFalse(result.ok)
        self.assertEqual(len(calls), 1)

    def test_empty_privileged_response_has_translatable_error_id(self):
        for stderr, expected_message in (("pkexec denied", "pkexec denied"), ("", "No response")):
            with self.subTest(stderr=stderr):
                runner = lambda argv: CommandResult(False, "", stderr, 1)
                _result, payload = PrivilegedRunner(Path("."), runner=runner).install()
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["message_id"], "error.no_response")
                self.assertEqual(payload["message_args"], {})
                self.assertEqual(payload["message"], expected_message)


if __name__ == "__main__":
    unittest.main()
