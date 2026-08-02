import unittest
from pathlib import Path


class ScriptAndReadmeTests(unittest.TestCase):
    def test_scripts_are_clone_location_independent(self):
        for name in ("run.sh", "install-app.sh", "uninstall-app.sh"):
            with self.subTest(name=name):
                text = Path(name).read_text(encoding="utf-8")
                self.assertNotIn("/home/", text)
                self.assertIn("SCRIPT_DIR", text)

    def test_readme_covers_operation_and_recovery(self):
        text = Path("README.md").read_text(encoding="utf-8")
        for heading in ("빠른 시작", "동봉 구성요소", "안전 범위", "복구와 제거", "문제 해결"):
            with self.subTest(heading=heading):
                self.assertIn(heading, text)


if __name__ == "__main__":
    unittest.main()
