#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Install the Org Kit's project assets without overwriting anything.

Runs after merge-config.py and merge-help-csv.py. Six jobs, each reported in
the JSON printed to stdout:

1. standards   - copy assets/standards/*.md into the standards folder
2. governance  - copy assets/governance/*.docx (blank questionnaires) into the
                 governance folder
3. overrides   - render assets/custom/*.toml into {project-root}/_bmad/custom/,
                 writing a file only when it does not exist yet; for an existing
                 file the rendered content is returned as `suggested` so the user
                 can merge it by hand
4. skills      - link (or copy) the kit's skill folders into the project's
                 skills directory (default {project-root}/.claude/skills)
5. gitignore   - add Scrooge's token usage folder to {project-root}/.gitignore
6. hooks       - add Scrooge's background ledger refresh (Stop and SubagentStop,
                 async) to {project-root}/.claude/settings.local.json; an event
                 that already runs token_report.py is left alone

An existing target file or folder is never overwritten; it is listed under
`skipped` with the reason. Folder arguments may carry the literal
`{project-root}` token (as stored in _bmad/config.yaml); it is resolved against
--project-root here.

Exit codes: 0=success, 1=validation error, 2=runtime error
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

TOKEN = "{project-root}"
SKILL_ROOT = Path(__file__).resolve().parent.parent
KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9]+$")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project-root", required=True, help="Absolute project root (where _bmad/ lives)")
    parser.add_argument("--standards-folder", required=True, help="standards_folder config value, may contain {project-root}")
    parser.add_argument("--governance-folder", required=True, help="governance_folder config value, may contain {project-root}")
    parser.add_argument("--jira-site", required=True, help="jira_site config value, e.g. https://<site>.atlassian.net")
    parser.add_argument("--jira-project-key", required=True, help="jira_project_key config value, e.g. PROJ")
    parser.add_argument("--assets-dir", default=str(SKILL_ROOT / "assets"), help="Kit assets folder (default: this skill's assets/)")
    parser.add_argument("--skills-source", default=str(SKILL_ROOT.parent), help="Folder holding the kit's skills (default: this skill's parent)")
    parser.add_argument("--skills-target", default=None, help="Project skills folder (default: {project-root}/.claude/skills)")
    parser.add_argument("--architecture-folder", default="docs/architecture", help="architecture_folder config value (Da Vinci's architecture.md and per-cloud files live here), may contain {project-root}")
    parser.add_argument("--token-usage-folder", default="docs/costing/token_usage", help="token_usage_folder config value, may contain {project-root}")
    parser.add_argument("--no-hooks", action="store_true", help="Do not add Scrooge's refresh hooks")
    parser.add_argument("--skills-mode", choices=["link", "copy", "skip"], default="link", help="How to install the kit's skills")
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen without writing")
    return parser.parse_args(argv)


def resolve(value: str, project_root: Path) -> Path:
    """Resolve a config path value against the project root."""
    if value.startswith(TOKEN):
        value = value[len(TOKEN):].lstrip("/\\")
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def copy_files(src_dir: Path, pattern: str, dest_dir: Path, dry_run: bool) -> dict:
    copied, skipped = [], []
    if not src_dir.is_dir():
        return {"copied": copied, "skipped": skipped, "target": str(dest_dir)}
    for src in sorted(src_dir.glob(pattern)):
        dest = dest_dir / src.name
        if dest.exists():
            skipped.append({"file": str(dest), "reason": "exists; left unchanged"})
            continue
        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        copied.append(str(dest))
    return {"copied": copied, "skipped": skipped, "target": str(dest_dir)}


def render_overrides(custom_src: Path, custom_dest: Path, values: dict[str, str], dry_run: bool) -> dict:
    written, existing = [], []
    if not custom_src.is_dir():
        return {"written": written, "existing": existing}
    for template in sorted(custom_src.glob("*.toml")):
        content = template.read_text(encoding="utf-8")
        for key, value in values.items():
            content = content.replace("{" + key + "}", value)
        dest = custom_dest / template.name
        if dest.exists():
            existing.append({"file": str(dest), "suggested": content})
            continue
        if not dry_run:
            custom_dest.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        written.append(str(dest))
    return {"written": written, "existing": existing}


def _relative(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def install_skills(source: Path, target: Path, mode: str, dry_run: bool) -> dict:
    installed, skipped = [], []
    if mode == "skip":
        return {"mode": mode, "installed": installed, "skipped": skipped, "target": str(target)}
    if target.exists() and source.resolve() == target.resolve():
        # The BMad installer already put the kit's skills in the project's skills folder, beside
        # every other module's; there is nothing to link, and the folder isn't all the kit's.
        return {"mode": "in-place", "installed": installed, "skipped": skipped, "target": str(target),
                "note": "skills already installed in the project's skills folder by the BMad installer"}
    for skill in sorted(p for p in source.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()):
        dest = target / skill.name
        if dest.exists() or dest.is_symlink():
            same = dest.resolve() == skill.resolve()
            skipped.append({"skill": skill.name, "reason": "already installed from this kit" if same else "exists; left unchanged"})
            continue
        if not dry_run:
            target.mkdir(parents=True, exist_ok=True)
            if mode == "link":
                dest.symlink_to(os.path.relpath(skill, target), target_is_directory=True)
            else:
                shutil.copytree(skill, dest, ignore=shutil.ignore_patterns("__pycache__", ".memlog.md"))
        installed.append(skill.name)
    return {"mode": mode, "installed": installed, "skipped": skipped, "target": str(target)}


HOOK_EVENTS = ("Stop", "SubagentStop")
REPORT_SCRIPT = "agent-scrooge/scripts/token_report.py"


def ensure_gitignore(project_root: Path, folder_rel: str, dry_run: bool) -> dict:
    path = project_root / ".gitignore"
    entry = folder_rel.rstrip("/") + "/"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if any(line.strip().strip("/") == entry.strip("/") for line in lines):
        return {"added": [], "skipped": [{"entry": entry, "reason": "already ignored"}], "file": str(path)}
    if not dry_run:
        text = "\n".join(lines + ["", "# Scrooge's token ledger (Org Kit)", entry]).lstrip("\n") + "\n"
        path.write_text(text, encoding="utf-8")
    return {"added": [entry], "skipped": [], "file": str(path)}


def ensure_hooks(project_root: Path, skills_rel: str, dry_run: bool) -> dict:
    path = project_root / ".claude" / "settings.local.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError as exc:
        return {"added": [], "skipped": [{"file": str(path), "reason": f"not valid JSON ({exc.msg}); left unchanged"}]}
    script = f'"$CLAUDE_PROJECT_DIR/{skills_rel}/{REPORT_SCRIPT}"'
    command = f'test -f {script} && python3 {script} "$CLAUDE_PROJECT_DIR" >/dev/null 2>&1 || true'
    hooks = settings.setdefault("hooks", {})
    added, skipped = [], []
    for event in HOOK_EVENTS:
        groups = hooks.setdefault(event, [])
        if any("token_report.py" in h.get("command", "") for g in groups for h in g.get("hooks", [])):
            skipped.append({"event": event, "reason": "already runs token_report.py; left unchanged"})
            continue
        groups.append({"hooks": [{"type": "command", "command": command, "async": True, "timeout": 120}]})
        added.append(event)
    if added and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return {"added": added, "skipped": skipped, "file": str(path), "command": command}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path(args.project_root)
    if TOKEN in args.project_root or not project_root.is_absolute() or not project_root.is_dir():
        print(json.dumps({"status": "error", "message": f"--project-root must be an existing absolute path: {args.project_root}"}))
        return 1
    if not KEY_PATTERN.match(args.jira_project_key):
        print(json.dumps({"status": "error", "message": f"jira_project_key must match ^[A-Z][A-Z0-9]+$: {args.jira_project_key}"}))
        return 1

    assets = Path(args.assets_dir)
    standards = resolve(args.standards_folder, project_root)
    governance = resolve(args.governance_folder, project_root)
    try:
        standards_rel = standards.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        print(json.dumps({"status": "error", "message": f"standards folder must be inside the project: {standards}"}))
        return 1

    token_usage = resolve(args.token_usage_folder, project_root)
    try:
        token_usage_rel = token_usage.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        print(json.dumps({"status": "error", "message": f"token usage folder must be inside the project: {token_usage}"}))
        return 1

    architecture = resolve(args.architecture_folder, project_root)
    try:
        architecture_rel = architecture.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        print(json.dumps({"status": "error", "message": f"architecture folder must be inside the project: {architecture}"}))
        return 1

    values = {
        "standards_glob": f"{standards_rel}/*.md",
        "architecture_file": f"{architecture_rel}/architecture.md",
        "architecture_glob": f"{architecture_rel}/*.md",
        "jira_site": args.jira_site.rstrip("/"),
        "jira_project_key": args.jira_project_key,
    }
    skills_target = resolve(args.skills_target, project_root) if args.skills_target else project_root / ".claude" / "skills"

    try:
        result = {
            "status": "success",
            "dry_run": args.dry_run,
            "standards": copy_files(assets / "standards", "*.md", standards, args.dry_run),
            "governance": copy_files(assets / "governance", "*.docx", governance, args.dry_run),
            "overrides": render_overrides(assets / "custom", project_root / "_bmad" / "custom", values, args.dry_run),
            "skills": install_skills(Path(args.skills_source), skills_target, args.skills_mode, args.dry_run),
            "gitignore": ensure_gitignore(project_root, token_usage_rel, args.dry_run),
            "hooks": ({"added": [], "skipped": [{"reason": "--no-hooks"}]} if args.no_hooks
                      else ensure_hooks(project_root, _relative(skills_target, project_root), args.dry_run)),
            "uv_available": shutil.which("uv") is not None,
        }
    except OSError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
