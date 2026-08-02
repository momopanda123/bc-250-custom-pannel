import importlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

from bc250.control import CommandResult


class MappingRunner:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, argv):
        key = tuple(argv)
        self.calls.append(key)
        return self.responses.get(key, CommandResult(False, "", f"unexpected command: {key}", 1))


class PowerTests(unittest.TestCase):
    def test_inspect_distinguishes_hardware_idle_from_desktop_sleep(self):
        spec = importlib.util.find_spec("bc250.power")
        self.assertIsNotNone(spec, "bc250.power is missing")
        power = importlib.import_module("bc250.power")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            driver = root / "cpuidle/current_driver"
            driver.parent.mkdir(parents=True)
            driver.write_text("none\n", encoding="utf-8")
            cpuinfo = root / "cpuinfo"
            cpuinfo.write_text("flags : fpu monitor mwaitx\n", encoding="utf-8")
            dpm = root / "drm/card1/device/power_dpm_force_performance_level"
            dpm.parent.mkdir(parents=True)
            dpm.write_text("auto\n", encoding="utf-8")

            responses = {
                ("gsettings", "get", power.POWER_SCHEMA, "sleep-inactive-ac-type"): CommandResult(True, "'nothing'", "", 0),
                ("gsettings", "get", power.POWER_SCHEMA, "sleep-inactive-battery-type"): CommandResult(True, "'nothing'", "", 0),
                ("gsettings", "get", power.POWER_SCHEMA, "sleep-inactive-ac-timeout"): CommandResult(True, "0", "", 0),
                ("gsettings", "get", power.POWER_SCHEMA, "sleep-inactive-battery-timeout"): CommandResult(True, "0", "", 0),
                ("gsettings", "get", power.SESSION_SCHEMA, "idle-delay"): CommandResult(True, "uint32 0", "", 0),
                ("gsettings", "get", power.SCREENSAVER_SCHEMA, "idle-activation-enabled"): CommandResult(True, "false", "", 0),
                ("gsettings", "get", power.POWER_SCHEMA, "idle-dim"): CommandResult(True, "false", "", 0),
            }
            controller = power.PowerController(
                runner=MappingRunner(responses),
                cpuidle_driver=driver,
                cpuinfo=cpuinfo,
                drm_root=root / "drm",
            )

            state = controller.inspect()

        self.assertEqual(state.cpu_idle_mode, "MWAIT")
        self.assertEqual(state.gpu_dpm_mode, "auto")
        self.assertTrue(state.suspend_blocked)
        self.assertTrue(state.display_blank_blocked)
        self.assertTrue(hasattr(state, "suspend_minutes"), "suspend timeout state is missing")
        self.assertTrue(hasattr(state, "display_minutes"), "display timeout state is missing")
        self.assertEqual(state.suspend_minutes, 0)
        self.assertEqual(state.display_minutes, 0)

    def test_inspect_reports_active_suspend_and_display_timeout_minutes(self):
        power = importlib.import_module("bc250.power")
        responses = {
            ("gsettings", "get", power.POWER_SCHEMA, "sleep-inactive-ac-type"): CommandResult(True, "'suspend'", "", 0),
            ("gsettings", "get", power.POWER_SCHEMA, "sleep-inactive-battery-type"): CommandResult(True, "'suspend'", "", 0),
            ("gsettings", "get", power.POWER_SCHEMA, "sleep-inactive-ac-timeout"): CommandResult(True, "1800", "", 0),
            ("gsettings", "get", power.POWER_SCHEMA, "sleep-inactive-battery-timeout"): CommandResult(True, "1800", "", 0),
            ("gsettings", "get", power.SESSION_SCHEMA, "idle-delay"): CommandResult(True, "uint32 600", "", 0),
            ("gsettings", "get", power.SCREENSAVER_SCHEMA, "idle-activation-enabled"): CommandResult(True, "true", "", 0),
            ("gsettings", "get", power.POWER_SCHEMA, "idle-dim"): CommandResult(True, "true", "", 0),
        }

        state = power.PowerController(runner=MappingRunner(responses)).inspect()

        self.assertFalse(state.suspend_blocked)
        self.assertFalse(state.display_blank_blocked)
        self.assertTrue(hasattr(state, "suspend_minutes"), "suspend timeout state is missing")
        self.assertTrue(hasattr(state, "display_minutes"), "display timeout state is missing")
        self.assertEqual(state.suspend_minutes, 30)
        self.assertEqual(state.display_minutes, 10)

    def test_set_suspend_timeout_writes_modes_and_seconds(self):
        power = importlib.import_module("bc250.power")
        self.assertTrue(
            hasattr(power.PowerController, "set_suspend_timeout"),
            "timed suspend control is missing",
        )
        expected = [
            ("gsettings", "set", power.POWER_SCHEMA, "sleep-inactive-ac-type", "suspend"),
            ("gsettings", "set", power.POWER_SCHEMA, "sleep-inactive-battery-type", "suspend"),
            ("gsettings", "set", power.POWER_SCHEMA, "sleep-inactive-ac-timeout", "1800"),
            ("gsettings", "set", power.POWER_SCHEMA, "sleep-inactive-battery-timeout", "1800"),
        ]
        runner = MappingRunner({command: CommandResult(True, "", "", 0) for command in expected})

        result = power.PowerController(runner=runner).set_suspend_timeout(30)

        self.assertTrue(result.ok, result.stderr)
        self.assertEqual(runner.calls, expected)

    def test_set_display_timeout_uses_minutes_or_blocks_blanking(self):
        power = importlib.import_module("bc250.power")
        self.assertTrue(
            hasattr(power.PowerController, "set_display_timeout"),
            "timed display control is missing",
        )
        cases = (
            (
                10,
                [
                    ("gsettings", "set", power.SESSION_SCHEMA, "idle-delay", "uint32 600"),
                    ("gsettings", "set", power.SCREENSAVER_SCHEMA, "idle-activation-enabled", "true"),
                    ("gsettings", "set", power.POWER_SCHEMA, "idle-dim", "true"),
                ],
            ),
            (
                0,
                [
                    ("gsettings", "set", power.SESSION_SCHEMA, "idle-delay", "uint32 0"),
                    ("gsettings", "set", power.SCREENSAVER_SCHEMA, "idle-activation-enabled", "false"),
                    ("gsettings", "set", power.POWER_SCHEMA, "idle-dim", "false"),
                ],
            ),
        )
        for minutes, expected in cases:
            runner = MappingRunner({command: CommandResult(True, "", "", 0) for command in expected})

            result = power.PowerController(runner=runner).set_display_timeout(minutes)

            with self.subTest(minutes=minutes):
                self.assertTrue(result.ok, result.stderr)
                self.assertEqual(runner.calls, expected)

    def test_timeout_minutes_reject_values_outside_custom_range(self):
        power = importlib.import_module("bc250.power")
        self.assertTrue(
            hasattr(power.PowerController, "set_suspend_timeout"),
            "timed suspend control is missing",
        )
        self.assertTrue(
            hasattr(power.PowerController, "set_display_timeout"),
            "timed display control is missing",
        )
        controller = power.PowerController(runner=MappingRunner({}))
        for minutes in (-1, 241):
            with self.subTest(minutes=minutes), self.assertRaises(ValueError):
                controller.set_suspend_timeout(minutes)
            with self.subTest(minutes=minutes), self.assertRaises(ValueError):
                controller.set_display_timeout(minutes)

    def test_set_suspend_blocked_writes_both_ac_and_battery_modes(self):
        power = importlib.import_module("bc250.power")
        self.assertTrue(hasattr(power.PowerController, "set_suspend_blocked"), "suspend control is missing")
        for blocked, value in ((True, "nothing"), (False, "suspend")):
            expected = [
                ("gsettings", "set", power.POWER_SCHEMA, "sleep-inactive-ac-type", value),
                ("gsettings", "set", power.POWER_SCHEMA, "sleep-inactive-battery-type", value),
            ]
            runner = MappingRunner({command: CommandResult(True, "", "", 0) for command in expected})

            result = power.PowerController(runner=runner).set_suspend_blocked(blocked)

            with self.subTest(blocked=blocked):
                self.assertTrue(result.ok, result.stderr)
                self.assertEqual(runner.calls, expected)

    def test_set_display_blank_blocked_writes_idle_screensaver_and_dim_values(self):
        power = importlib.import_module("bc250.power")
        self.assertTrue(
            hasattr(power.PowerController, "set_display_blank_blocked"),
            "display blanking control is missing",
        )
        cases = (
            (True, ("uint32 0", "false", "false")),
            (False, ("uint32 300", "true", "true")),
        )
        for blocked, values in cases:
            expected = [
                ("gsettings", "set", power.SESSION_SCHEMA, "idle-delay", values[0]),
                ("gsettings", "set", power.SCREENSAVER_SCHEMA, "idle-activation-enabled", values[1]),
                ("gsettings", "set", power.POWER_SCHEMA, "idle-dim", values[2]),
            ]
            runner = MappingRunner({command: CommandResult(True, "", "", 0) for command in expected})

            result = power.PowerController(runner=runner).set_display_blank_blocked(blocked)

            with self.subTest(blocked=blocked):
                self.assertTrue(result.ok, result.stderr)
                self.assertEqual(runner.calls, expected)


if __name__ == "__main__":
    unittest.main()
