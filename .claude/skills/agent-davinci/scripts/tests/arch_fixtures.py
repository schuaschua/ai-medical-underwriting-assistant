"""Shared fixture for the agent-davinci script tests: a throwaway project with a spine and a spec."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
SPINE_REL = "_bmad-output/planning-artifacts/architecture/chat/ARCHITECTURE-SPINE.md"
SPINE = """# Chat service architecture spine

## Decisions

### AD-1 Chat API
The Chat API (.NET 8) on Azure App Service serves the web client over HTTPS.

### AD-2 Conversation store
Conversations, including customer names, are stored in Azure Cosmos DB. The Chat API reads and writes them.

- **AD-3** Azure OpenAI answers questions; the Chat API calls it over HTTPS.

## Open questions
Region is not decided yet.
"""
SPEC = "# Spec\n\n## 2 Users\nCustomers ask questions in the web chat.\n"


def run(script: str, *args: str, env: dict | None = None) -> tuple[int, dict]:
    out = subprocess.run([sys.executable, str(SCRIPTS / script), *args], capture_output=True, text=True,
                         env={**os.environ, **(env or {})})
    return out.returncode, json.loads(out.stdout)


def containers_model(**extra) -> dict:
    model = {
        "type": "c4-containers", "title": "Chat service", "spine": SPINE_REL, "sources": ["_bmad-output/specs/SPEC.md"],
        "groups": [{"id": "sys", "label": "Chat service", "kind": "boundary", "cite": "AD-1"}],
        "nodes": [
            {"id": "customer", "label": "Customer", "kind": "person", "cite": "SPEC.md §2"},
            {"id": "api", "label": "Chat API", "kind": "container", "tech": ".NET 8", "group": "sys", "cite": "AD-1"},
            {"id": "db", "label": "Conversation store", "kind": "database", "tech": "Cosmos DB", "group": "sys",
             "data": ["PII"], "cite": ["AD-2"]},
            {"id": "aoai", "label": "Azure OpenAI", "kind": "container_ext", "cite": "AD-3"},
        ],
        "edges": [
            {"from": "customer", "to": "api", "label": "asks questions", "tech": "HTTPS", "cite": ["SPEC.md §2", "AD-1"]},
            {"from": "api", "to": "db", "label": "reads and writes conversations", "data": ["PII"], "cite": "AD-2"},
            {"from": "api", "to": "aoai", "label": "asks for answers", "tech": "HTTPS", "cite": "AD-3"},
        ],
        "gaps": ["No AD states the Azure region."],
    }
    model.update(extra)
    return model


class Project(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.root = Path(self.tmp.name).resolve()
        spine = self.root / SPINE_REL
        spine.parent.mkdir(parents=True)
        spine.write_text(SPINE, encoding="utf-8")
        (self.root / "_bmad-output/specs").mkdir(parents=True)
        (self.root / "_bmad-output/specs/SPEC.md").write_text(SPEC, encoding="utf-8")
        self.folder = self.root / "docs/architecture/diagrams"
        self.folder.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                            "GIT_COMMITTER_EMAIL": "t@t"})

    def build(self, model: dict, name: str = "c4-containers", *flags: str) -> tuple[int, dict]:
        path = self.folder / f"{name}.model.json"
        path.write_text(json.dumps(model), encoding="utf-8")
        return run("build-drawio.py", str(self.root), str(path), "--export", "none", *flags)
