#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Unit tests for diagram-drift.py (stdlib unittest).

Run from the skill root: uv run scripts/tests/test_diagram-drift.py
"""

from __future__ import annotations

import base64
import json
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arch_fixtures import *  # noqa: E402,F403


class DriftTests(Project):
    def test_current_then_stale_then_outdated(self):
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-qm", "spine")
        self.assertEqual(self.build(containers_model())[0], 0)
        code, out = run("diagram-drift.py", str(self.root))
        self.assertEqual((code, out["diagrams"][0]["status"]), (0, "current"), out)

        spine = self.root / SPINE_REL
        spine.write_text(SPINE.replace("Azure Cosmos DB", "Azure SQL") + "\n### AD-4 Search\nAzure AI Search indexes documents.\n",
                         encoding="utf-8")
        self.git("commit", "-qam", "change AD-2, add AD-4")
        code, out = run("diagram-drift.py", str(self.root))
        entry = out["diagrams"][0]
        self.assertEqual((code, entry["status"]), (1, "stale"), out)
        self.assertEqual({(a["id"], a["change"], a["cited"]) for a in entry["ads"]},
                         {("AD-2", "changed", True), ("AD-4", "added", False)})
        self.assertEqual(entry["model"], "c4-containers.model.json")

        self.assertEqual(self.build(containers_model(), "c4-containers", "--force")[0], 0)
        spine.write_text(spine.read_text(encoding="utf-8").replace("Region is not decided yet.", "Region: TBD."), encoding="utf-8")
        self.git("commit", "-qam", "edit outside ADs")
        code, out = run("diagram-drift.py", str(self.root))
        self.assertEqual((code, out["diagrams"][0]["status"]), (0, "outdated"), out)

    def test_source_change_is_stale_and_reads_compressed_files(self):
        self.assertEqual(self.build(containers_model())[0], 0)
        path = self.folder / "c4-containers.drawio"
        tree = ET.parse(path)
        diagram = tree.getroot().find("diagram")
        graph = diagram.find("mxGraphModel")
        raw = urllib.parse.quote(ET.tostring(graph, encoding="unicode"), safe="~()*!.'")
        comp = zlib.compressobj(9, zlib.DEFLATED, -15)
        diagram.remove(graph)
        diagram.text = base64.b64encode(comp.compress(raw.encode()) + comp.flush()).decode()
        tree.write(path, encoding="utf-8")
        (self.root / "_bmad-output/specs/SPEC.md").write_text(SPEC + "\n## 3 Agents\n", encoding="utf-8")
        code, out = run("diagram-drift.py", str(self.root))
        entry = out["diagrams"][0]
        self.assertEqual((code, entry["status"]), (1, "stale"), out)
        self.assertEqual(entry["sources"], [{"file": "_bmad-output/specs/SPEC.md", "change": "changed"}])

    def test_unstamped_and_missing_spine(self):
        (self.folder / "hand-drawn.drawio").write_text('<mxfile><diagram id="a"><mxGraphModel><root><mxCell id="0"/></root></mxGraphModel></diagram></mxfile>',
                                                      encoding="utf-8")
        self.assertEqual(self.build(containers_model())[0], 0)
        (self.root / SPINE_REL).unlink()
        code, out = run("diagram-drift.py", str(self.root))
        self.assertEqual(code, 1)
        self.assertEqual({d["file"]: d["status"] for d in out["diagrams"]},
                         {"c4-containers.drawio": "spine-missing", "hand-drawn.drawio": "unstamped"})


if __name__ == "__main__":
    unittest.main()
