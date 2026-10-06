#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Describe a project's architecture spines, diagrams folder and existing diagrams as JSON.

Without --spine: the spines found at the BMad location; the architecture file (`architecture.md` in
the org config's `architecture_folder`, default docs/architecture), whether it exists yet, and the other
documents in that folder (per-cloud-provider files such as azure.md and aws.md, and the organisation's
architecture policies); the diagrams folder (org config
`diagrams_folder`), the diagrams already in it, the design documents folder (org config
`design_docs_folder`), its document models and which outlines and Word template it uses, this project's
`confluence_wiki_url` ("" when it has none), whether draw.io desktop is installed for
PNG/SVG exports, and the user's name and language from BMad's config.

With --spine: that spine's stamp, which is what a diagram records about the spine it was
drawn from: path, last git commit touching it (and whether it has uncommitted changes),
modification time, and a short hash of every AD's text. diagram-drift.py compares a
diagram's stamp with the current one to name the ADs that changed.

An AD is defined by a heading (`### AD-4 Hosting`) or by a bold id at the start of a list item
or paragraph (`- **AD-4** ...`); a plain line that merely starts with an id is part of the text
around it. An AD runs to the next AD, or, when it is a heading, to the next heading at the same
or a higher level. The first definition of an id wins; later ones are reported under
`duplicates`, which is a defect in the spine. `next_ad` is the id a new AD should take.

Usage:
    uv run spine.py <project-root> [--spine <path>] [-o <file>]

Exit codes: 0=success, 1=no spine found or bad path
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

SPINE_GLOB = "_bmad-output/planning-artifacts/architecture/*/ARCHITECTURE-SPINE.md"
DEFAULT_ARCH_FOLDER = "docs/architecture"
ARCHITECTURE_FILE = "architecture.md"
DEFAULT_FOLDER = "docs/architecture/diagrams"
DEFAULT_DOCS_FOLDER = "docs/architecture/design"
CONFIG_FILES = ["_bmad/config.toml", "_bmad/custom/config.toml", "_bmad/custom/config.user.toml"]
# An AD is defined only by a heading ("### AD-4 Hosting") or a bold id at the start of a list item or
# paragraph ("- **AD-4** ..."). A plain line that starts with an id ("AD-2 constrains this") is a mention
# inside another AD's text, not a definition.
AD_START = re.compile(r"^ {0,3}(?:(#{1,6})\s+(?:\*\*|__)?|(?:[-*+]\s+)?(?:\*\*|__))\[?(AD-\d+)\b\]?(?:\*\*|__)?[\s:.\-–—]*(.*)$")
HEADING = re.compile(r"^ {0,3}(#{1,6})\s")


def find_spines(root: Path) -> list[Path]:
    return sorted(root.glob(SPINE_GLOB))


def core_settings(root: Path) -> dict:
    """user_name and communication_language from BMad's [core] config (later files win)."""
    found = {"user_name": "", "communication_language": "English"}
    for rel in ["_bmad/config.toml", "_bmad/config.user.toml", "_bmad/custom/config.toml", "_bmad/custom/config.user.toml"]:
        path = root / rel
        if path.is_file():
            try:
                core = tomllib.loads(path.read_text(encoding="utf-8")).get("core", {})
            except tomllib.TOMLDecodeError:
                continue
            found.update({k: str(v) for k, v in core.items() if k in found and str(v).strip()})
    return found


def diagrams_folder(root: Path) -> Path:
    """The org config's diagrams_folder (custom overrides win), resolved against the root."""
    return org_folder(root, "diagrams_folder", DEFAULT_FOLDER)


def architecture_file(root: Path) -> Path:
    """The architecture answers and principles Da Vinci agrees before bmad-architecture: <architecture_folder>/architecture.md."""
    return org_folder(root, "architecture_folder", DEFAULT_ARCH_FOLDER) / ARCHITECTURE_FILE


def architecture_docs(main: Path) -> list[str]:
    """The other documents beside architecture.md: per-provider files (azure.md, aws.md ...) and policies."""
    folder = main.parent
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_file() and p != main and not p.name.startswith("."))


def design_docs_folder(root: Path) -> Path:
    """The org config's design_docs_folder (custom overrides win), resolved against the root."""
    return org_folder(root, "design_docs_folder", DEFAULT_DOCS_FOLDER)


def org_folder(root: Path, key: str, default: str) -> Path:
    value = default
    for rel in CONFIG_FILES:
        path = root / rel
        if path.is_file():
            try:
                found = tomllib.loads(path.read_text(encoding="utf-8")).get("modules", {}).get("org", {}).get(key)
            except tomllib.TOMLDecodeError:
                continue
            if isinstance(found, str) and found.strip():
                value = found.strip()
    value = value.replace("{project-root}", "").lstrip("/\\") if value.startswith("{project-root}") else value
    path = Path(value)
    return path if path.is_absolute() else root / path


