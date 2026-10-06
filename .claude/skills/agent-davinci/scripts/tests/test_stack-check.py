#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Unit tests for stack-check.py (stdlib unittest).

Run from the skill root: uv run scripts/tests/test_stack-check.py
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arch_fixtures import *  # noqa: E402,F403

ARCH = """# Architecture: chat

## Context

| # | Question | Answer | Source |
| --- | --- | --- | --- |
| 1a | Environment | Cloud | owner |
| 1b | Cloud platforms | Azure ([azure.md](azure.md)) | owner |
| 2b | Well-Architected priorities | 1 security, 2 reliability | owner |
| 3a | Application type | Pure web application | owner |
| 3b | Architecture style | Modular monolith | owner |
| 3d | Approved stack | React front end, Python back end, PostgreSQL | owner |
| 4a | AI | Some; existing models only, on Azure OpenAI | owner |

## DevOps

| # | Item | Answer | Source |
| --- | --- | --- | --- |
| 5a | CI | Jenkins | owner |
| 5b | CD / GitOps | Argo CD, pull-based | owner |
| 5c | Source control and branching | GitHub, trunk-based | owner |
| 5d | Infrastructure as code | Terraform | owner |
| 6a | Environments | Dev, Test, Prod | owner |
| 6b | Approval gates and release strategy | tests and a named approver before Prod; canary | owner |
| 6c | Secrets and pipeline security | OIDC federation, no stored keys; SAST and dependency scans | owner |
"""
AZURE = """# Azure: chat

| # | Question | Answer | Source |
| --- | --- | --- | --- |
| 1d | Regions and availability | UAE North, zone-redundant | owner |
| 2d | Naming and tags | `org-uaen-chat-<type>-01`; tags: owner, cost centre, environment | owner |
"""
COMPLIANT = {
    "Jenkinsfile": "pipeline { stages { stage('scan') { steps { sh 'semgrep ci'; sh 'pip-audit' } } } }\n",
    "infra/main.tf": 'provider "azurerm" {}\nresource "azurerm_resource_group" "rg" {\n  location = "uaenorth"\n'
                     '  tags = { owner = "a", cost_center = "b", environment = "c" }\n}\n',
    "infra/prod.tfvars": 'location = "UAE North"\n',
    "infra/dev.tfvars": "\n",
    "deploy/app.yaml": "apiVersion: argoproj.io/v1alpha1\nkind: Application\n",
    "web/package.json": json.dumps({"dependencies": {"react": "18", "react-dom": "18"}}),
    "api/requirements.txt": "fastapi==0.110\npsycopg[binary]>=3\nopenai\nazure-identity\n",
}


