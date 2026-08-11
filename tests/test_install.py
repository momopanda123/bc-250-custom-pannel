import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bc250_install as install_module
from bc250.bootstrap import PlatformReport
from bc250.messages import UserMessage
from bc250_install import extend_governor_curve, install_bundle, remove_bundle, service_commands


class InstallTests(unittest.TestCase):
    SUPPORTED_PLATFORM = PlatformReport(
        True,
        True,
        "x86_64",
        True,
        UserMessage("platform.ready"),
    )

    def _path_with_system_root(self, value):
        return self.root if value == "/" else Path(value)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.project = base / "project"
        self.root = base / "root"
        (self.project / "vendor").mkdir(parents=True)
        self.payload = self.project / "vendor/tool"
        self.payload.write_text("tool-data", encoding="utf-8")
        (self.project / "VENDOR-MANIFEST.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "components": [
                        {
                            "name": "test-tool",
                            "path": "vendor/tool",
                            "version": "1",
                            "source": "fixture",
                            "sha256": hashlib.sha256(self.payload.read_bytes()).hexdigest(),
                            "license": "MIT",
                            "target": "test",
                            "install_path": "/opt/bc250-custom-pannel/tool",
                            "mode": "0755",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_copies_only_manifest_file_with_mode(self):
        result = install_bundle(self.project, self.root, manage_services=False)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["message_id"], "install.complete")
        self.assertEqual(result["message_args"], {})
        self.assertEqual(result["message"], "번들 구성요소 설치 완료")
        installed = self.root / "opt/bc250-custom-pannel/tool"
        self.assertEqual(installed.read_text(encoding="utf-8"), "tool-data")
        mode = installed.stat().st_mode & 0o777
        if os.name == "nt":
            self.assertTrue(mode & 0o200)
        else:
            self.assertEqual(mode, 0o755)

    def test_bad_hash_prevents_all_writes(self):
        self.payload.write_text("tampered", encoding="utf-8")
        result = install_bundle(self.project, self.root, manage_services=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["message_id"], "error.bundle_invalid")
        self.assertEqual(result["message_args"], {"detail": result["message"]})
        self.assertIn("SHA-256", result["message"])
        self.assertFalse((self.root / "opt").exists())

    def test_service_commands_have_exact_required_order(self):
        self.assertEqual(
            service_commands(),
            [
                ["systemctl", "daemon-reload"],
                [
                    "busctl", "--system", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
                    "org.freedesktop.DBus", "ReloadConfig",
                ],
                ["systemctl", "enable", "cyan-skillfish-governor-smu.service"],
                ["systemctl", "restart", "cyan-skillfish-governor-smu.service"],
                ["systemctl", "enable", "bc250-cu-live-manager.service"],
                ["systemctl", "enable", "bc250-cpu-mode.service"],
            ],
        )

    def test_known_legacy_curve_is_extended_without_changing_active_settings(self):
        legacy = """[frequency-range]
min = 500
max = 1800

[temperature]
throttling = 90
throttling_recovery = 85

[[safe-points]]
frequency = 500
voltage = 700
[[safe-points]]
frequency = 1000
voltage = 800
[[safe-points]]
frequency = 1175
voltage = 850
[[safe-points]]
frequency = 1500
voltage = 900
[[safe-points]]
frequency = 1600
voltage = 910
[[safe-points]]
frequency = 1700
voltage = 920
[[safe-points]]
frequency = 1800
voltage = 930
"""

        updated, changed = extend_governor_curve(legacy)

        self.assertTrue(changed)
        self.assertIn("min = 500\nmax = 1800", updated)
        self.assertIn("throttling = 90\nthrottling_recovery = 85", updated)
        self.assertIn("frequency = 350\nvoltage = 700", updated)
        self.assertIn("frequency = 2400\nvoltage = 1150", updated)

    def test_unknown_custom_curve_is_not_rewritten(self):
        custom = """[[safe-points]]
frequency = 600
voltage = 750
[[safe-points]]
frequency = 1900
voltage = 990
"""

        updated, changed = extend_governor_curve(custom)

        self.assertFalse(changed)
        self.assertEqual(updated, custom)

    def test_remove_commands_disable_the_persistent_cpu_mode_service(self):
        self.assertTrue(hasattr(install_module, "remove_service_commands"), "remove service plan is missing")
        self.assertEqual(
            install_module.remove_service_commands(),
            [
                ["systemctl", "disable", "--now", "bc250-cpu-mode.service"],
                ["systemctl", "disable", "--now", "cyan-skillfish-governor-smu.service"],
                ["systemctl", "disable", "bc250-cu-live-manager.service"],
            ],
        )

    def test_install_authentication_requirement_has_stable_id(self):
        with (
            patch("bc250_install.Path", side_effect=self._path_with_system_root),
            patch("bc250_install.os.geteuid", return_value=1000, create=True),
        ):
            result = install_bundle(self.project, self.root)
        self.assertEqual(result["message_id"], "install.auth_required")
        self.assertEqual(result["message_args"], {})
        self.assertEqual(result["message"], "시스템 설치에는 인증이 필요합니다.")

    def test_service_failure_has_stable_id_and_raw_diagnostic(self):
        failure = __import__("subprocess").CompletedProcess([], 1, "", "service denied")
        with (
            patch("bc250_install.Path", side_effect=self._path_with_system_root),
            patch("bc250_install.os.geteuid", return_value=0, create=True),
            patch("bc250_install.detect_platform", return_value=self.SUPPORTED_PLATFORM, create=True),
            patch("bc250_install._copy_atomic"),
            patch("bc250_install.subprocess.run", return_value=failure),
        ):
            result = install_bundle(self.project, self.root)
        self.assertEqual(result["message_id"], "install.service_failed")
        self.assertEqual(result["message_args"], {})
        self.assertEqual(result["message"], "service denied")

    def test_unsupported_production_platform_rejects_before_writes_or_services(self):
        cases = (
            (
                PlatformReport(False, False, "x86_64", True, UserMessage("platform.not_bazzite")),
                "platform.not_bazzite",
                {},
                "This system is not Bazzite.",
            ),
            (
                PlatformReport(
                    False,
                    True,
                    "aarch64",
                    True,
                    UserMessage("platform.arch_unsupported", {"architecture": "aarch64"}),
                ),
                "platform.arch_unsupported",
                {"architecture": "aarch64"},
                "Unsupported architecture: aarch64",
            ),
            (
                PlatformReport(False, True, "x86_64", False, UserMessage("platform.device_missing")),
                "platform.device_missing",
                {},
                "Required device was not found.",
            ),
        )
        for unsupported, message_id, message_args, message in cases:
            with self.subTest(message_id=message_id):
                with (
                    patch("bc250_install.Path", side_effect=self._path_with_system_root),
                    patch("bc250_install.os.geteuid", return_value=0, create=True),
                    patch("bc250_install.detect_platform", return_value=unsupported, create=True),
                    patch("bc250_install._copy_atomic") as copy_atomic,
                    patch("bc250_install.subprocess.run") as run,
                ):
                    result = install_bundle(self.project, self.root)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["message_id"], message_id)
                    self.assertEqual(result["message_args"], message_args)
                    self.assertEqual(result["message"], message)
                    copy_atomic.assert_not_called()
                    run.assert_not_called()
                    self.assertFalse((self.root / "opt").exists())

    def test_remove_complete_has_stable_id(self):
        installed = self.root / "opt/bc250-custom-pannel/tool"
        installed.parent.mkdir(parents=True)
        installed.write_text("tool-data", encoding="utf-8")
        result = remove_bundle(self.project, self.root, manage_services=False)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["message_id"], "install.remove_complete")
        self.assertEqual(result["message_args"], {})
        self.assertEqual(result["message"], "번들 구성요소를 제거했습니다.")
        self.assertEqual(result["removed"], [str(installed)])

    def test_install_operation_failure_preserves_raw_diagnostic(self):
        with patch("bc250_install._copy_atomic", side_effect=OSError("fixture copy failure")):
            result = install_bundle(self.project, self.root, manage_services=False)
        self.assertEqual(result["message_id"], "dialog.operation_failed")
        self.assertEqual(result["message_args"], {})
        self.assertEqual(result["message"], "fixture copy failure")

    def test_remove_service_exception_returns_structured_failure(self):
        with (
            patch("bc250_install.Path", side_effect=self._path_with_system_root),
            patch("bc250_install.os.geteuid", return_value=0, create=True),
            patch("bc250_install.subprocess.run", side_effect=OSError("fixture disable failure")),
        ):
            result = remove_bundle(self.project, self.root)
        self.assertEqual(result["message_id"], "dialog.operation_failed")
        self.assertEqual(result["message_args"], {})
        self.assertEqual(result["message"], "fixture disable failure")
        self.assertEqual(result["removed"], [])

    def test_governor_unit_does_not_pull_in_live_cu_service(self):
        unit = Path("vendor/templates/cyan-skillfish-governor-smu.service").read_text(encoding="utf-8")
        self.assertNotIn("Wants=bc250-cu-live-manager.service", unit)
        self.assertNotIn("After=bc250-cu-live-manager.service", unit)

    def test_cu_service_uses_the_bundled_umr_installed_by_the_button(self):
        unit = Path("vendor/templates/bc250-cu-live-manager.service").read_text(encoding="utf-8")
        self.assertIn("Environment=UMR=/opt/bc250-custom-pannel/bin/umr", unit)

    def test_cpu_mode_service_runs_boot_recovery_without_starting_during_install(self):
        unit = Path("vendor/templates/bc250-cpu-mode.service").read_text(encoding="utf-8")
        self.assertIn("apply-cpu-mode --boot", unit)
        self.assertNotIn(["systemctl", "enable", "--now", "bc250-cpu-mode.service"], service_commands())

    def test_production_install_materializes_every_declared_system_file(self):
        project = Path(__file__).resolve().parents[1]
        result = install_bundle(project, self.root, manage_services=False)
        self.assertTrue(result["ok"], result)
        expected = {
            "etc/cyan-skillfish-governor-smu/cyan-skillfish-governor-smu": "0755",
            "usr/local/bin/bc250-cu-live-manager": "0755",
            "opt/bc250-custom-pannel/bin/umr": "0755",
            "etc/cyan-skillfish-governor-smu/config.toml": "0644",
            "etc/bc250-cu-live-manager.conf": "0644",
            "etc/bc250-custom-pannel-cpu.conf": "0644",
            "etc/systemd/system/cyan-skillfish-governor-smu.service": "0644",
            "etc/systemd/system/bc250-cu-live-manager.service": "0644",
            "etc/systemd/system/bc250-cpu-mode.service": "0644",
            "etc/dbus-1/system.d/com.cyanskillfish.Governor.conf": "0644",
            "etc/polkit-1/rules.d/49-bc250-custom-pannel.rules": "0644",
            "usr/local/libexec/bc250-custom-pannel-privileged": "0755",
            "opt/bc250-custom-pannel/licenses/cyan-skillfish-governor-MIT.txt": "0644",
            "opt/bc250-custom-pannel/licenses/umr-MIT.txt": "0644",
        }
        installed = {Path(path).relative_to(self.root).as_posix() for path in result["installed"]}
        self.assertEqual(installed, set(expected))
        for relative, mode in expected.items():
            with self.subTest(relative=relative):
                destination = self.root / relative
                self.assertTrue(destination.is_file())
                if os.name != "nt":
                    self.assertEqual(destination.stat().st_mode & 0o777, int(mode, 8))
        receipt = Path(result["receipt"])
        self.assertTrue(receipt.is_file())
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o644)

    def test_install_and_cpu_mode_are_one_root_transaction(self):
        self.assertTrue(
            hasattr(install_module, "install_and_set_cpu_mode"),
            "single-auth install and CPU mode transaction is missing",
        )
        installed = {
            "ok": True,
            "message_id": "install.complete",
            "message_args": {},
            "message": "installed",
            "installed": ["/one"],
        }
        applied = {
            "ok": True,
            "message_id": "helper.cpu_unlock_armed",
            "message_args": {},
            "message": "armed",
            "reboot_required": True,
        }
        with (
            patch("bc250_install.install_bundle", return_value=installed) as install,
            patch("bc250_install.run_action", return_value=applied) as run_action,
        ):
            result = install_module.install_and_set_cpu_mode(self.project, True, self.root, False)

        self.assertEqual(result["message_id"], "helper.cpu_unlock_armed")
        self.assertEqual(result["installed"], ["/one"])
        install.assert_called_once_with(self.project, self.root, False)
        run_action.assert_called_once_with("set-cpu-mode", {"enabled": True}, self.root)

    def test_production_install_uses_ostree_writable_destinations(self):
        project = Path(__file__).resolve().parents[1]
        result = install_bundle(project, self.root, manage_services=False)

        self.assertTrue(result["ok"], result)
        installed = {Path(path).relative_to(self.root).as_posix() for path in result["installed"]}
        self.assertFalse(any(path.startswith("usr/share/") for path in installed), installed)
        self.assertIn("etc/polkit-1/rules.d/49-bc250-custom-pannel.rules", installed)
        self.assertIn("opt/bc250-custom-pannel/licenses/cyan-skillfish-governor-MIT.txt", installed)
        self.assertIn("opt/bc250-custom-pannel/licenses/umr-MIT.txt", installed)


if __name__ == "__main__":
    unittest.main()
