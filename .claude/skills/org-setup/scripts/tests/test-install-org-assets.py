#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for install-org-assets.py against a throwaway project root.

Run from the skill root: uv run scripts/tests/test-install-org-assets.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
SCRIPT = SKILL / "scripts" / "install-org-assets.py"


def run(*args: str) -> tuple[int, dict]:
    out = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)
    return out.returncode, json.loads(out.stdout)


class InstallOrgAssetsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "_bmad").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def base_args(self, *extra: str) -> list[str]:
        return [
            "--project-root", str(self.root),
            "--standards-folder", "{project-root}/docs/standards",
            "--governance-folder", "{project-root}/docs/governance",
            "--jira-site", "https://example.atlassian.net/",
            "--jira-project-key", "PROJ",
            "--skills-mode", "skip",
            *extra,
        ]

    def test_fresh_install_copies_everything(self):
        code, out = run(*self.base_args())
        self.assertEqual(code, 0, out)
        for name in ["azure.md", "terraform.md", "security.md", "coding-style.md"]:
            self.assertTrue((self.root / "docs" / "standards" / name).is_file(), name)
        for name in ["TechnicalArchitectureAssessment.docx", "DataGovernancePrivacyAssessment.docx", "EnterpriseAIRiskAssessment.docx"]:
            self.assertTrue((self.root / "docs" / "governance" / name).is_file(), name)
        custom = self.root / "_bmad" / "custom"
        self.assertEqual(len(out["overrides"]["written"]), 10)
        dev = tomllib.loads((custom / "bmad-agent-dev.toml").read_text(encoding="utf-8"))
        self.assertEqual(dev["agent"]["persistent_facts"], ["file:docs/architecture/*.md", "file:docs/standards/*.md"])
        epics = tomllib.loads((custom / "bmad-create-epics-and-stories.toml").read_text(encoding="utf-8"))
        self.assertIn("Jira project PROJ (https://example.atlassian.net)", epics["workflow"]["on_complete"])
        self.assertEqual(len(epics["workflow"]["persistent_facts"]), 1)
        self.assertIn("token budgets", epics["workflow"]["on_complete"])
        sprints = tomllib.loads((custom / "bmad-sprint-planning.toml").read_text(encoding="utf-8"))
        self.assertIn("Scrooge", sprints["workflow"]["on_complete"])
        self.assertIn("token-budgets.json", sprints["workflow"]["on_complete"])
        arch = tomllib.loads((custom / "bmad-architecture.toml").read_text(encoding="utf-8"))
        self.assertEqual(arch["workflow"]["persistent_facts"],
                         ["file:docs/architecture/*.md", "file:docs/standards/*.md"])
        self.assertIn("docs/architecture/architecture.md", arch["workflow"]["activation_steps_prepend"][0])
        self.assertIn("agent-davinci", arch["workflow"]["activation_steps_prepend"][0])
        self.assertIn("Jules", arch["workflow"]["on_complete"])
        self.assertIn("Scrooge", arch["workflow"]["on_complete"])
        self.assertIn("agent-davinci", arch["workflow"]["on_complete"])
        for name in ("bmad-build.toml", "bmad-build-auto.toml"):
            build = tomllib.loads((custom / name).read_text(encoding="utf-8"))
            self.assertEqual(build["workflow"]["persistent_facts"],
                             ["file:docs/architecture/*.md", "file:docs/standards/*.md"])
            self.assertIn("close out the project", build["workflow"]["on_complete"])
            self.assertIn("ai_feedback.py", build["workflow"]["on_complete"])
            self.assertIn("agent-davinci/scripts/stack-check.py", build["workflow"]["on_complete"])
        build = tomllib.loads((custom / "bmad-build.toml").read_text(encoding="utf-8"))
        self.assertIn("docs/architecture/architecture.md", build["workflow"]["activation_steps_prepend"][0])
        self.assertIn("agent-davinci", build["workflow"]["activation_steps_prepend"][0])
        spec = tomllib.loads((custom / "bmad-spec.toml").read_text(encoding="utf-8"))
        self.assertIn("docs/architecture/architecture.md", spec["workflow"]["activation_steps_prepend"][0])
        self.assertIn("agent-davinci", spec["workflow"]["activation_steps_prepend"][0])
        review = tomllib.loads((custom / "bmad-code-review.toml").read_text(encoding="utf-8"))
        self.assertIn("stack-check.py", review["workflow"]["on_complete"])
        retro = tomllib.loads((custom / "bmad-retrospective.toml").read_text(encoding="utf-8"))
        self.assertIn("1 (very poor) to 5 (excellent)", retro["workflow"]["on_complete"])
        self.assertIn("not grounded in company data", retro["workflow"]["on_complete"])

    def test_never_overwrites_existing_files(self):
        standards = self.root / "docs" / "standards"
        standards.mkdir(parents=True)
        (standards / "azure.md").write_text("project version", encoding="utf-8")
        custom = self.root / "_bmad" / "custom"
        custom.mkdir()
        (custom / "bmad-build.toml").write_text("# mine\n", encoding="utf-8")
        code, out = run(*self.base_args())
        self.assertEqual(code, 0, out)
        self.assertEqual((standards / "azure.md").read_text(encoding="utf-8"), "project version")
        self.assertIn(str(standards / "azure.md"), [s["file"] for s in out["standards"]["skipped"]])
        self.assertEqual((custom / "bmad-build.toml").read_text(encoding="utf-8"), "# mine\n")
        existing = {Path(e["file"]).name: e["suggested"] for e in out["overrides"]["existing"]}
        self.assertIn("file:docs/standards/*.md", existing["bmad-build.toml"])

    def test_dry_run_writes_nothing(self):
        code, out = run(*self.base_args("--dry-run"))
        self.assertEqual(code, 0, out)
        self.assertFalse((self.root / "docs").exists())
        self.assertFalse((self.root / "_bmad" / "custom").exists())
        self.assertFalse((self.root / ".claude").exists())
        self.assertFalse((self.root / ".gitignore").exists())
        self.assertEqual(len(out["standards"]["copied"]), 4)

    def test_skills_already_in_the_project_folder_are_left_in_place(self):
        skills = self.root / ".claude" / "skills"
        for name in ("org-setup", "bmad-help"):
            (skills / name).mkdir(parents=True)
            (skills / name / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
        args = [a for a in self.base_args() if a not in ("--skills-mode", "skip")]
        code, out = run(*args, "--skills-source", str(skills))
        self.assertEqual(code, 0, out)
        self.assertEqual((out["skills"]["mode"], out["skills"]["installed"], out["skills"]["skipped"]), ("in-place", [], []))

    def test_links_skills_once(self):
        args = [a for a in self.base_args() if a not in ("--skills-mode", "skip")]
        code, out = run(*args)
        self.assertEqual(code, 0, out)
        link = self.root / ".claude" / "skills" / "org-setup"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), SKILL)
        code, again = run(*args)
        self.assertEqual(again["skills"]["installed"], [])
        self.assertTrue(all(s["reason"] == "already installed from this kit" for s in again["skills"]["skipped"]))

    def test_adds_scrooge_hooks_and_gitignore_once(self):
        (self.root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        code, out = run(*self.base_args("--token-usage-folder", "{project-root}/reports/tokens"))
        self.assertEqual(code, 0, out)
        self.assertEqual(out["gitignore"]["added"], ["reports/tokens/"])
        self.assertIn("reports/tokens/", (self.root / ".gitignore").read_text(encoding="utf-8").splitlines())
        settings = json.loads((self.root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
        for event in ("Stop", "SubagentStop"):
            hook = settings["hooks"][event][0]["hooks"][0]
            self.assertTrue(hook["async"])
            self.assertIn(".claude/skills/agent-scrooge/scripts/token_report.py", hook["command"])
        code, again = run(*self.base_args("--token-usage-folder", "reports/tokens"))
        self.assertEqual((again["gitignore"]["added"], again["hooks"]["added"]), ([], []))
        settings = json.loads((self.root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
        self.assertEqual(len(settings["hooks"]["Stop"]), 1)

    def test_keeps_other_settings_and_honours_no_hooks(self):
        claude = self.root / ".claude"
        claude.mkdir()
        (claude / "settings.local.json").write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}), encoding="utf-8")
        code, out = run(*self.base_args("--no-hooks"))
        self.assertEqual((code, out["hooks"]["added"]), (0, []))
        code, out = run(*self.base_args())
        settings = json.loads((claude / "settings.local.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["permissions"], {"allow": ["Bash(ls)"]})
        self.assertEqual(out["hooks"]["added"], ["Stop", "SubagentStop"])

    def test_principles_follow_the_architecture_folder(self):
        code, out = run(*self.base_args("--architecture-folder", "{project-root}/design/arch"))
        self.assertEqual(code, 0, out)
        arch = tomllib.loads((self.root / "_bmad" / "custom" / "bmad-architecture.toml").read_text(encoding="utf-8"))
        self.assertEqual(arch["workflow"]["persistent_facts"][0], "file:design/arch/*.md")

    def test_rejects_bad_project_key(self):
        args = self.base_args()
        args[args.index("PROJ")] = "proj"
        code, out = run(*args)
        self.assertEqual(code, 1)
        self.assertEqual(out["status"], "error")

    def test_rejects_unresolved_project_root(self):
        args = self.base_args()
        args[1] = "{project-root}"
        code, out = run(*args)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