class StackCheckTests(Project):
    def write(self, rel: str, text: str = "x\n") -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def project(self, **extra: str) -> None:
        self.write("docs/architecture/architecture.md", ARCH)
        self.write("docs/architecture/azure.md", AZURE)
        for rel, text in {**COMPLIANT, **extra}.items():
            self.write(rel, text)

    def check(self, *flags: str) -> tuple[int, dict]:
        return run("stack-check.py", str(self.root), *flags)

    def off(self, out: dict) -> dict:
        return {o["tool"]: o for o in out["off_stack"]}

    def test_no_architecture_passes_quietly(self):
        code, out = self.check()
        self.assertEqual((code, out["status"]), (0, "no_architecture"))

    def test_no_answer_tables(self):
        self.write("docs/architecture/architecture.md", "# Architecture\n\n## Principles\n")
        code, out = self.check()
        self.assertEqual((code, out["status"]), (0, "no_answers"))

    def test_compliant_project(self):
        self.project()
        code, out = self.check()
        self.assertEqual(code, 0, json.dumps(out, indent=1))
        self.assertEqual(out["off_stack"], [])
        self.assertEqual(out["warnings"], [])
        checks = {c["check"]: c for c in out["checks"]}
        self.assertEqual(set(checks), {"cloud", "mobile", "frontend", "backend", "database", "ai", "ci", "cd", "iac", "scans", "source_host"})
        self.assertEqual(checks["backend"]["found"], {"fastapi": ["api/requirements.txt"]})
        self.assertIn("openai", checks["ai"]["found"])  # allowed by Azure OpenAI

    def test_judge_lists_what_files_cannot_show(self):
        self.project()
        code, out = self.check()
        judged = {j["part"] for j in out["judge"]}
        self.assertTrue({"2b", "3b", "5c", "6b", "1d", "2d"} <= judged, judged)
        self.assertFalse({"1b", "3d", "5a", "5d"} & judged, judged)

    def test_devops_tools_off_stack(self):
        self.project(**{".github/workflows/ci.yml": "jobs: {}\n", "infra/extra.bicep": "param x string\n"})
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertEqual({"github-actions", "bicep"}, set(self.off(out)))
        self.assertEqual(self.off(out)["bicep"]["agreed"], ["terraform"])

    def test_application_stack_off_stack(self):
        self.project(**{"admin/package.json": json.dumps({"dependencies": {"@angular/core": "17", "express": "4", "mongoose": "8"}}),
                        "svc/Svc.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web"><ItemGroup>'
                                          '<PackageReference Include="Anthropic.SDK" Version="1" /></ItemGroup></Project>'})
        code, out = self.check()
        self.assertEqual(code, 1)
        off = self.off(out)
        self.assertTrue({"angular", "express", "mongodb", "aspnet", "anthropic"} <= set(off), set(off))
        self.assertEqual(off["aspnet"]["check"], "backend")

    def test_named_framework_narrows_language(self):
        self.project(**{"api/requirements.txt": "django\n"})
        text = ARCH.replace("Python back end", "FastAPI back end")
        self.write("docs/architecture/architecture.md", text)
        code, out = self.check()
        self.assertIn("django", self.off(out))

    def test_other_cloud_and_mobile_for_pure_web(self):
        self.project(**{"worker/requirements.txt": "boto3\n", "mobile/package.json": json.dumps({"dependencies": {"react-native": "0.74", "react": "18"}})})
        code, out = self.check()
        off = self.off(out)
        self.assertIn("aws", off)
        self.assertEqual(off["react-native"]["check"], "mobile")
        self.assertNotIn("react", off)

    def test_on_premises_forbids_every_cloud(self):
        self.write("docs/architecture/architecture.md",
                   "| # | Question | Answer | Source |\n| --- | --- | --- | --- |\n| 1a | Environment | On-premises | owner |\n")
        self.write("infra/main.tf", 'provider "azurerm" {}\n')
        code, out = self.check()
        self.assertEqual(self.off(out)["azure"]["part"], "1a")

    def test_scans_and_stored_keys(self):
        self.project(Jenkinsfile="withCredentials([string(credentialsId: 'x', variable: 'AZURE_CLIENT_SECRET')]) { sh 'semgrep ci' }\n")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("stored cloud credentials", self.off(out))
        self.assertIn({"check": "scans", "part": "6c", "tool": "dependency"}, out["missing"])

    def test_missing_scan_passes_before_any_pipeline(self):
        self.project()
        (self.root / "Jenkinsfile").unlink()
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn({"check": "ci", "part": "5a", "tool": "jenkins"}, out["missing"])
        code, out = self.check("--strict")
        self.assertEqual((code, out["status"]), (1, "missing"))

    def test_source_host_from_git_remote(self):
        self.project()
        self.git("init", "-q")
        self.git("remote", "add", "origin", "https://bitbucket.org/acme/chat.git")
        code, out = self.check()
        self.assertEqual(self.off(out)["bitbucket"]["check"], "source_host")

    def test_warnings_for_regions_tags_and_environments(self):
        self.project(**{"infra/main.tf": 'provider "azurerm" {}\nresource "x" "y" { location = "westeurope" }\n',
                        "infra/uat.tfvars": "\n"})
        code, out = self.check()
        self.assertEqual(code, 0, out)
        warned = {(w["check"], w["value"]) for w in out["warnings"]}
        self.assertIn(("regions", "westeurope"), warned)
        self.assertIn(("tags", "cost centre"), warned)
        self.assertIn(("environments", "uat"), warned)
        self.assertNotIn(("regions", "UAE North"), warned)

    def test_legacy_numbered_rows_and_item_keyed_devops(self):
        self.write("docs/architecture/architecture.md",
                   "| # | Question | Answer | Source |\n| --- | --- | --- | --- |\n| 15 | Approved stack | Vue front end | owner |\n\n"
                   "## DevOps\n\n| Item | Answer | Source |\n| --- | --- | --- |\n| CI | GitHub Actions | owner |\n")
        self.write("web/package.json", json.dumps({"dependencies": {"react": "18"}}))
        self.write("Jenkinsfile")
        code, out = self.check()
        self.assertEqual({"react", "jenkins"}, set(self.off(out)))

    def test_ignores_dependency_and_gitignored_folders(self):
        self.project(**{"node_modules/pkg/main.bicep": "x", "scratch/old.bicep": "x", ".gitignore": "scratch/\n"})
        self.git("init", "-q")
        code, out = self.check()
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
