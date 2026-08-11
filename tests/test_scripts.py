import unittest
from pathlib import Path


class ScriptAndReadmeTests(unittest.TestCase):
    def test_cu_manager_exposes_exact_masks_with_readback_state_and_cpu_mask_probe(self):
        text = Path("vendor/bin/bc250-cu-live-manager").read_text(encoding="utf-8")
        self.assertIn("apply-masks", text)
        self.assertIn("requested_masks", text)
        self.assertIn("actual_masks", text)
        self.assertIn("/run/bc250-custom-pannel/cu-state.json", text)
        self.assertIn("cpu-mask", text)

    def test_scripts_are_clone_location_independent(self):
        for name in ("run.sh", "install-app.sh", "uninstall-app.sh"):
            with self.subTest(name=name):
                text = Path(name).read_text(encoding="utf-8")
                self.assertNotIn("/home/", text)
                self.assertIn("SCRIPT_DIR", text)

    def test_readmes_cover_operation_and_recovery(self):
        documents = {
            "README.md": (
                "Quick start",
                "Bundled components",
                "Safety limits",
                "Recovery and removal",
                "Troubleshooting",
            ),
            "README.ko.md": ("빠른 시작", "동봉 구성요소", "안전 범위", "복구와 제거", "문제 해결"),
        }
        for path, headings in documents.items():
            text = Path(path).read_text(encoding="utf-8")
            for heading in headings:
                with self.subTest(path=path, heading=heading):
                    self.assertIn(heading, text)

        english = Path("README.md").read_text(encoding="utf-8")
        korean = Path("README.ko.md").read_text(encoding="utf-8")
        self.assertIn("[한국어](README.ko.md)", english)
        self.assertIn("[English](README.md)", korean)


if __name__ == "__main__":
    unittest.main()
