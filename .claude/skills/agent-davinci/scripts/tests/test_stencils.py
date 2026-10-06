#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for stencils.py and the shipped stencil catalogues (stdlib unittest).

Run from the skill root: uv run scripts/tests/test_stencils.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arch_fixtures import run  # noqa: E402

CATALOGUES = Path(__file__).resolve().parent.parent.parent / "assets" / "stencils"


class StencilTests(unittest.TestCase):
    def test_azure_catalogue_is_well_formed(self):
        cat = json.loads((CATALOGUES / "azure.json").read_text(encoding="utf-8"))
        ids = [s["id"] for s in cat["stencils"]]
        self.assertEqual(len(ids), len(set(ids)))
        for s in cat["stencils"]:
            self.assertIn(f"image=img/lib/azure2/{s['id']}.svg", s["style"], s["id"])
            self.assertTrue(s["title"] and s["w"] and s["h"], s["id"])

    def test_search_finds_common_services_first(self):
        for query, expected in (("Cosmos DB", "databases/Azure_Cosmos_DB"), ("Key Vault", "security/Key_Vaults"),
                                ("Azure OpenAI", "ai_machine_learning/Azure_OpenAI"), ("Front Door", "networking/Front_Doors"),
                                ("Power BI", "power_platform/PowerBI"), ("CosmosDB", "databases/Azure_Cosmos_DB")):
            code, out = run("stencils.py", "search", "azure", *query.split())
            self.assertEqual(code, 0, out)
            self.assertEqual(out["matches"][0]["id"], expected, query)

    def test_unknown_provider_and_no_match(self):
        code, out = run("stencils.py", "search", "oracle", "db")
        self.assertEqual(code, 1)
        self.assertIn("azure", out["providers"])
        self.assertEqual(run("stencils.py", "search", "azure", "zzqx")[0], 1)

    def test_extract_takes_one_library_and_dedupes(self):
        index = [
            {"style": "image;html=1;image=img/lib/azure2/compute/Function_Apps.svg;", "w": 68, "h": 60, "title": "Function Apps", "tags": "azure2 function"},
            {"style": "image;html=1;image=img/lib/azure2/compute/Function_Apps.svg;", "w": 68, "h": 60, "title": "Function Apps", "tags": "dup"},
            {"style": "shape=mxgraph.aws4.lambda;html=1;", "w": 78, "h": 78, "title": "Lambda", "tags": "aws"},
        ]
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            src, out = Path(tmp) / "index.json", Path(tmp) / "x.json"
            src.write_text(json.dumps(index), encoding="utf-8")
            code, res = run("stencils.py", "extract", "--index", str(src), "--provider", "azure", "--prefix", "img/lib/azure2/", "-o", str(out))
            self.assertEqual((code, res["stencils"]), (0, 1), res)
            self.assertEqual(json.loads(out.read_text())["stencils"][0]["id"], "compute/Function_Apps")
            code, res = run("stencils.py", "extract", "--index", str(src), "--provider", "aws", "--prefix", "mxgraph.aws4.", "-o", str(out))
            self.assertEqual(json.loads(out.read_text())["stencils"][0]["id"], "lambda")


if __name__ == "__main__":
    unittest.main()
