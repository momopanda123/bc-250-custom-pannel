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
            controller = power.PowerController(desktop="gnome",
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

        state = power.PowerController(desktop="gnome", runner=MappingRunner(responses)).inspect()

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

        result = power.PowerController(desktop="gnome", runner=runner).set_suspend_timeout(30)

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

            result = power.PowerController(desktop="gnome", runner=runner).set_display_timeout(minutes)

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
        controller = power.PowerController(desktop="gnome", runner=MappingRunner({}))
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

            result = power.PowerController(desktop="gnome", runner=runner).set_suspend_blocked(blocked)

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

            result = power.PowerController(desktop="gnome", runner=runner).set_display_blank_blocked(blocked)

            with self.subTest(blocked=blocked):
                self.assertTrue(result.ok, result.stderr)
                self.assertEqual(runner.calls, expected)


class KdePowerTests(unittest.TestCase):
    """Regression fixtures follow PowerDevil's 5.27 and Plasma 6 schemas.

    These tests exercise files and command contracts, not a live KDE session.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.calls = []
        self.fail_key = None
        self.fail_refresh = False

    def runner(self, argv):
        import configparser
        from bc250.power import KDE_REFRESH
        self.calls.append(tuple(argv))
        if tuple(argv[1:]) == KDE_REFRESH:
            return CommandResult(not self.fail_refresh, "", "offline" if self.fail_refresh else "", 1 if self.fail_refresh else 0)
        if argv[0] not in ("kwriteconfig5", "kwriteconfig6"):
            raise AssertionError(f"Unexpected command: {argv}")
        key = argv[argv.index("--key") + 1]
        if key == self.fail_key:
            return CommandResult(False, "", "write denied", 1)
        filename = Path(argv[argv.index("--file") + 1])
        groups = [argv[i + 1] for i, value in enumerate(argv) if value == "--group"]
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(filename)
        section = "][".join(groups)
        if not parser.has_section(section):
            parser.add_section(section)
        parser.set(section, key, argv[-1])
        with filename.open("w") as stream:
            parser.write(stream)
        return CommandResult(True, "", "", 0)

    def controller(self, version=6, **kwargs):
        from bc250.power import PowerController
        options = dict(desktop="KDE", config_home=self.root, plasma_version=version,
                       kwriteconfig=f"kwriteconfig{version}", busctl="busctl", runner=self.runner)
        options.update(kwargs)
        return PowerController(**options)

    def fixture(self, version, content):
        from bc250.power import KDE_CONFIG_FILES
        (self.root / KDE_CONFIG_FILES[version]).write_text(content, encoding="utf-8")

    def test_plasma5_reads_suspend_milliseconds_and_display_seconds(self):
        self.fixture(5, "[AC][SuspendSession]\nsuspendType=1\nidleTime=1800000\n"
                        "[AC][DPMSControl]\nidleTime=600\n")
        state = self.controller(5).inspect()
        self.assertEqual((state.suspend_minutes, state.display_minutes), (30, 10))
        self.assertFalse(state.suspend_blocked)
        self.assertFalse(state.display_blank_blocked)

    def test_plasma6_reads_only_new_schema_even_with_stale_plasma5_file(self):
        self.fixture(5, "[AC][SuspendSession]\nsuspendType=1\nidleTime=1800000\n")
        self.fixture(6, "[AC][SuspendAndShutdown]\nAutoSuspendAction=1\nAutoSuspendIdleTimeoutSec=900\n"
                        "[AC][Display]\nTurnOffDisplayWhenIdle=true\nTurnOffDisplayIdleTimeoutSec=300\n")
        state = self.controller().inspect()
        self.assertEqual((state.suspend_minutes, state.display_minutes), (15, 5))

    def test_plasma5_writes_exact_units_and_ram_action_to_new_file(self):
        controller = self.controller(5)
        self.assertTrue(controller.set_suspend_timeout(30).ok)
        self.assertTrue(controller.set_display_timeout(10).ok)
        writes = {(c[c.index("--group") + 3], c[c.index("--key") + 1]): c[-1]
                  for c in self.calls if c[0].startswith("kwriteconfig")}
        self.assertEqual(writes[("SuspendSession", "idleTime")], "1800000")
        self.assertEqual(writes[("SuspendSession", "suspendType")], "1")
        self.assertEqual(writes[("SuspendSession", "suspendThenHibernate")], "false")
        self.assertEqual(writes[("DPMSControl", "idleTime")], "600")
        self.assertEqual(controller.inspect().display_minutes, 10)
        self.assertFalse((self.root / "powerdevilrc").exists())

    def test_plasma6_writes_seconds_and_explicit_enable_flags(self):
        controller = self.controller()
        self.assertTrue(controller.set_suspend_timeout(30).ok)
        self.assertTrue(controller.set_display_timeout(10).ok)
        text = (self.root / "powerdevilrc").read_text()
        for value in ("AutoSuspendAction = 1", "SleepMode = 1", "AutoSuspendIdleTimeoutSec = 1800",
                      "TurnOffDisplayWhenIdle = true", "TurnOffDisplayIdleTimeoutSec = 600"):
            self.assertIn(value, text)
        self.assertFalse((self.root / "powermanagementprofilesrc").exists())
        state = controller.inspect()
        self.assertEqual((state.suspend_minutes, state.display_minutes), (30, 10))

    def test_never_roundtrips_for_both_actions_and_versions(self):
        for version in (5, 6):
            with self.subTest(version=version):
                controller = self.controller(version)
                self.assertTrue(controller.set_suspend_timeout(30).ok)
                self.assertTrue(controller.set_display_timeout(10).ok)
                self.assertTrue(controller.set_suspend_timeout(0).ok)
                self.assertTrue(controller.set_display_timeout(0).ok)
                state = controller.inspect()
                self.assertEqual((state.suspend_minutes, state.display_minutes), (0, 0))
                self.assertTrue(state.suspend_blocked)
                self.assertTrue(state.display_blank_blocked)
                self.assertFalse(any("--delete" in command for command in self.calls))

    def test_reenable_overwrites_old_hibernate_action(self):
        for version, text in ((5, "[AC][SuspendSession]\nsuspendType=2\nidleTime=60000\nsuspendThenHibernate=true\n"),
                              (6, "[AC][SuspendAndShutdown]\nAutoSuspendAction=2\nAutoSuspendIdleTimeoutSec=60\nSleepMode=3\n")):
            with self.subTest(version=version):
                self.fixture(version, text)
                controller = self.controller(version)
                self.assertIsNone(controller.inspect().suspend_minutes)
                self.assertTrue(controller.set_suspend_timeout(15).ok)
                self.assertEqual(controller.inspect().suspend_minutes, 15)
                self.assertTrue(controller.set_suspend_blocked(True).ok)
                self.assertEqual(controller.inspect().suspend_minutes, 0)
                self.assertTrue(controller.set_suspend_blocked(False).ok)
                self.assertEqual(controller.inspect().suspend_minutes, 30)

    def test_plasma6_disable_flags_override_stored_positive_timeouts(self):
        self.fixture(6, "[AC][SuspendAndShutdown]\nAutoSuspendAction=0\nAutoSuspendIdleTimeoutSec=1800\n"
                        "[AC][Display]\nTurnOffDisplayWhenIdle=false\nTurnOffDisplayIdleTimeoutSec=600\n")
        state = self.controller().inspect()
        self.assertEqual((state.suspend_minutes, state.display_minutes), (0, 0))

    def test_plasma5_suspend_needs_action_not_only_timer(self):
        self.fixture(5, "[AC][SuspendSession]\nidleTime=1800000\n")
        self.assertIsNone(self.controller(5).inspect().suspend_minutes)

    def test_missing_config_remains_unknown_not_never(self):
        for version in (5, 6):
            state = self.controller(version).inspect()
            self.assertIsNone(state.suspend_minutes)
            self.assertIsNone(state.display_minutes)
            self.assertFalse(state.suspend_blocked)
            self.assertFalse(state.display_blank_blocked)

    def test_invalid_config_and_values_do_not_crash(self):
        for version in (5, 6):
            self.fixture(version, "this is not a group")
            self.assertIsNone(self.controller(version).inspect().suspend_minutes)
        self.fixture(5, "[AC][SuspendSession]\nsuspendType=1\nidleTime=oops%\n[AC][DPMSControl]\nidleTime=-1\n")
        state = self.controller(5).inspect()
        self.assertIsNone(state.suspend_minutes)
        self.assertIsNone(state.display_minutes)
        self.fixture(6, "[AC][SuspendAndShutdown]\nAutoSuspendAction=1\nAutoSuspendIdleTimeoutSec=0\n"
                        "[AC][Display]\nTurnOffDisplayWhenIdle=true\nTurnOffDisplayIdleTimeoutSec=0\n")
        state = self.controller().inspect()
        self.assertIsNone(state.suspend_minutes)
        self.assertIsNone(state.display_minutes)

    def test_positive_subminute_time_is_not_never(self):
        self.fixture(5, "[AC][SuspendSession]\nsuspendType=1\nidleTime=1000\n[AC][DPMSControl]\nidleTime=30\n")
        state = self.controller(5).inspect()
        self.assertEqual((state.suspend_minutes, state.display_minutes), (1, 1))

    def test_refresh_after_writes_for_both_versions(self):
        from bc250.power import KDE_REFRESH
        for version in (5, 6):
            for method in ("set_suspend_timeout", "set_display_timeout"):
                self.calls.clear()
                self.assertTrue(getattr(self.controller(version), method)(15).ok)
                self.assertEqual(self.calls[-1], ("busctl", *KDE_REFRESH))
                self.assertTrue(all(c[0] == f"kwriteconfig{version}" for c in self.calls[:-1]))

    def test_refresh_failure_is_not_reported_as_success_or_auth_cancel(self):
        self.fail_refresh = True
        result = self.controller().set_display_timeout(10)
        self.assertFalse(result.ok)
        self.assertIn("saved", result.stderr)
        self.assertIn("PowerDevil", result.stderr)
        self.assertNotIn(result.returncode, (126, 127))

    def test_write_failure_stops_and_reports_partial_save(self):
        self.fail_key = "AutoSuspendIdleTimeoutSec"
        result = self.controller().set_suspend_timeout(10)
        self.assertFalse(result.ok)
        self.assertIn("partially saved", result.stderr)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.controller().inspect().suspend_minutes, 0)

    def test_missing_writer_causes_no_writes(self):
        from unittest.mock import patch
        for version in (5, 6):
            with patch("bc250.power.shutil.which", return_value=None):
                result = self.controller(version, kwriteconfig=None).set_suspend_timeout(30)
            self.assertFalse(result.ok)
            self.assertIn(f"kwriteconfig{version}", result.stderr)
            self.assertNotIn(result.returncode, (126, 127))
            self.assertEqual(self.calls, [])

    def test_missing_busctl_fails_before_any_writes(self):
        from unittest.mock import patch
        with patch("bc250.power.shutil.which", return_value=None):
            result = self.controller(busctl=None).set_display_timeout(30)
        self.assertFalse(result.ok)
        self.assertIn("busctl", result.stderr)
        self.assertEqual(self.calls, [])

    def test_session_version_selects_matching_writer_when_both_installed(self):
        from unittest.mock import patch
        from bc250.power import PowerController
        for version in (5, 6):
            with patch.dict("os.environ", {"KDE_SESSION_VERSION": str(version)}), \
                 patch("bc250.power.shutil.which", side_effect=lambda name: name):
                controller = PowerController(desktop="KDE", runner=self.runner, config_home=self.root)
                self.calls.clear()
                self.assertTrue(controller.set_display_timeout(10).ok)
                self.assertEqual(self.calls[0][0], f"kwriteconfig{version}")

    def test_plasmashell_version_fallback_is_cached(self):
        from unittest.mock import patch
        from bc250.power import PowerController
        runner = MappingRunner({("plasmashell", "--version"): CommandResult(True, "plasmashell 6.4.5", "", 0)})
        with patch.dict("os.environ", {"KDE_SESSION_VERSION": ""}), \
             patch("bc250.power.shutil.which", side_effect=lambda name: name):
            controller = PowerController(desktop="KDE", runner=runner)
            self.assertEqual(controller._kde_version(), 6)
            self.assertEqual(controller._kde_version(), 6)
        self.assertEqual(runner.calls, [("plasmashell", "--version")])

    def test_unknown_version_does_not_guess_from_installed_tools(self):
        from unittest.mock import patch
        from bc250.power import PowerController
        with patch.dict("os.environ", {"KDE_SESSION_VERSION": ""}), \
             patch("bc250.power.shutil.which", side_effect=lambda name: None if name == "plasmashell" else name):
            controller = PowerController(desktop="KDE", config_home=self.root, runner=self.runner)
            result = controller.set_display_timeout(10)
            self.assertIsNone(controller.inspect().display_minutes)
        self.assertFalse(result.ok)
        self.assertIn("identify", result.stderr)
        self.assertEqual(self.calls, [])

    def test_bad_version_command_does_not_enable_write(self):
        from unittest.mock import patch
        from bc250.power import PowerController
        for result in (CommandResult(False, "plasmashell 6.4", "broken", 1), CommandResult(True, "unexpected", "", 0)):
            runner = MappingRunner({("plasmashell", "--version"): result})
            with patch.dict("os.environ", {"KDE_SESSION_VERSION": ""}), \
                 patch("bc250.power.shutil.which", side_effect=lambda name: name):
                controller = PowerController(desktop="KDE", runner=runner)
                self.assertFalse(controller.set_suspend_timeout(30).ok)
                self.assertEqual(runner.calls, [("plasmashell", "--version")])

    def test_custom_config_home_used_for_reads_and_writes(self):
        controller = self.controller()
        self.assertTrue(controller.set_display_timeout(15).ok)
        self.assertEqual(self.calls[0][self.calls[0].index("--file") + 1], str(self.root / "powerdevilrc"))
        self.assertEqual(controller.inspect().display_minutes, 15)

    def test_write_preserves_other_profiles_and_settings(self):
        self.fixture(6, "[Battery][Display]\nTurnOffDisplayIdleTimeoutSec=60\n[AC][Display]\nDisplayBrightness=42\n")
        self.assertTrue(self.controller().set_display_timeout(10).ok)
        text = (self.root / "powerdevilrc").read_text()
        self.assertIn("[Battery][Display]", text)
        self.assertIn("TurnOffDisplayIdleTimeoutSec = 60\n", text)
        self.assertIn("DisplayBrightness = 42", text)

    def test_display_toggle_roundtrips(self):
        for version in (5, 6):
            controller = self.controller(version)
            self.assertTrue(controller.set_display_blank_blocked(True).ok)
            self.assertEqual(controller.inspect().display_minutes, 0)
            self.assertTrue(controller.set_display_blank_blocked(False).ok)
            self.assertEqual(controller.inspect().display_minutes, 5)

    def test_invalid_minutes_do_not_write(self):
        for version in (5, 6):
            controller = self.controller(version)
            for method in (controller.set_suspend_timeout, controller.set_display_timeout):
                for minutes in (-1, 241):
                    with self.subTest(version=version, minutes=minutes), self.assertRaises(ValueError):
                        method(minutes)
        self.assertEqual(self.calls, [])

    def test_desktop_detection_uses_case_insensitive_tokens(self):
        from bc250.power import PowerController
        for desktop in ("KDE", "kde", "Kde", "ubuntu:KDE"):
            self.assertTrue(PowerController(desktop=desktop)._is_kde())
        for desktop in ("GNOME", "ubuntu", "", "notkde"):
            self.assertFalse(PowerController(desktop=desktop)._is_kde())


if __name__ == "__main__":
    unittest.main()