def org_value(root: Path, key: str) -> str:
    """A [modules.org] setting of this project (custom overrides win); "" when unset or 'none'."""
    value = ""
    for rel in CONFIG_FILES:
        path = root / rel
        if path.is_file():
            try:
                found = tomllib.loads(path.read_text(encoding="utf-8")).get("modules", {}).get("org", {}).get(key)
            except tomllib.TOMLDecodeError:
                continue
            if isinstance(found, str):
                value = found.strip()
    return "" if value.lower() == "none" else value


def design_templates(folder: Path) -> dict:
    """The project's own design-doc outlines and Word style template, where it has them."""
    t = folder / "templates"
    return {"hld": (t / "hld.md").as_posix() if (t / "hld.md").is_file() else "kit default",
            "lld": (t / "lld.md").as_posix() if (t / "lld.md").is_file() else "kit default",
            "word": (t / "reference.docx").as_posix() if (t / "reference.docx").is_file() else "kit default styles"}


def drawio_cli() -> str | None:
    env = os.environ.get("DRAWIO_CLI")
    if env:
        return env if Path(env).is_file() or shutil.which(env) else None
    for name in ("drawio", "draw.io"):
        if shutil.which(name):
            return shutil.which(name)
    for app in ("/Applications/draw.io.app/Contents/MacOS/draw.io", r"C:\Program Files\draw.io\draw.io.exe"):
        if Path(app).is_file():
            return app
    return None


def parse_ads(text: str) -> tuple[dict[str, dict], list[str]]:
    """Return ({AD id: {title, line, hash}}, duplicate ids)."""
    lines = text.splitlines()
    starts = []
    for i, line in enumerate(lines):
        m = AD_START.match(line)
        if m:
            level = len(m.group(1)) if m.group(1) else 0
            starts.append((i, m.group(2), level, m.group(3).strip(" *_")))
    ads: dict[str, dict] = {}
    duplicates: list[str] = []
    for n, (i, ad_id, level, title) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        for j in range(i + 1, end):
            h = HEADING.match(lines[j])
            if h and (level == 0 or len(h.group(1)) <= level):
                end = j
                break
        if ad_id in ads:
            duplicates.append(ad_id)
            continue
        body = re.sub(r"\n{3,}", "\n\n", "\n".join(l.rstrip() for l in lines[i:end]).strip())
        ads[ad_id] = {"title": title, "line": i + 1, "hash": hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]}
    return ads, duplicates


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _git(root: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def stamp(root: Path, spine: Path) -> dict:
    rel = spine.resolve().relative_to(root.resolve()).as_posix()
    log = _git(root, "log", "-1", "--format=%H%x09%cI", "--", rel)
    commit, committed = (log.split("\t") + [""])[:2] if log else ("", "")
    dirty = bool(_git(root, "status", "--porcelain", "--", rel)) if commit else False
    modified = datetime.fromtimestamp(spine.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    ads, duplicates = parse_ads(spine.read_text(encoding="utf-8"))
    top = max((int(a[3:]) for a in ads), default=0)
    return {"spine": rel, "spine_commit": commit, "spine_committed": committed, "spine_dirty": dirty,
            "spine_modified": modified, "ad_hashes": {k: v["hash"] for k, v in ads.items()},
            "ads": ads, "duplicates": duplicates, "next_ad": f"AD-{top + 1}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project_root")
    parser.add_argument("--spine", help="Spine to describe (relative to the project root, or absolute)")
    parser.add_argument("-o", "--output", help="Write the JSON here instead of stdout")
    args = parser.parse_args(argv)
    root = Path(args.project_root).resolve()

    if args.spine:
        spine = Path(args.spine)
        spine = spine if spine.is_absolute() else root / spine
        try:
            result = {"status": "success", **stamp(root, spine)} if spine.is_file() else \
                {"status": "error", "message": f"spine not found: {args.spine}"}
        except ValueError:
            result = {"status": "error", "message": f"spine must be inside the project: {spine}"}
    else:
        spines = find_spines(root)
        folder = diagrams_folder(root)
        design_folder = design_docs_folder(root)
        architecture = architecture_file(root)
        result = {
            "status": "success" if spines else "error",
            "spines": [p.relative_to(root).as_posix() for p in spines],
            "architecture": {"path": architecture.as_posix(), "exists": architecture.is_file(),
                             "other_docs": architecture_docs(architecture)},
            "diagrams_folder": folder.as_posix(),
            "diagrams": sorted(p.name for p in folder.glob("*.drawio")) if folder.is_dir() else [],
            "design_docs_folder": design_folder.as_posix(),
            "design_docs": sorted(p.name for p in design_folder.glob("*.doc.json")) if design_folder.is_dir() else [],
            "design_templates": design_templates(design_folder),
            "confluence_wiki_url": org_value(root, "confluence_wiki_url"),
            "drawio_cli": drawio_cli(),
            **core_settings(root),
        }
        if not spines:
            result["message"] = f"no architecture spine at {SPINE_GLOB}"

    text = json.dumps(result, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
