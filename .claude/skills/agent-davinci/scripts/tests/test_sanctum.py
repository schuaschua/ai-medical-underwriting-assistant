#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Unit tests for init-sanctum.py and wake.py (stdlib unittest).

Run from the kit root: uv run skills/agent-davinci/scripts/tests/test_sanctum.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SKILL / "scripts" / script), *args], capture_output=True, text=True)


class SanctumTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "_bmad").mkdir()
        (self.root / "_bmad" / "config.toml").write_text(
            '[core]\nuser_name = "Alex"\n[modules.org]\nregion = "uae"\n'
            'architecture_folder = "{project-root}/docs/architecture"\n', encoding="utf-8")
        self.sanctum = self.root / "_bmad" / "memory" / "agent-davinci"

    def tearDown(self):
        self._tmp.cleanup()

    def test_first_breath_then_waking(self):
        self.assertIn("MODE: FIRST_BREATH", run("wake.py", str(self.root)).stdout)

        out = json.loads(run("init-sanctum.py", str(self.root), str(SKILL)).stdout)
        self.assertEqual(out["status"], "created")
        self.assertEqual(set(out["written"]),
                         {"INDEX.md", "PERSONA.md", "CREED.md", "BOND.md", "MEMORY.md", "CAPABILITIES.md"})
        self.assertIn("Met Alex", (self.sanctum / "PERSONA.md").read_text(encoding="utf-8"))
        bond = (self.sanctum / "BOND.md").read_text(encoding="utf-8")
        self.assertIn("docs/architecture/architecture.md", bond)
        self.assertIn("uae", bond)
        self.assertNotIn("{project-root}", bond)

        (self.sanctum / "MEMORY.md").write_text("# Memory\nmine", encoding="utf-8")
        self.assertEqual(json.loads(run("init-sanctum.py", str(self.root), str(SKILL)).stdout)["status"], "exists")
        self.assertEqual((self.sanctum / "MEMORY.md").read_text(encoding="utf-8"), "# Memory\nmine")

        woke = run("wake.py", str(self.root)).stdout
        self.assertIn("MODE: WAKING", woke)
        self.assertIn("===== CREED.md =====", woke)
        for code in ("[AP]", "[DG]", "[DS]", "[DD]"):
            self.assertIn(code, woke)
        self.assertNotIn("first-breath", woke)
        self.assertNotIn("memory-guidance", woke)


if __name__ == "__main__":
    unittest.main()
