#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Unit tests for region-sources.py and the region registry (stdlib unittest).

Run from the skill root: uv run scripts/tests/test_region-sources.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from urllib.parse import urlparse

SKILL = Path(__file__).resolve().parent.parent.parent
SCRIPT = SKILL / "scripts" / "region-sources.py"
REGISTRY = SKILL / "assets" / "regions.toml"
MODULE_YAML = SKILL.parent / "org-setup" / "assets" / "module.yaml"


def run(*args: str) -> tuple[int, dict]:
    out = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)
    return out.returncode, json.loads(out.stdout)


class RegionSourcesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "_bmad" / "custom").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel: str, region: str) -> None:
        (self.root / rel).write_text(f'[modules.org]\nregion = "{region}"\n', encoding="utf-8")

    def test_unset_region_is_none(self):
        code, out = run(str(self.root))
        self.assertEqual(code, 0, out)
        self.assertEqual((out["region"], out["from"], out["sources"]), ("none", "default", []))

    def test_reads_configured_region(self):
        self.write("_bmad/config.toml", "uae")
        code, out = run(str(self.root))
        self.assertEqual(code, 0, out)
        self.assertEqual(out["region"], "uae")
        self.assertEqual(out["domains"], ["rulebook.centralbank.ae"])
        self.assertEqual({s["id"] for s in out["sources"]}, {"CBUAE-ET", "CBUAE-AI"})

    def test_custom_override_wins(self):
        self.write("_bmad/config.toml", "uae")
        self.write("_bmad/custom/config.user.toml", "none")
        code, out = run(str(self.root))
        self.assertEqual(code, 0, out)
        self.assertEqual((out["region"], out["from"]), ("none", "_bmad/custom/config.user.toml"))

    def test_region_argument_skips_config(self):
        code, out = run(str(self.root), "--region", "UAE")
        self.assertEqual(code, 0, out)
        self.assertEqual((out["region"], out["from"]), ("uae", "--region"))

    def test_unknown_region_fails(self):
        code, out = run(str(self.root), "--region", "mars")
        self.assertEqual(code, 1)
        self.assertIn("uae", out["known"])


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.regions = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))["regions"]

    def test_module_yaml_offers_exactly_the_registry_regions(self):
        text = MODULE_YAML.read_text(encoding="utf-8")
        block = text.split("\nregion:", 1)[1]
        offered = re.findall(r'^\s+- value: "([^"]+)"', block, re.MULTILINE)
        self.assertEqual(sorted(offered), sorted(self.regions))

    def test_sources_are_complete_and_inside_the_region_domains(self):
        for code, entry in self.regions.items():
            ids = [s["id"] for s in entry["sources"]]
            self.assertEqual(len(ids), len(set(ids)), code)
            for source in entry["sources"]:
                for field in ("id", "title", "url", "applies_to"):
                    self.assertTrue(source.get(field), f"{code} {source.get('id')} {field}")
                url = urlparse(source["url"])
                self.assertEqual(url.scheme, "https", source["url"])
                self.assertIn(url.hostname, entry["domains"], source["url"])


if __name__ == "__main__":
    unittest.main()
