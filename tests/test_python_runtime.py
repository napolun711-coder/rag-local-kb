from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PythonRuntimeTests(unittest.TestCase):
    def test_deployment_python_is_pinned(self) -> None:
        self.assertEqual((ROOT / ".python-version").read_text(encoding="utf-8").strip(), "3.12")

    def test_setup_uses_managed_python_312(self) -> None:
        setup = (ROOT / "scripts" / "setup.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("UV_PYTHON_PREFERENCE = 'only-managed'", setup)
        self.assertIn("'--python', '3.12'", setup)

    def test_ci_verifies_python_312(self) -> None:
        for relative in (".github/workflows/ci.yml", ".github/workflows/release.yml"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("uv sync --frozen --no-dev --python 3.12", text)
            self.assertIn("sys.version_info[:2] == (3, 12)", text)

    def test_pyyaml_has_windows_cp312_wheel(self) -> None:
        lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
        self.assertIn("pyyaml-6.0.3-cp312-cp312-win_amd64.whl", lock)


if __name__ == "__main__":
    unittest.main()
