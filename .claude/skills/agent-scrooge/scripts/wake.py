#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
Waking: load Scrooge's sanctum in one pass, or route to First Breath.

Run on activation. When the sanctum exists it prints the whole identity in a single read (INDEX,
PERSONA, CREED, BOND, MEMORY, CAPABILITIES) followed by the built-in capabilities, read live from
the skill's references/ frontmatter so a kit update reaches Scrooge without touching the sanctum.
When no sanctum exists it prints a directive to run First Breath.

The sanctum is <project-root>/_bmad/memory/agent-scrooge/. It also holds the project data files
(stories.json, savings.json, rate-card.json); those are data, not identity, and are not printed.

This loads runtime memory only. It never reads or writes config or customize.toml.

Usage:
    uv run wake.py <project-root>
"""

import re
import sys
from pathlib import Path

SKILL_NAME = "agent-scrooge"
SKILL_ROOT = Path(__file__).resolve().parent.parent
SKILL_ONLY = {"first-breath.md", "memory-guidance.md", "prompt-quality-canon.md"}

IDENTITY_FILES = ["INDEX.md", "PERSONA.md", "CREED.md", "BOND.md", "MEMORY.md", "CAPABILITIES.md"]


def emit(path: Path) -> None:
    print(f"\n===== {path.name} =====")
    try:
        print(path.read_text(encoding="utf-8").rstrip())
    except FileNotFoundError:
        print(f"(missing: {path.name})")


def frontmatter(path: Path) -> dict:
    match = re.match(r"^---\s*\n(.*?)\n---", path.read_text(encoding="utf-8"), re.DOTALL)
    meta = {}
    for line in (match.group(1).splitlines() if match else []):
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip().strip("'\"")
    return meta


def built_in_capabilities() -> str:
    rows = ["| Code | Name | Description | Load |", "|------|------|-------------|------|"]
    for md in sorted((SKILL_ROOT / "references").glob("*.md")):
        meta = frontmatter(md)
        if md.name not in SKILL_ONLY and meta.get("code") and meta.get("name"):
            rows.append(f"| [{meta['code']}] | {meta['name']} | {meta.get('description', '')} | `references/{md.name}` |")
    return "\n".join(rows)


def main() -> int:
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not positional:
        print("Usage: wake.py <project-root>", file=sys.stderr)
        return 2
    sanctum = Path(positional[0]).resolve() / "_bmad" / "memory" / SKILL_NAME
    if not ((sanctum / "CREED.md").is_file() and (sanctum / "MEMORY.md").is_file()):
        print("MODE: FIRST_BREATH")
        print(f"NO SANCTUM at {sanctum}")
        print("This is your one birth. Load references/first-breath.md and follow it.")
        return 0
    print("MODE: WAKING")
    print(f"Sanctum: {sanctum}")
    for name in IDENTITY_FILES:
        emit(sanctum / name)
    print("\n===== BUILT-IN CAPABILITIES (from the skill) =====")
    print(built_in_capabilities())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
