#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Unit tests for init-sanctum.py and wake.py against a throwaway project root.

Run from the skill root: uv run scripts/tests/test_init-sanctum.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
SCRIPTS = SKILL / "scripts"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True)


class InitAndWakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.root = Path(self.tmp.name)
        (self.root / "_bmad").mkdir()
        (self.root / "_bmad" / "config.user.toml").write_text(
            '[core]\nuser_name = "Tester"\ncommunication_language = "English"\n', encoding="utf-8")
        self.sanctum = self.root / "_bmad" / "memory" / "agent-jules"

    def tearDown(self):
        self.tmp.cleanup()

    def test_wake_routes_to_first_breath_without_sanctum(self):
        out = run(str(SCRIPTS / "wake.py"), str(self.root))
        self.assertIn("MODE: FIRST_BREATH", out.stdout)

    def test_init_scaffolds_self_contained_sanctum(self):
        out = run(str(SCRIPTS / "init-sanctum.py"), str(self.root), str(SKILL))
        self.assertEqual(out.returncode, 0, out.stderr)
        for name in ["INDEX.md", "PERSONA.md", "CREED.md", "BOND.md", "MEMORY.md", "CAPABILITIES.md", "PULSE.md"]:
            self.assertTrue((self.sanctum / name).is_file(), name)
        self.assertFalse((self.sanctum / "references" / "first-breath.md").exists())
        self.assertTrue((self.sanctum / "references" / "prompt-quality-canon.md").is_file())
        self.assertTrue((self.sanctum / "scripts" / "review-status.py").is_file())
        caps = (self.sanctum / "CAPABILITIES.md").read_text(encoding="utf-8")
        for code in ["AQ", "CL", "RC", "CW", "DR", "AL"]:
            self.assertIn(f"[{code}]", caps)
        self.assertIn("Tester", (self.sanctum / "BOND.md").read_text(encoding="utf-8"))
        self.assertNotIn("{sanctum_path}", (self.sanctum / "CREED.md").read_text(encoding="utf-8"))

    def test_wake_loads_sanctum_and_pulse(self):
        run(str(SCRIPTS / "init-sanctum.py"), str(self.root), str(SKILL))
        waking = run(str(SCRIPTS / "wake.py"), str(self.root))
        self.assertIn("MODE: WAKING", waking.stdout)
        self.assertNotIn("===== PULSE.md", waking.stdout)
        pulse = run(str(SCRIPTS / "wake.py"), str(self.root), "--pulse")
        self.assertIn("MODE: PULSE", pulse.stdout)
        self.assertIn("===== PULSE.md", pulse.stdout)


if __name__ == "__main__":
    unittest.main()
