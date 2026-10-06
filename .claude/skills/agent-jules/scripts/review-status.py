#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Parse a governance review file (<DocName>.review.md) into JSON and flag broken items.

A review file holds one item per questionnaire question or reviewer comment:

    ## Q-12
    - source: question 16 "What is the RTO ...?" (TechnicalArchitectureAssessment.docx)
    - owner: Product Lead
    - status: pending
    - posted: C-1a2b3c4d          (optional, set after a reply is applied)

    <draft text, one or more lines>
    Note: I have derived this answer from ... because ....

Everything before the first "## " heading is the legend and is ignored.

Output (stdout, JSON): the file, every item (id, source, owner, status, posted,
draft, has_note), counts by status and by owner, and a list of problems:
unknown status, missing or unknown owner, missing source, empty draft, draft not
ending with the reasoning note, duplicate id.

Exit codes: 0 = no problems, 1 = problems found, 2 = error (file missing).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

STATUSES = {"pending", "approved", "changed", "rejected", "applied"}
OWNERS = {"Product Lead", "Tech Lead"}
NOTE_PREFIX = "Note: I have derived this answer from"
FIELD_RE = re.compile(r"^- (source|owner|status|posted):\s*(.*)$")
HEADING_RE = re.compile(r"^## (\S+)\s*$")


def parse(text: str) -> list[dict]:
    items: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        heading = HEADING_RE.match(line)
        if heading:
            current = {"id": heading.group(1), "source": "", "owner": "", "status": "",
                       "posted": "", "draft_lines": []}
            items.append(current)
            continue
        if current is None:
            continue
        field = FIELD_RE.match(line)
        if field and not current["draft_lines"]:
            current[field.group(1)] = field.group(2).strip()
        else:
            current["draft_lines"].append(line)
    for item in items:
        draft = "\n".join(item.pop("draft_lines")).strip()
        item["draft"] = draft
        last = draft.splitlines()[-1].strip() if draft else ""
        item["has_note"] = last.startswith(NOTE_PREFIX)
    return items


def problems_for(items: list[dict]) -> list[dict]:
    problems = []
    seen = Counter(item["id"] for item in items)
    for item in items:
        def flag(msg: str) -> None:
            problems.append({"id": item["id"], "problem": msg})
        if seen[item["id"]] > 1:
            flag("duplicate id")
        if item["status"].lower() not in STATUSES:
            flag(f"unknown status '{item['status']}' (expected one of {sorted(STATUSES)})")
        if item["owner"] not in OWNERS:
            flag(f"missing or unknown owner '{item['owner']}' (expected Product Lead or Tech Lead)")
        if not item["source"]:
            flag("missing source")
        if not item["draft"]:
            flag("empty draft")
        elif not item["has_note"]:
            flag("draft does not end with the reasoning note line")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("review_file", help="path to <DocName>.review.md")
    parser.add_argument("-o", "--output", help="write JSON here instead of stdout")
    parser.add_argument("--verbose", action="store_true", help="progress to stderr")
    args = parser.parse_args()

    path = Path(args.review_file)
    if not path.is_file():
        print(f"error: {path} not found", file=sys.stderr)
        return 2
    items = parse(path.read_text(encoding="utf-8"))
    problems = problems_for(items)
    result = {
        "file": str(path),
        "items": items,
        "counts": {
            "by_status": dict(Counter(i["status"].lower() for i in items)),
            "by_owner": dict(Counter(i["owner"] for i in items)),
        },
        "problems": problems,
    }
    if args.verbose:
        print(f"{len(items)} items, {len(problems)} problems", file=sys.stderr)
    out = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(out + "\n", encoding="utf-8")
    else:
        print(out)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
