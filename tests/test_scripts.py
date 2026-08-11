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

    def test_readmes_cover_installation_and_usage(self):
        documents = {
            "README.md": (
                "Install and run",
                "Install components",
                "Using the application",
                "Apply and Save",
                "Remove",
            ),
            "README.ko.md": (
                "설치 및 첫 실행",
                "구성요소 설치",
                "사용 방법",
                "Apply와 Save",
                "제거",
            ),
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
        for text in (english, korean):
            self.assertIn("./install-app.sh", text)
            self.assertNotIn("./run.sh", text)

    def test_interactive_launcher_detaches_without_breaking_cli_mode(self):
        text = Path("run.sh").read_text(encoding="utf-8")
        self.assertIn('[[ $# -eq 0 && -t 0 && -t 1 ]]', text)
        self.assertIn('setsid -f python3 "$SCRIPT_DIR/app.py"', text)
        self.assertIn('exec python3 "$SCRIPT_DIR/app.py" "$@"', text)

        desktop_installer = Path("install-app.sh").read_text(encoding="utf-8")
        self.assertIn("Terminal=false", desktop_installer)


if __name__ == "__main__":
    unittest.main()
