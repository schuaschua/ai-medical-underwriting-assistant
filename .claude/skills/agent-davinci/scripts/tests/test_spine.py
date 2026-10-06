#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Unit tests for spine.py (stdlib unittest).

Run from the skill root: uv run scripts/tests/test_spine.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arch_fixtures import *  # noqa: E402,F403


class SpineTests(Project):
    def test_lists_spines_folder_and_diagrams(self):
        (self.folder / "old.drawio").write_text("<mxfile/>", encoding="utf-8")
        code, out = run("spine.py", str(self.root))
        self.assertEqual(code, 0, out)
        self.assertEqual(out["spines"], [SPINE_REL])
        self.assertTrue(out["diagrams_folder"].endswith("docs/architecture/diagrams"))
        self.assertEqual(out["diagrams"], ["old.drawio"])

    def test_config_folder_and_override(self):
        (self.root / "_bmad/custom").mkdir(parents=True)
        (self.root / "_bmad/config.toml").write_text('[modules.org]\ndiagrams_folder = "{project-root}/docs/a"\n', encoding="utf-8")
        (self.root / "_bmad/custom/config.toml").write_text('[modules.org]\ndiagrams_folder = "docs/b"\n', encoding="utf-8")
        self.assertTrue(run("spine.py", str(self.root))[1]["diagrams_folder"].endswith("/docs/b"))

    def test_parses_heading_and_list_ads(self):
        code, out = run("spine.py", str(self.root), "--spine", SPINE_REL)
        self.assertEqual(code, 0, out)
        self.assertEqual(list(out["ads"]), ["AD-1", "AD-2", "AD-3"])
        self.assertEqual(out["ads"]["AD-1"]["title"], "Chat API")
        self.assertEqual(out["spine_commit"], "")  # not a git repository

    def test_mentions_are_not_definitions(self):
        spine = self.root / SPINE_REL
        spine.write_text(SPINE.replace("The Chat API reads and writes them.",
                                       "The Chat API reads and writes them.\nAD-3 below covers the model.\n- AD-1 also applies here."),
                         encoding="utf-8")
        code, out = run("spine.py", str(self.root), "--spine", SPINE_REL)
        self.assertEqual(code, 0, out)
        self.assertEqual(list(out["ads"]), ["AD-1", "AD-2", "AD-3"])   # the forward reference didn't claim AD-3
        self.assertEqual(out["duplicates"], [])                        # nor did the in-body mention of AD-1
        self.assertEqual(out["ads"]["AD-3"]["title"], "Azure OpenAI answers questions; the Chat API calls it over HTTPS.")
        self.assertEqual(out["next_ad"], "AD-4")

    def test_reports_user_settings(self):
        (self.root / "_bmad").mkdir(exist_ok=True)
        (self.root / "_bmad/config.toml").write_text('[core]\ncommunication_language = "Arabic"\n', encoding="utf-8")
        (self.root / "_bmad/config.user.toml").write_text('[core]\nuser_name = "Dj"\n', encoding="utf-8")
        out = run("spine.py", str(self.root))[1]
        self.assertEqual((out["user_name"], out["communication_language"]), ("Dj", "Arabic"))

    def test_no_spine(self):
        (self.root / SPINE_REL).unlink()
        code, out = run("spine.py", str(self.root))
        self.assertEqual((code, out["status"]), (1, "error"))
        # the architecture questions come before the spine, so their file is known without one
        self.assertTrue(out["architecture"]["path"].endswith("docs/architecture/architecture.md"))
        self.assertFalse(out["architecture"]["exists"])

    def test_architecture_file_and_provider_docs_follow_the_folder(self):
        (self.root / "_bmad").mkdir(exist_ok=True)
        (self.root / "_bmad/config.toml").write_text('[modules.org]\narchitecture_folder = "{project-root}/arch"\n',
                                                     encoding="utf-8")
        (self.root / "arch/diagrams").mkdir(parents=True)
        (self.root / "arch/architecture.md").write_text("# Architecture\n", encoding="utf-8")
        (self.root / "arch/azure.md").write_text("# Azure\n", encoding="utf-8")
        (self.root / "arch/Cloud policy.pdf").write_bytes(b"%PDF")
        out = run("spine.py", str(self.root))[1]
        self.assertEqual(out["architecture"], {"path": (self.root / "arch/architecture.md").as_posix(), "exists": True,
                                               "other_docs": ["Cloud policy.pdf", "azure.md"]})


if __name__ == "__main__":
    unittest.main()
