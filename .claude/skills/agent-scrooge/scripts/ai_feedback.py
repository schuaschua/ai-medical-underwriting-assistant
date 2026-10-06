#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""The team's rating of the AI at the end of every epic: 1 to 5, and why when it was poor.

At the end of each epic the team is asked two questions:

  1. How would you rate the AI on this epic, from 1 (very poor) to 5 (excellent)?
  2. Only when the rating is poor (1 or 2): what went wrong? One or more of
       coding     AI coding standard not up to quality
       testing    AI testing standards not up to quality
       decisions  AI decision-making was flawed
       grounding  AI answers were not grounded in company data
       security   Poor AI security / compliance suggestions
       other      Other (please specify)

Answers are kept in Scrooge's sanctum, <project-root>/_bmad/memory/agent-scrooge/ai-feedback.csv (committed
with the project, one row per epic; recording an epic again replaces its row). The close-out adds them to
the estimate workbook as the AI Feedback sheet (tblAIFeedback).

Finished epics come from the sprint status (_bmad-output/**/sprint-status.yaml): an epic is done when its
epic-N line is done, or when every one of its stories is.

Usage:
    uv run ai_feedback.py <project-root> pending
    uv run ai_feedback.py <project-root> record --epic 2 --rating 2 --category testing --category other \\
        --other "ignored the API contract" [--comment "..."]
    uv run ai_feedback.py <project-root> record --epic 2 --skip        (the team declined to rate it)
    uv run ai_feedback.py <project-root> list

Prints JSON. Exit codes: 0 ok, 1 invalid answer, 2 error.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

DATA_FILE = Path("_bmad/memory/agent-scrooge/ai-feedback.csv")
FIELDS = ["epic", "rating", "poor", "categories", "other", "comment", "status", "recorded"]
POOR = 2  # ratings at or below this ask the second question
CATEGORIES = {
    "coding": "AI coding standard not up to quality",
    "testing": "AI testing standards not up to quality",
    "decisions": "AI decision-making was flawed",
    "grounding": "AI answers were not grounded in company data",
    "security": "Poor AI security / compliance suggestions",
    "other": "Other",
}
DONE = {"done", "completed", "complete"}


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


def epics(root: Path) -> dict[str, bool]:
    """epic-N -> done, from the sprint status, in epic order."""
    out_dir = root / "_bmad-output"
    files = sorted(out_dir.glob("**/sprint-status.yaml")) if out_dir.is_dir() else []
    if not files:
        return {}
    current = parse_status(files[0].read_text(encoding="utf-8"))
    numbers = {k[5:] for k in current if re.fullmatch(r"epic-\d+", k)}
    stories: dict[str, list[str]] = {}
    for key in current:
        m = re.match(r"^(\d+)-\d+", key)
        if m:
            stories.setdefault(m.group(1), []).append(key)
    result = {}
    for n in sorted(numbers | set(stories), key=int):
        keys = stories.get(n, [])
        result[f"epic-{n}"] = current.get(f"epic-{n}") in DONE or bool(keys) and all(current.get(k) in DONE for k in keys)
    return result


def load(root: Path) -> dict[str, dict]:
    path = root / DATA_FILE
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {r["epic"]: r for r in csv.DictReader(fh)}


def save(root: Path, rows: dict[str, dict]) -> Path:
    path = root / DATA_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    order = sorted(rows, key=lambda e: int(re.sub(r"\D", "", e) or 0))
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for epic in order:
            writer.writerow({f: rows[epic].get(f, "") for f in FIELDS})
    return path


def epic_key(value: str) -> str:
    m = re.fullmatch(r"(?:epic-?)?(\d+)", value.strip().lower())
    if not m:
        raise ValueError(f"--epic: expected an epic number such as 2 or epic-2, got {value!r}")
    return f"epic-{int(m.group(1))}"


def build_row(args) -> tuple[dict, list[str]]:
    """The row to record, or the reasons the answer can't be recorded."""
    errors: list[str] = []
    row = {"epic": epic_key(args.epic), "recorded": date.today().isoformat(), "comment": (args.comment or "").strip()}
    if args.skip:
        if args.rating is not None or args.category:
            errors.append("--skip records that the team declined to rate the epic; give no rating or category with it")
        return {**row, "status": "skipped"}, errors
    if args.rating not in range(1, 6):
        errors.append("--rating: give a whole number from 1 (very poor) to 5 (excellent)")
        return row, errors
    cats = list(dict.fromkeys(c.strip().lower() for c in args.category))
    other = (args.other or "").strip()
    poor = args.rating <= POOR
    for c in cats:
        if c not in CATEGORIES:
            errors.append(f"--category {c!r}: use one of {', '.join(CATEGORIES)}")
    if poor and not cats:
        errors.append(f"a rating of {args.rating} is poor: ask what went wrong and give at least one --category")
    if not poor and cats:
        errors.append(f"categories are asked only for a poor rating (1 or 2), not {args.rating}")
    if "other" in cats and not other:
        errors.append("--category other: give what it was with --other")
    if other and "other" not in cats:
        errors.append("--other goes with --category other")
    return {**row, "rating": args.rating, "poor": "yes" if poor else "no", "status": "rated",
            "categories": "; ".join(CATEGORIES.get(c, c) for c in cats), "other": other}, errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("project_root")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("pending", help="finished epics the team hasn't rated yet")
    sub.add_parser("list", help="every recorded rating")
    rec = sub.add_parser("record", help="record the rating for one epic")
    rec.add_argument("--epic", required=True)
    rec.add_argument("--rating", type=int)
    rec.add_argument("--category", action="append", default=[], help=", ".join(CATEGORIES))
    rec.add_argument("--other", help="what 'other' was")
    rec.add_argument("--comment", help="anything else the team said")
    rec.add_argument("--skip", action="store_true", help="the team declined to rate this epic")
    args = p.parse_args(sys.argv[1:] if argv is None else argv)
    root = Path(args.project_root).resolve()
    try:
        rows = load(root)
        if args.command == "pending":
            status = epics(root)
            pending = [e for e, done in status.items() if done and e not in rows]
            print(json.dumps({"status": "ok", "pending": pending, "epics_done": sum(status.values()),
                              "epics": len(status), "recorded": len(rows),
                              "questions": {"rating": "How would you rate the AI on this epic, from 1 (very poor) to 5 (excellent)?",
                                            "poor_at_or_below": POOR, "categories": CATEGORIES}}, indent=1))
            return 0
        if args.command == "list":
            print(json.dumps({"status": "ok", "file": str(root / DATA_FILE), "feedback": list(rows.values())}, indent=1))
            return 0
        row, errors = build_row(args)
        if errors:
            print(json.dumps({"status": "invalid", "errors": errors}, indent=1))
            return 1
        replaced = row["epic"] in rows
        rows[row["epic"]] = row
        path = save(root, rows)
    except (OSError, ValueError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return 2
    print(json.dumps({"status": "ok", "file": str(path), "replaced": replaced, "row": row}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
