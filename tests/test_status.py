import tempfile
import unittest
import json
from pathlib import Path
from subprocess import CompletedProcess

import bc250.status as status_module

from bc250.messages import UserMessage
from bc250.status import (
    UNKNOWN,
    StatusCollector,
    format_clock,
    format_power,
    format_temperature,
    format_voltage,
    masks_to_cu_count,
    parse_cu_result,
    read_active_fans,
    read_cpu_temperature,
    read_system_info,
)


def make_hwmon(root: Path, index: int, name: str, files: dict[str, str]) -> Path:
    hwmon = root / f"hwmon{index}"
    hwmon.mkdir(parents=True)
    (hwmon / "name").write_text(name + "\n", encoding="utf-8")
    for filename, value in files.items():
        (hwmon / filename).write_text(str(value) + "\n", encoding="utf-8")
    return hwmon


class StatusTests(unittest.TestCase):
    def test_cpu_topology_reports_stock_six_core_twelve_thread_layout(self):
        self.assertTrue(hasattr(status_module, "read_cpu_topology"), "read_cpu_topology is missing")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "present").write_text("0-11\n", encoding="utf-8")
            for thread in range(12):
                topology = root / f"cpu{thread}" / "topology"
                topology.mkdir(parents=True)
                (topology / "physical_package_id").write_text("0\n", encoding="utf-8")
                (topology / "core_id").write_text(f"{thread // 2}\n", encoding="utf-8")

            cores, threads = status_module.read_cpu_topology(root)

        self.assertEqual((cores, threads), (6, 12))

    def test_cpu_topology_reports_unlocked_eight_core_sixteen_thread_layout(self):
        self.assertTrue(hasattr(status_module, "read_cpu_topology"), "read_cpu_topology is missing")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "present").write_text("0-7,12-19\n", encoding="utf-8")
            for index, thread in enumerate((*range(8), *range(12, 20))):
                topology = root / f"cpu{thread}" / "topology"
                topology.mkdir(parents=True)
                (topology / "physical_package_id").write_text("0\n", encoding="utf-8")
                (topology / "core_id").write_text(f"{index // 2}\n", encoding="utf-8")

            cores, threads = status_module.read_cpu_topology(root)

        self.assertEqual((cores, threads), (8, 16))

    def test_cpu_topology_reports_only_online_cores_after_toggle_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "present").write_text("0-7,12-19\n", encoding="utf-8")
            (root / "online").write_text("0-7,12-15\n", encoding="utf-8")
            for index, thread in enumerate((*range(8), *range(12, 20))):
                topology = root / f"cpu{thread}" / "topology"
                topology.mkdir(parents=True)
                (topology / "physical_package_id").write_text("0\n", encoding="utf-8")
                (topology / "core_id").write_text(f"{index // 2}\n", encoding="utf-8")

            cores, threads = status_module.read_cpu_topology(root)

        self.assertEqual((cores, threads), (6, 12))

    def test_cpu_temperature_prefers_k10temp_tctl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_hwmon(root, 1, "nct6686", {"temp1_label": "CPU", "temp1_input": "43000"})
            make_hwmon(root, 3, "k10temp", {"temp1_label": "Tctl", "temp1_input": "43750"})
            value, source = read_cpu_temperature(root)
        self.assertEqual(value, "43.8 \N{DEGREE SIGN}C")
        self.assertEqual(source, "Tctl")

    def test_cpu_temperature_falls_back_to_board_cpu_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_hwmon(root, 1, "nct6687", {"temp1_label": "CPU", "temp1_input": "43000"})
            value, source = read_cpu_temperature(root)
        self.assertEqual((value, source), ("43.0 \N{DEGREE SIGN}C", "CPU"))

    def test_cpu_temperature_is_unavailable_when_no_supported_reading_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_hwmon(root, 1, "nct6686", {"temp1_label": "Board", "temp1_input": "43000"})
            value, source = read_cpu_temperature(root)
        self.assertEqual((value, source), (UNKNOWN, ""))

    def test_cpu_temperature_uses_board_fallback_when_tctl_is_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_hwmon(root, 1, "k10temp", {"temp1_label": "Tctl", "temp1_input": "not-a-number"})
            make_hwmon(root, 2, "nct6687", {"temp1_label": "CPU", "temp1_input": "43000"})
            value, source = read_cpu_temperature(root)
        self.assertEqual((value, source), ("43.0 \N{DEGREE SIGN}C", "CPU"))

    def test_active_fan_reports_highest_rpm_and_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_hwmon(root, 1, "nct6686", {
                "fan1_label": "CPU Fan", "fan1_input": "900",
                "fan2_label": "Pump Fan", "fan2_input": "1721",
                "fan3_label": "System Fan #1", "fan3_input": "0",
            })
            fan = read_active_fans(root)
        self.assertEqual((fan.rpm, fan.label, fan.active_count, fan.state), (1721, "Pump Fan", 2, "active"))

    def test_active_fan_is_unavailable_without_nct_hwmon(self):
        with tempfile.TemporaryDirectory() as tmp:
            fan = read_active_fans(Path(tmp))
        self.assertEqual((fan.rpm, fan.label, fan.active_count, fan.state), (None, "", 0, "unavailable"))

    def test_active_fan_reports_stopped_only_for_readable_zero_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_hwmon(root, 1, "nct6686", {
                "fan1_label": "CPU Fan", "fan1_input": "0",
                "fan2_label": "Pump Fan", "fan2_input": "0",
            })
            fan = read_active_fans(root)
        self.assertEqual((fan.rpm, fan.label, fan.active_count, fan.state), (0, "", 0, "stopped"))

    def test_active_fan_is_unavailable_when_nct_inputs_are_not_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_hwmon(root, 1, "nct6687", {
                "fan1_label": "CPU Fan",
                "fan2_label": "Pump Fan", "fan2_input": "invalid",
                "fan3_label": "System Fan", "fan3_input": "-1",
            })
            fan = read_active_fans(root)
        self.assertEqual((fan.rpm, fan.label, fan.active_count, fan.state), (None, "", 0, "unavailable"))

    def test_active_fan_is_unavailable_when_zero_is_mixed_with_unusable_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_hwmon(root, 1, "nct6687", {
                "fan1_label": "CPU Fan", "fan1_input": "0",
                "fan2_label": "Pump Fan", "fan2_input": "invalid",
                "fan3_label": "System Fan",
            })
            fan = read_active_fans(root)
        self.assertEqual((fan.rpm, fan.label, fan.active_count, fan.state), (None, "", 0, "unavailable"))

    def test_sensor_discovery_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hwmon = make_hwmon(root, 1, "nct6686", {
                "temp1_label": "CPU", "temp1_input": "43000",
                "fan2_label": "Pump Fan", "fan2_input": "1721",
                "pwm2": "255", "pwm2_enable": "99",
            })
            for path in hwmon.iterdir():
                path.chmod(0o444)
            before = {path.name: path.read_bytes() for path in hwmon.iterdir()}
            read_cpu_temperature(root)
            read_active_fans(root)
            after = {path.name: path.read_bytes() for path in hwmon.iterdir()}
        self.assertEqual(after, before)

    def test_collector_uses_injected_hwmon_root_for_snapshot_sensor_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            drm = root / "drm"
            amdgpu = drm / "card0" / "device" / "hwmon" / "hwmon0"
            amdgpu.mkdir(parents=True)
            for filename, value in {
                "name": "amdgpu", "temp1_input": "42100", "power1_average": "35100000",
                "in0_input": "699", "freq1_input": "1800000000",
            }.items():
                (amdgpu / filename).write_text(value + "\n", encoding="utf-8")
            hwmon = root / "hwmon"
            make_hwmon(hwmon, 1, "k10temp", {"temp1_label": "Tctl", "temp1_input": "43750"})
            make_hwmon(hwmon, 2, "nct6686", {"fan1_label": "Pump Fan", "fan1_input": "1721"})
            dmi = root / "dmi"
            dmi.mkdir()
            (dmi / "bios_vendor").write_text("AMI\n", encoding="utf-8")
            config = root / "cu.conf"
            config.write_text("BC250_WGP_MASKS=0x1f,0x1f,0x1f,0x1f\n", encoding="utf-8")

            def runner(argv: list[str]) -> CompletedProcess[str]:
                stdout = "[ OK ] dispatch registers updated (40/40 CUs target)\n" if argv[0] == "journalctl" else "active\n"
                return CompletedProcess(argv, 0, stdout, "")

            snapshot = StatusCollector(runner, drm, dmi, config, hwmon).collect()

        self.assertEqual(snapshot.gpu_temperature, "42.1 \N{DEGREE SIGN}C")
        self.assertEqual((snapshot.cpu_temperature, snapshot.cpu_temperature_source), ("43.8 \N{DEGREE SIGN}C", "Tctl"))
        self.assertEqual((snapshot.fan.rpm, snapshot.fan.label, snapshot.fan.active_count, snapshot.fan.state), (1721, "Pump Fan", 1, "active"))
        self.assertEqual(snapshot.cu_state, UserMessage("status.cu_applied", {"count": 40}))

    def test_collector_uses_required_cu_and_amdgpu_error_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "cu.conf"
            config.write_text("BC250_WGP_MASKS=0x0f,0x0f,0x0f,0x0f\n", encoding="utf-8")

            def runner(argv: list[str]) -> CompletedProcess[str]:
                stdout = "[ OK ] dispatch registers updated (40/40 CUs target)\n" if argv[0] == "journalctl" else "active\n"
                return CompletedProcess(argv, 0, stdout, "")

            snapshot = StatusCollector(runner, root / "drm", root / "dmi", config, root / "hwmon").collect()

        self.assertEqual(snapshot.cu_state, UserMessage("status.cu_mismatch", {"current": 40}))
        self.assertEqual(snapshot.errors, (UserMessage("error.cu_mismatch"), UserMessage("error.amdgpu_missing")))

    def test_sensor_unit_conversion(self):
        self.assertEqual(format_temperature("41000"), "41.0 °C")
        self.assertEqual(format_power("35100000"), "35.1 W")
        self.assertEqual(format_voltage("699"), "699 mV")
        self.assertEqual(format_clock("1800000000"), "1800 MHz")
        self.assertEqual(format_power("bad"), UNKNOWN)

    def test_cu_result_requires_exact_success_message(self):
        line = "[ OK ] dispatch registers updated (40/40 CUs target)"
        self.assertEqual(parse_cu_result(line), 40)
        self.assertIsNone(parse_cu_result("requested 40 CUs target"))

    def test_saved_masks_convert_to_cu_count(self):
        self.assertEqual(masks_to_cu_count("0x07,0x07,0x07,0x07"), 24)
        self.assertEqual(masks_to_cu_count("0x0f,0x0f,0x0f,0x0f"), 32)
        self.assertEqual(masks_to_cu_count("0x1f,0x1f,0x1f,0x1f"), 40)
        self.assertIsNone(masks_to_cu_count("0xff"))

    def test_collector_prefers_verified_register_readback_over_legacy_journal_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "cu.conf"
            config.write_text("BC250_WGP_MASKS=0x07,0x0f,0x17,0x1f\n", encoding="utf-8")
            state = root / "cu-state.json"
            state.write_text(
                json.dumps(
                    {
                        "verified": True,
                        "requested_masks": [7, 15, 23, 31],
                        "actual_masks": [7, 15, 23, 31],
                    }
                ),
                encoding="utf-8",
            )

            def runner(argv):
                stdout = "[ OK ] dispatch registers updated (40/40 CUs target)\n" if argv[0] == "journalctl" else "active\n"
                return CompletedProcess(argv, 0, stdout, "")

            snapshot = StatusCollector(
                runner,
                root / "drm",
                root / "dmi",
                config,
                root / "hwmon",
                root / "cpu",
                state,
            ).collect()

        self.assertEqual(snapshot.cu_masks, (7, 15, 23, 31))
        self.assertEqual(snapshot.cu_saved_masks, (7, 15, 23, 31))
        self.assertTrue(snapshot.cu_verified)
        self.assertEqual(snapshot.cu_count, 32)

    def test_register_readback_mismatch_is_visible_even_when_total_count_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "cu.conf"
            config.write_text("BC250_WGP_MASKS=0x0f,0x0f,0x0f,0x0f\n", encoding="utf-8")
            state = root / "cu-state.json"
            state.write_text(
                json.dumps(
                    {
                        "verified": False,
                        "requested_masks": [15, 15, 15, 15],
                        "actual_masks": [7, 23, 15, 15],
                    }
                ),
                encoding="utf-8",
            )
            runner = lambda argv: CompletedProcess(argv, 0, "active\n", "")
            snapshot = StatusCollector(
                runner, root / "drm", root / "dmi", config, root / "hwmon", root / "cpu", state
            ).collect()

        self.assertFalse(snapshot.cu_verified)
        self.assertIn(UserMessage("error.cu_mismatch"), snapshot.errors)

    def test_bios_and_kernel_are_read_without_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            dmi = Path(tmp)
            (dmi / "bios_vendor").write_text("AMI\n", encoding="utf-8")
            (dmi / "bios_version").write_text("Robin5.00\n", encoding="utf-8")
            (dmi / "bios_date").write_text("07/01/2026\n", encoding="utf-8")
            info = read_system_info(dmi_root=dmi, uname=("6.19-test", "x86_64"))
        self.assertEqual(info.bios_vendor, "AMI")
        self.assertEqual(info.bios_version, "Robin5.00")
        self.assertEqual(info.kernel_release, "6.19-test")


if __name__ == "__main__":
    unittest.main()
