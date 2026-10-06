#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""How long the project took: BMad stages, custom agents, epics and stories, start to finish.

Gathers dates from the project itself and writes project_timeline.csv to the token usage folder
(org config token_usage_folder, default docs/costing/token_usage):

  bmad stage  each BMad artifact under _bmad-output/ (brief, spec/PRD, architecture spine, UX,
              epics, test design, sprint status): first and last commit that touched it (git),
              else the file's modification date
  agent       each custom agent's sanctum under _bmad/memory/ (Jules, Scrooge, ...): the birth date
              in its PERSONA.md, else its first session log
  epic/story  from _bmad-output/implementation-artifacts/sprint-status.yaml and its git history:
              started = the first commit where it (or, for an epic, any of its stories) left backlog,
              completed = the first commit where it reached done (an epic without its own done
              line completes with its last story)
  claude      each work item in the token ledger (claude_usage.csv: planning phases, custom agents,
              stories): first and last Claude call
  project     start = the earliest date above; end = the last epic's completion once every epic is
              done, else still running (end = today)

Dates are days (YYYY-MM-DD). Working days count Monday to Friday. A date that comes from a file's
modification time rather than git is marked "file date": copying or checking out files resets it.

Usage:
    uv run project_timeline.py <project-root> [--ledger <claude_usage.csv>] [-o <csv>]

Prints JSON: the project summary and every row. Exit codes: 0 ok, 2 error.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import tomllib
from datetime import date, datetime, timedelta
from pathlib import Path

ARTIFACTS = [  # (stage, glob under _bmad-output), first match per stage per file
    ("product brief", "**/*brief*.md"),
    ("spec / PRD", "specs/**/*.md"),
    ("spec / PRD", "**/*prd*.md"),
    ("architecture", "planning-artifacts/architecture/*/ARCHITECTURE-SPINE.md"),
    ("UX design", "**/*ux*.md"),
    ("epics & stories", "**/*epic*.md"),
    ("test design", "test-artifacts/**/*.md"),
    ("test design", "**/*test-design*.md"),
    ("sprint status", "**/sprint-status.yaml"),
]
DONE, BACKLOG = {"done", "completed", "complete"}, {"backlog", "drafted", ""}


def org_folder(root: Path, key: str, default: str) -> Path:
    value = default
    for rel in ("_bmad/config.toml", "_bmad/custom/config.toml", "_bmad/custom/config.user.toml"):
        path = root / rel
        if path.is_file():
            try:
                found = tomllib.loads(path.read_text(encoding="utf-8")).get("modules", {}).get("org", {}).get(key)
            except tomllib.TOMLDecodeError:
                continue
            if isinstance(found, str) and found.strip():
                value = found.strip()
    value = value.replace("{project-root}", str(root))
    path = Path(value)
    return path if path.is_absolute() else root / path


