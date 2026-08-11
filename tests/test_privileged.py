import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bc250_privileged import _save_governor, run_action


GOVERNOR_CONFIG = """[frequency-range]
min = 500
max = 1800

[temperature]
throttling = 85
throttling_recovery = 75

[gpu]
set-method = "smu"
voltage-limit = 930
"""


class PrivilegedHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        governor = self.root / "etc/cyan-skillfish-governor-smu"
        governor.mkdir(parents=True)
        (governor / "config.toml").write_text(GOVERNOR_CONFIG, encoding="utf-8")
        cu = self.root / "etc/bc250-cu-live-manager.conf"
        cu.parent.mkdir(parents=True, exist_ok=True)
        cu.write_text("BC250_WGP_MASKS=0x1f,0x1f,0x1f,0x1f\nUMR=/opt/bc250-custom-pannel/bin/umr\n", encoding="utf-8")
        manager = self.root / "usr/local/bin/bc250-cu-live-manager"
        manager.parent.mkdir(parents=True)
        manager.write_text("#!/bin/sh\n", encoding="utf-8")
        manager.chmod(0o755)

    def _write_cpu_topology(self, cpu_ids, online_ids):
        cpu_root = self.root / "sys/devices/system/cpu"
        cpu_root.mkdir(parents=True, exist_ok=True)
        cpu_ids = tuple(cpu_ids)
        (cpu_root / "present").write_text(
            ",".join(str(cpu_id) for cpu_id in cpu_ids) + "\n",
            encoding="utf-8",
        )
        for index, cpu_id in enumerate(cpu_ids):
            cpu = cpu_root / f"cpu{cpu_id}"
            topology = cpu / "topology"
            topology.mkdir(parents=True)
            (topology / "physical_package_id").write_text("0\n", encoding="utf-8")
            (topology / "core_id").write_text(f"{index // 2}\n", encoding="utf-8")
            if cpu_id != 0:
                (cpu / "online").write_text("1\n" if cpu_id in online_ids else "0\n", encoding="utf-8")
        return cpu_root

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_governor_is_atomic_and_keeps_backup(self):
        result = run_action("save-governor", {"preset": "eco", "throttle": 84, "recovery": 74}, self.root)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["message_id"], "helper.governor_saved")
        self.assertEqual(result["message_args"], {})
        self.assertEqual(result["message"], "거버너 설정을 저장했습니다.")
        path = self.root / "etc/cyan-skillfish-governor-smu/config.toml"
        self.assertIn("max = 1500", path.read_text(encoding="utf-8"))
        self.assertTrue(path.with_suffix(".toml.bc250-backup").exists())

    def test_save_custom_governor_writes_user_frequency_range(self):
        result = run_action(
            "save-governor-custom",
            {"min_mhz": 0, "max_mhz": 4_294_967_295, "max_mv": 4_294_967_295, "throttle": 255, "recovery": 255},
            self.root,
        )

        self.assertTrue(result["ok"], result)
        text = (self.root / "etc/cyan-skillfish-governor-smu/config.toml").read_text(encoding="utf-8")
        self.assertIn("min = 0", text)
        self.assertIn("max = 4294967295", text)
        self.assertIn("throttling = 255", text)
        self.assertIn("throttling_recovery = 255", text)
        self.assertIn("voltage-limit = 4294967295", text)

    def test_global_apply_validates_every_field_before_touching_hardware(self):
        before = (self.root / "etc/cyan-skillfish-governor-smu/config.toml").read_bytes()
        result = run_action(
            "apply-all",
            {
                "min_mhz": 1800,
                "max_mhz": 1700,
                "max_mv": 930,
                "throttle": 85,
                "recovery": 75,
                "cpu_extra_cores": "on",
                "cu_masks": "0x07,0x07,0x07,0x07",
                "persist": "on",
            },
            self.root,
        )

        self.assertFalse(result["ok"])
        self.assertEqual((self.root / "etc/cyan-skillfish-governor-smu/config.toml").read_bytes(), before)

    def test_global_save_applies_all_hardware_then_persists_exact_values(self):
        args = {
            "min_mhz": 500,
            "max_mhz": 1800,
            "max_mv": 930,
            "throttle": 85,
            "recovery": 75,
            "cpu_extra_cores": "on",
            "cu_masks": "0x07,0x0f,0x17,0x1f",
            "persist": "on",
        }
        ok = {"ok": True, "message_id": "ok", "message_args": {}, "message": "ok"}
        with (
            patch("bc250_privileged._apply_governor_runtime", return_value=ok) as governor,
            patch("bc250_privileged._apply_cu_masks", return_value=ok) as cu_apply,
            patch("bc250_privileged._save_cpu_mode", return_value={**ok, "reboot_required": True}) as cpu,
            patch("bc250_privileged._save_governor_values", return_value={**ok, "backup": "/g"}) as governor_save,
            patch("bc250_privileged._save_cu_masks", return_value={**ok, "backup": "/c"}) as cu_save,
        ):
            result = run_action("apply-all", args, self.root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["message_id"], "helper.all_saved")
        self.assertEqual(result["cu_count"], 32)
        self.assertTrue(result["reboot_required"])
        governor.assert_called_once_with(500, 1800, 930, 85, 75)
        cu_apply.assert_called_once_with((7, 15, 23, 31), self.root)
        cpu.assert_called_once_with(True, self.root)
        governor_save.assert_called_once_with(500, 1800, 85, 75, self.root, 930, restart_service=False)
        cu_save.assert_called_once_with((7, 15, 23, 31), self.root)

    def test_cpu_boot_recovery_requests_only_one_warm_reboot_per_attempt(self):
        self._write_cpu_topology(range(12), set(range(12)))
        (self.root / "etc/bc250-custom-pannel-cpu.conf").write_text("CPU_EXTRA_CORES=on\n", encoding="utf-8")
        boot_id = self.root / "proc/sys/kernel/random/boot_id"
        boot_id.parent.mkdir(parents=True)
        boot_id.write_text("cold-boot\n", encoding="utf-8")
        completed = __import__("subprocess").CompletedProcess([], 0, "0x77\n", "")
        unlocked = __import__("subprocess").CompletedProcess([], 0, "armed\n", "")

        with patch("bc250_privileged.subprocess.run", side_effect=(completed, unlocked)):
            first = run_action("apply-cpu-mode", {"boot": True}, self.root)

        self.assertTrue(first["ok"], first)
        self.assertTrue(first["reboot_required"])

        boot_id.write_text("warm-boot\n", encoding="utf-8")
        still_armed = __import__("subprocess").CompletedProcess([], 0, "0xFF\n", "")
        with patch("bc250_privileged.subprocess.run", return_value=still_armed) as run:
            second = run_action("apply-cpu-mode", {"boot": True}, self.root)

        self.assertTrue(second["ok"], second)
        self.assertEqual(second["message_id"], "helper.cpu_recovery_failed")
        self.assertEqual(run.call_count, 1)

    def test_invalid_custom_governor_range_does_not_change_file(self):
        path = self.root / "etc/cyan-skillfish-governor-smu/config.toml"
        before = path.read_bytes()
        result = run_action(
            "save-governor-custom",
            {"min_mhz": 1800, "max_mhz": 1700, "throttle": 85, "recovery": 75},
            self.root,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(path.read_bytes(), before)

    def test_save_cu_changes_only_boot_profile(self):
        result = run_action("save-cu", {"cu": 32}, self.root)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["message_id"], "helper.cu_saved")
        self.assertEqual(result["message_args"], {"count": 32})
        self.assertIsInstance(result["message_args"]["count"], int)
        self.assertEqual(result["message"], "Saved 32 CU boot profile.")
        text = (self.root / "etc/bc250-cu-live-manager.conf").read_text(encoding="utf-8")
        self.assertIn("BC250_WGP_MASKS=0x0f,0x0f,0x0f,0x0f", text)

    def test_cpu_unlock_arms_mask_without_rebooting(self):
        completed = __import__("subprocess").CompletedProcess([], 0, "CPU unlock armed\n", "")
        with patch("bc250_privileged.subprocess.run", return_value=completed) as run:
            result = run_action("unlock-cpu", {}, self.root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["message_id"], "helper.cpu_unlock_armed")
        self.assertTrue(result["reboot_required"])
        argv = run.call_args.args[0]
        self.assertEqual(argv[-2:], ["--yes", "cpu-unlock"])
        self.assertNotIn("reboot", argv)

    def test_cpu_unlock_failure_preserves_manager_diagnostic(self):
        completed = __import__("subprocess").CompletedProcess([], 1, "", "unexpected CPU mask 0x55")
        with patch("bc250_privileged.subprocess.run", return_value=completed):
            result = run_action("unlock-cpu", {}, self.root)

        self.assertFalse(result["ok"])
        self.assertEqual(result["message_id"], "helper.cpu_unlock_failed")
        self.assertIn("unexpected CPU mask 0x55", result["message"])

    def test_cpu_mode_disable_offlines_only_the_two_highest_physical_cores(self):
        cpu_ids = (*range(8), *range(12, 20))
        cpu_root = self._write_cpu_topology(cpu_ids, set(cpu_ids))
        config = self.root / "etc/bc250-custom-pannel-cpu.conf"
        config.write_text("CPU_EXTRA_CORES=on\n", encoding="utf-8")

        result = run_action("set-cpu-mode", {"enabled": "off"}, self.root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["message_id"], "helper.cpu_mode_disabled")
        self.assertEqual(config.read_text(encoding="utf-8"), "CPU_EXTRA_CORES=off\n")
        self.assertEqual(
            {cpu_id for cpu_id in cpu_ids if cpu_id != 0 and (cpu_root / f"cpu{cpu_id}/online").read_text().strip() == "0"},
            {16, 17, 18, 19},
        )

    def test_cpu_mode_enable_onlines_present_extra_cores_without_unlock_command(self):
        cpu_ids = (*range(8), *range(12, 20))
        cpu_root = self._write_cpu_topology(cpu_ids, set(cpu_ids) - {16, 17, 18, 19})
        config = self.root / "etc/bc250-custom-pannel-cpu.conf"
        config.write_text("CPU_EXTRA_CORES=off\n", encoding="utf-8")

        with patch("bc250_privileged.subprocess.run") as run:
            result = run_action("set-cpu-mode", {"enabled": "on"}, self.root)

        self.assertTrue(result["ok"], result)
        self.assertFalse(run.called)
        self.assertEqual(config.read_text(encoding="utf-8"), "CPU_EXTRA_CORES=on\n")
        self.assertTrue(all((cpu_root / f"cpu{cpu_id}/online").read_text().strip() == "1" for cpu_id in (16, 17, 18, 19)))

    def test_cpu_mode_enable_arms_unlock_when_only_twelve_threads_are_present(self):
        self._write_cpu_topology(range(12), set(range(12)))
        completed = __import__("subprocess").CompletedProcess([], 0, "CPU unlock armed\n", "")

        with patch("bc250_privileged.subprocess.run", return_value=completed) as run:
            result = run_action("set-cpu-mode", {"enabled": "on"}, self.root)

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["reboot_required"])
        self.assertEqual(run.call_args.args[0][-2:], ["--yes", "cpu-unlock"])
        self.assertEqual(
            (self.root / "etc/bc250-custom-pannel-cpu.conf").read_text(encoding="utf-8"),
            "CPU_EXTRA_CORES=on\n",
        )

    def test_cpu_mode_disable_rolls_back_partial_hotplug_failure(self):
        cpu_ids = (*range(8), *range(12, 20))
        cpu_root = self._write_cpu_topology(cpu_ids, set(cpu_ids))
        config = self.root / "etc/bc250-custom-pannel-cpu.conf"
        config.write_text("CPU_EXTRA_CORES=on\n", encoding="utf-8")
        failing = cpu_root / "cpu18/online"
        failing.unlink()
        failing.mkdir()

        result = run_action("set-cpu-mode", {"enabled": "off"}, self.root)

        self.assertFalse(result["ok"])
        self.assertEqual(result["message_id"], "dialog.operation_failed")
        self.assertIn("cpu18", result["message"])
        self.assertEqual(config.read_text(encoding="utf-8"), "CPU_EXTRA_CORES=on\n")
        self.assertEqual((cpu_root / "cpu16/online").read_text().strip(), "1")
        self.assertEqual((cpu_root / "cpu17/online").read_text().strip(), "1")

    def test_apply_cpu_mode_replays_the_saved_boot_preference(self):
        cpu_ids = (*range(8), *range(12, 20))
        cpu_root = self._write_cpu_topology(cpu_ids, set(cpu_ids))
        (self.root / "etc/bc250-custom-pannel-cpu.conf").write_text(
            "CPU_EXTRA_CORES=off\n",
            encoding="utf-8",
        )

        result = run_action("apply-cpu-mode", {}, self.root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["message_id"], "helper.cpu_mode_disabled")
        self.assertEqual(
            {cpu_id for cpu_id in (16, 17, 18, 19) if (cpu_root / f"cpu{cpu_id}/online").read_text().strip() == "0"},
            {16, 17, 18, 19},
        )

    def test_apply_cpu_mode_auto_default_does_not_change_online_cpus(self):
        cpu_ids = (*range(8), *range(12, 20))
        cpu_root = self._write_cpu_topology(cpu_ids, set(cpu_ids))
        (self.root / "etc/bc250-custom-pannel-cpu.conf").write_text(
            "CPU_EXTRA_CORES=auto\n",
            encoding="utf-8",
        )

        result = run_action("apply-cpu-mode", {}, self.root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["message_id"], "helper.cpu_mode_unchanged")
        self.assertTrue(
            all(
                (cpu_root / f"cpu{cpu_id}/online").read_text().strip() == "1"
                for cpu_id in cpu_ids
                if cpu_id != 0
            )
        )

    def test_arbitrary_values_are_rejected_without_file_change(self):
        path = self.root / "etc/cyan-skillfish-governor-smu/config.toml"
        before = path.read_bytes()
        result = run_action("save-governor", {"preset": "1666", "throttle": 85, "recovery": 75}, self.root)
        self.assertFalse(result["ok"])
        self.assertEqual(path.read_bytes(), before)

    def test_governor_restart_failure_has_stable_id_and_preserves_diagnostic(self):
        config = self.root / "etc/cyan-skillfish-governor-smu/config.toml"
        with (
            patch("bc250_privileged._rooted", return_value=config),
            patch("bc250_privileged.subprocess.run", return_value=__import__("subprocess").CompletedProcess([], 1, "", "restart denied")),
        ):
            result = _save_governor({"preset": "eco", "throttle": 84, "recovery": 74}, Path("/"))
        self.assertEqual(result["message_id"], "helper.governor_restart_failed")
        self.assertEqual(result["message_args"], {})
        self.assertEqual(result["message"], "거버너 재시작 실패: restart denied")

    def test_restore_backup_has_stable_id_and_empty_args(self):
        config = self.root / "etc/cyan-skillfish-governor-smu/config.toml"
        backup = config.with_suffix(".toml.bc250-backup")
        backup.write_text(GOVERNOR_CONFIG.replace("max = 1800", "max = 1500"), encoding="utf-8")
        result = run_action("restore-backup", {"target": "governor"}, self.root)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["message_id"], "helper.backup_restored")
        self.assertEqual(result["message_args"], {})
        self.assertEqual(result["message"], "백업 설정을 복원했습니다.")

    def test_root_authentication_requirement_has_stable_id(self):
        def path_with_system_root(value):
            return self.root if value == "/" else Path(value)

        with (
            patch("bc250_privileged.Path", side_effect=path_with_system_root),
            patch("bc250_privileged.os.geteuid", return_value=1000, create=True),
        ):
            result = run_action("save-cu", {"cu": 32}, self.root)
        self.assertEqual(result["message_id"], "helper.auth_required")
        self.assertEqual(result["message_args"], {})
        self.assertEqual(result["message"], "이 작업은 시스템 인증이 필요합니다.")

    def test_unsupported_action_has_stable_id(self):
        result = run_action("unsupported", {}, self.root)
        self.assertEqual(result["message_id"], "helper.action_invalid")
        self.assertEqual(result["message_args"], {})
        self.assertEqual(result["message"], "허용되지 않은 작업입니다.")

    def test_operation_failure_has_stable_id_and_raw_diagnostic(self):
        with patch("bc250_privileged._save_cu", side_effect=OSError("fixture filesystem failure")):
            result = run_action("save-cu", {"cu": 32}, self.root)
        self.assertEqual(result["message_id"], "dialog.operation_failed")
        self.assertEqual(result["message_args"], {})
        self.assertEqual(result["message"], "fixture filesystem failure")


if __name__ == "__main__":
    unittest.main()
