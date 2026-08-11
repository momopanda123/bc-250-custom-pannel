import hashlib
import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from bc250.bootstrap import (
    BootstrapReport,
    BundleReport,
    PlatformReport,
    detect_platform,
    inspect,
    installed_component_matches,
    load_install_receipt,
    load_manifest,
    verify_bundle,
)
from bc250.messages import UserMessage
from bc250_install import install_bundle


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def _fixture(self, payload: bytes, digest: str) -> Path:
        root = Path(self.tmp.name)
        vendor = root / "vendor"
        vendor.mkdir(parents=True, exist_ok=True)
        (vendor / "component.bin").write_bytes(payload)
        (root / "VENDOR-MANIFEST.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "components": [
                        {
                            "name": "fixture",
                            "path": "vendor/component.bin",
                            "version": "1",
                            "source": "https://example.invalid/component",
                            "sha256": digest,
                            "license": "MIT",
                            "target": "test",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return root

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_bundle_is_accepted(self):
        payload = b"verified component"
        root = self._fixture(payload, hashlib.sha256(payload).hexdigest())
        self.assertTrue(verify_bundle(root).ok)

    def test_production_bundle_manifest_matches_real_project_files(self):
        report = verify_bundle(PROJECT_ROOT)
        self.assertTrue(report.ok, report.errors)

    def test_production_bundle_installs_persistent_cpu_mode_config_and_service(self):
        components = load_manifest(PROJECT_ROOT)["components"]
        by_install_path = {component.get("install_path"): component for component in components}
        self.assertIn("/etc/bc250-custom-pannel-cpu.conf", by_install_path)
        self.assertIn("/etc/systemd/system/bc250-cpu-mode.service", by_install_path)
        service = PROJECT_ROOT / by_install_path["/etc/systemd/system/bc250-cpu-mode.service"]["path"]
        config = PROJECT_ROOT / by_install_path["/etc/bc250-custom-pannel-cpu.conf"]["path"]
        self.assertIn(
            "ExecStart=/usr/local/libexec/bc250-custom-pannel-privileged apply-cpu-mode",
            service.read_text(encoding="utf-8"),
        )
        self.assertEqual(config.read_text(encoding="utf-8"), "CPU_EXTRA_CORES=auto\n")

    def test_modified_vendor_file_is_rejected(self):
        root = self._fixture(b"changed", "0" * 64)
        report = verify_bundle(root)
        self.assertFalse(report.ok)
        self.assertEqual(report.errors[0].key, "error.bundle_invalid")
        self.assertIn("SHA-256", str(report.errors[0].params["detail"]))

    def test_installed_component_must_match_the_bundled_hash(self):
        payload = b"new compatible binary"
        root = self._fixture(payload, hashlib.sha256(payload).hexdigest())
        component = load_manifest(root)["components"][0]
        component["install_path"] = "/opt/example/component.bin"
        system_root = Path(self.tmp.name) / "system"
        installed = system_root / "opt/example/component.bin"
        installed.parent.mkdir(parents=True)
        installed.write_bytes(b"old incompatible binary")

        self.assertFalse(installed_component_matches(root, component, system_root))
        installed.write_bytes(payload)
        self.assertTrue(installed_component_matches(root, component, system_root))

    def test_only_bazzite_x86_64_bc250_is_supported(self):
        good = detect_platform(
            os_release="ID=fedora\nVARIANT_ID=bazzite\n",
            arch="x86_64",
            pci_devices="1002:13fe",
        )
        self.assertTrue(good.supported)
        self.assertEqual(good.message.key, "platform.ready")
        not_bazzite = detect_platform("ID=fedora\nVARIANT_ID=workstation\n", "x86_64", "1002:13fe")
        wrong_arch = detect_platform("ID=fedora\nVARIANT_ID=bazzite\n", "aarch64", "1002:13fe")
        missing_gpu = detect_platform("ID=fedora\nVARIANT_ID=bazzite\n", "x86_64", "1002:9999")
        self.assertEqual(not_bazzite.message.key, "platform.not_bazzite")
        self.assertEqual(wrong_arch.message.key, "platform.arch_unsupported")
        self.assertEqual(wrong_arch.message.params, {"architecture": "aarch64"})
        self.assertEqual(missing_gpu.message.key, "platform.device_missing")

    def test_skip_platform_has_stable_message_key(self):
        report = inspect(Path("."), skip_platform=True)
        self.assertEqual(report.platform.message.key, "platform.skipped")
        self.assertEqual(report.platform.message.params, {})

    def test_ready_requires_the_persistent_cpu_mode_component(self):
        self.assertIn("cpu_mode_installed", {field.name for field in fields(BootstrapReport)})
        report = BootstrapReport(
            bundle=BundleReport(True, (), ()),
            platform=PlatformReport(True, True, "x86_64", True, UserMessage("platform.ready")),
            governor_installed=True,
            cu_manager_installed=True,
            umr_installed=True,
            helper_installed=True,
            cpu_mode_installed=False,
        )
        self.assertFalse(report.ready)

    def test_inspect_requires_governor_service_and_dbus_policy(self):
        system_root = Path(self.tmp.name) / "installed-system"
        result = install_bundle(PROJECT_ROOT, system_root, manage_services=False)
        self.assertTrue(result["ok"], result)
        self.assertTrue(inspect(PROJECT_ROOT, skip_platform=True, system_root=system_root).ready)

        (system_root / "etc/dbus-1/system.d/com.cyanskillfish.Governor.conf").unlink()
        report = inspect(PROJECT_ROOT, skip_platform=True, system_root=system_root)
        self.assertFalse(report.governor_installed)
        self.assertFalse(report.ready)

    def test_root_inaccessible_polkit_rule_uses_root_owned_install_receipt(self):
        system_root = Path(self.tmp.name) / "installed-system"
        result = install_bundle(PROJECT_ROOT, system_root, manage_services=False)
        self.assertTrue(result["ok"], result)
        receipt = load_install_receipt(system_root)
        polkit_path = "/etc/polkit-1/rules.d/49-bc250-custom-pannel.rules"
        self.assertIn(polkit_path, receipt)

        original = installed_component_matches

        def unreadable_polkit(project_root, component, candidate_root):
            if component.get("install_path") == polkit_path:
                return False
            return original(project_root, component, candidate_root)

        with patch("bc250.bootstrap.installed_component_matches", side_effect=unreadable_polkit):
            report = inspect(PROJECT_ROOT, skip_platform=True, system_root=system_root)

        self.assertTrue(report.helper_installed)
        self.assertTrue(report.ready)

    def test_ready_requires_installed_license_support_files(self):
        self.assertIn("support_installed", {field.name for field in fields(BootstrapReport)})
        system_root = Path(self.tmp.name) / "installed-system"
        result = install_bundle(PROJECT_ROOT, system_root, manage_services=False)
        self.assertTrue(result["ok"], result)
        (system_root / "opt/bc250-custom-pannel/licenses/umr-MIT.txt").unlink()

        report = inspect(PROJECT_ROOT, skip_platform=True, system_root=system_root)
        self.assertFalse(report.support_installed)
        self.assertFalse(report.ready)

    def test_cpu_mode_bundle_default_preserves_the_existing_cpu_state(self):
        text = Path("vendor/templates/bc250-cpu-mode.conf").read_text(encoding="utf-8")
        self.assertEqual(text, "CPU_EXTRA_CORES=auto\n")


if __name__ == "__main__":
    unittest.main()