def git(root: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout if out.returncode == 0 else ""


def file_dates(root: Path, path: Path) -> tuple[str, str, str]:
    """(first, last, source) for one file: git commits, else its modification date."""
    rel = path.relative_to(root).as_posix()
    stamps = [line[:10] for line in git(root, "log", "--follow", "--format=%cI", "--", rel).split() if line]
    if stamps:
        return min(stamps), max(stamps), "git"
    day = datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
    return day, day, "file date"


def stage_rows(root: Path) -> list[dict]:
    out_dir = root / "_bmad-output"
    rows, seen = [], set()
    for stage, pattern in ARTIFACTS:
        for path in sorted(out_dir.glob(pattern)) if out_dir.is_dir() else []:
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            first, last, source = file_dates(root, path)
            rows.append({"category": "bmad stage", "name": stage, "detail": path.relative_to(root).as_posix(),
                         "status": "", "started": first, "completed": last, "source": source})
    return rows


def agent_rows(root: Path) -> list[dict]:
    rows = []
    memory = root / "_bmad" / "memory"
    for sanctum in sorted(p for p in memory.glob("*") if p.is_dir()) if memory.is_dir() else []:
        persona = sanctum / "PERSONA.md"
        born = ""
        if persona.is_file():
            m = re.search(r"\*\*Born:\*\*\s*(\d{4}-\d{2}-\d{2})", persona.read_text(encoding="utf-8"))
            born = m.group(1) if m else ""
        sessions = sorted(p.stem for p in (sanctum / "sessions").glob("*.md")) if (sanctum / "sessions").is_dir() else []
        if not born and sessions:
            born = sessions[0][:10]
        if born or sessions:
            rows.append({"category": "agent", "name": sanctum.name, "detail": f"{len(sessions)} session logs",
                         "status": "", "started": born, "completed": sessions[-1][:10] if sessions else born,
                         "source": "sanctum"})
    return rows


def parse_status(text: str) -> dict[str, str]:
    """The development_status mapping of a BMad sprint-status.yaml (flat key: value lines)."""
    status, inside = {}, False
    for line in text.splitlines():
        if re.match(r"^development_status:\s*$", line):
            inside = True
            continue
        if inside:
            if line and not line.startswith((" ", "\t", "#")):
                break
            m = re.match(r"^\s+([A-Za-z0-9._-]+):\s*([A-Za-z-]*)", line)
            if m:
                status[m.group(1)] = m.group(2).lower()
    return status


def status_history(root: Path, path: Path) -> list[tuple[str, dict[str, str]]]:
    """(commit day, status mapping) for each commit of the sprint status, oldest first."""
    rel = path.relative_to(root).as_posix()
    history = []
    for line in git(root, "log", "--reverse", "--format=%H %cI", "--", rel).splitlines():
        sha, stamp = line.split(" ", 1)
        history.append((stamp[:10], parse_status(git(root, "show", f"{sha}:{rel}"))))
    return history


def epic_rows(root: Path) -> list[dict]:
    files = sorted((root / "_bmad-output").glob("**/sprint-status.yaml")) if (root / "_bmad-output").is_dir() else []
    if not files:
        return []
    path = files[0]
    current = parse_status(path.read_text(encoding="utf-8"))
    history = status_history(root, path)
    source = "git" if history else "file date"
    if not history:
        history = [(file_dates(root, path)[1], current)]
    started: dict[str, str] = {}
    done: dict[str, str] = {}
    for day, mapping in history:
        for key, value in mapping.items():
            if value not in BACKLOG:
                started.setdefault(key, day)
            if value in DONE:
                done.setdefault(key, day)
            elif key in done and value not in DONE:
                done.pop(key)  # reopened
    epics = {k[5:]: k for k in current if re.fullmatch(r"epic-\d+", k)}
    stories: dict[str, list[str]] = {}
    for key in current:
        m = re.match(r"^(\d+)-\d+", key)
        if m and not key.startswith("epic-"):
            stories.setdefault(m.group(1), []).append(key)
    rows = []
    for number in sorted(set(epics) | set(stories), key=int):
        keys = stories.get(number, [])
        epic_key = epics.get(number, f"epic-{number}")
        story_starts = [started[k] for k in keys if k in started]
        story_done = [done[k] for k in keys if k in done]
        all_done = (current.get(epic_key) in DONE) or (keys and all(current.get(k) in DONE for k in keys))
        epic_start = min([d for d in (started.get(epic_key), *story_starts) if d], default="")
        epic_end = done.get(epic_key) or (max(story_done) if all_done and story_done else "")
        rows.append({"category": "epic", "name": epic_key, "detail": f"{len(keys)} stories",
                     "status": "done" if all_done else current.get(epic_key, "in-progress"),
                     "started": epic_start, "completed": epic_end if all_done else "", "source": source})
        for key in keys:
            rows.append({"category": "story", "name": key, "detail": epic_key, "status": current.get(key, ""),
                         "started": started.get(key, ""), "completed": done.get(key, "") if current.get(key) in DONE else "",
                         "source": source})
    return rows


def claude_rows(ledger: Path) -> list[dict]:
    if not ledger.is_file():
        return []
    items: dict[tuple[str, str], list[str]] = {}
    with ledger.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("first_call"):
                span = items.setdefault((r["kind"], r["work_item"]), [r["first_call"], r["last_call"]])
                span[0], span[1] = min(span[0], r["first_call"]), max(span[1], r["last_call"])
    return [{"category": "claude", "name": item, "detail": kind, "status": "", "started": first[:10],
             "completed": last[:10], "source": "token ledger"} for (kind, item), (first, last) in sorted(items.items())]


def working_days(start: date, end: date) -> int:
    days = (end - start).days + 1
    return sum(1 for i in range(days) if (start + timedelta(days=i)).weekday() < 5)


def summarise(rows: list[dict]) -> dict:
    starts = [r["started"] for r in rows if r["started"]]
    epics = [r for r in rows if r["category"] == "epic"]
    finished = bool(epics) and all(r["status"] == "done" and r["completed"] for r in epics)
    if not starts:
        return {"started": "", "completed": "", "status": "no dates found", "calendar_days": 0, "working_days": 0}
    start = min(starts)
    end = max(r["completed"] for r in epics) if finished else date.today().isoformat()
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    return {"started": start, "completed": end if finished else "", "status": "complete" if finished else "in progress",
            "calendar_days": (e - s).days + 1, "working_days": working_days(s, e),
            "epics_done": sum(r["status"] == "done" for r in epics), "epics": len(epics),
            "file_dates": sum(r["source"] == "file date" for r in rows)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("project_root")
    p.add_argument("--ledger", type=Path, help="claude_usage.csv (default: in the token usage folder)")
    p.add_argument("-o", "--output", type=Path, help="CSV to write (default: <token usage folder>/project_timeline.csv)")
    args = p.parse_args(sys.argv[1:] if argv is None else argv)
    root = Path(args.project_root).resolve()
    folder = org_folder(root, "token_usage_folder", "docs/costing/token_usage")
    try:
        rows = stage_rows(root) + agent_rows(root) + epic_rows(root) + claude_rows(args.ledger or folder / "claude_usage.csv")
    except (OSError, ValueError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return 2
    summary = summarise(rows)
    out = args.output or folder / "project_timeline.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = ["category", "name", "detail", "status", "started", "completed", "days", "source"]
    project = {"category": "project", "name": root.name, "detail": f"{summary.get('working_days', 0)} working days",
               "status": summary["status"], "started": summary["started"], "completed": summary["completed"],
               "source": "summary"}
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in [project, *rows]:
            span = ""
            if r["started"] and r["completed"]:
                span = (date.fromisoformat(r["completed"]) - date.fromisoformat(r["started"])).days + 1
            writer.writerow({**r, "days": span})
    print(json.dumps({"status": "ok", "file": str(out), "project": summary, "rows": rows}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
