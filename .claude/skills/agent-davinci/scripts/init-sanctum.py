#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""
First Breath: deterministic sanctum scaffolding for Da Vinci.

Runs before the conversational awakening. Creates <project-root>/_bmad/memory/agent-davinci/
(or completes a partial one) with the identity files from assets/*-template.md, config values
substituted, and a sessions/ folder. It never overwrites a file that exists.

Capabilities and scripts stay in the skill and are not copied: wake.py lists the built-in
capabilities from the skill each session, so a kit update reaches Da Vinci without a migration.

It reads _bmad/config.toml and config.user.toml ([core], then [modules.org]) only to fill the
templates, and never writes config or customize.toml.

Usage:
    uv run init-sanctum.py <project-root> <skill-path>

Prints JSON: {"status": "created" | "completed" | "exists", "sanctum", "written", "kept"}.
"""

import json
import sys
import tomllib
from datetime import date
from pathlib import Path

SKILL_NAME = "agent-davinci"
TEMPLATES = ["INDEX", "PERSONA", "CREED", "BOND", "MEMORY", "CAPABILITIES"]


def config(bmad: Path) -> dict:
    merged: dict = {}
    for name in ("config.toml", "config.user.toml"):
        path = bmad / name
        if not path.is_file():
            continue
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            continue
        for section in (data.get("core", {}), data.get("modules", {}).get("org", {})):
            merged.update({k: str(v) for k, v in section.items() if isinstance(v, (str, int, float))})
    return merged


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: uv run init-sanctum.py <project-root> <skill-path>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    skill = Path(sys.argv[2]).resolve()
    sanctum = root / "_bmad" / "memory" / SKILL_NAME
    existed = sanctum.is_dir()
    if (sanctum / "CREED.md").is_file() and (sanctum / "MEMORY.md").is_file():
        print(json.dumps({"status": "exists", "sanctum": str(sanctum),
                          "message": "Da Vinci has already been born. Skipping First Breath scaffolding."}))
        return 0

    cfg = config(root / "_bmad")
    variables = {
        "user_name": cfg.get("user_name", "friend"),
        "communication_language": cfg.get("communication_language", "English"),
        "birth_date": date.today().isoformat(),
        "project_root": str(root),
        "sanctum_path": str(sanctum),
        "jira_project_key": cfg.get("jira_project_key", "not set"),
        "region": cfg.get("region", "none"),
        "architecture_folder": cfg.get("architecture_folder", "docs/architecture").replace("{project-root}/", ""),
    }
    sanctum.mkdir(parents=True, exist_ok=True)
    (sanctum / "sessions").mkdir(exist_ok=True)
    written, kept = [], sorted(p.name for p in sanctum.iterdir() if p.is_file())
    for name in TEMPLATES:
        out = sanctum / f"{name}.md"
        if out.exists():
            continue
        text = (skill / "assets" / f"{name}-template.md").read_text(encoding="utf-8")
        for key, value in variables.items():
            text = text.replace("{" + key + "}", value)
        out.write_text(text, encoding="utf-8")
        written.append(out.name)
    print(json.dumps({"status": "completed" if existed else "created", "sanctum": str(sanctum),
                      "written": written, "kept": kept}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
