#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""List the reviewer comments in a .docx as JSON, using only the standard library.

For each comment: a stable `key` (8 hex chars from author + date + text, because
Word may renumber comment ids on save), the Word id, author, date, text, the
document text the comment is anchored to, the key of the comment it replies to
(threads), whether it is resolved, and, with --review, whether that review file
already mentions `C-<key>` (as an item id or on a `- posted:` line).

Reads word/comments.xml, word/commentsExtended.xml (threads and resolved flags,
when present) and word/document.xml. A document with no comments returns an
empty list. No Word tooling is needed, so it works on a headless pulse.

Exit codes: 0 = listed, 2 = error (missing or unreadable file).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
W15 = "http://schemas.microsoft.com/office/word/2012/wordml"


def q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def text_of(element: ET.Element) -> str:
    paragraphs = []
    for p in element.iter(q(W, "p")):
        paragraphs.append("".join(t.text or "" for t in p.iter(q(W, "t"))))
    return "\n".join(paragraphs).strip()


def comment_key(author: str, date: str, text: str) -> str:
    return hashlib.sha1(f"{author}|{date}|{text}".encode("utf-8")).hexdigest()[:8]


def anchors(document_xml: bytes) -> dict[str, str]:
    """Map comment id -> text between its commentRangeStart and commentRangeEnd."""
    root = ET.fromstring(document_xml)
    open_ids: list[str] = []
    collected: dict[str, list[str]] = {}
    for el in root.iter():
        if el.tag == q(W, "commentRangeStart"):
            cid = el.get(q(W, "id"))
            open_ids.append(cid)
            collected.setdefault(cid, [])
        elif el.tag == q(W, "commentRangeEnd"):
            cid = el.get(q(W, "id"))
            if cid in open_ids:
                open_ids.remove(cid)
        elif el.tag == q(W, "t") and open_ids:
            for cid in open_ids:
                collected[cid].append(el.text or "")
    return {cid: re.sub(r"\s+", " ", "".join(parts)).strip() for cid, parts in collected.items()}


def list_comments(docx: Path) -> list[dict]:
    with zipfile.ZipFile(docx) as z:
        names = set(z.namelist())
        if "word/comments.xml" not in names:
            return []
        comments_root = ET.fromstring(z.read("word/comments.xml"))
        anchor_map = anchors(z.read("word/document.xml")) if "word/document.xml" in names else {}
        extended = {}
        if "word/commentsExtended.xml" in names:
            for ex in ET.fromstring(z.read("word/commentsExtended.xml")).iter(q(W15, "commentEx")):
                extended[ex.get(q(W15, "paraId"))] = {
                    "parent_para": ex.get(q(W15, "paraIdParent")),
                    "done": ex.get(q(W15, "done")) == "1",
                }

    comments = []
    para_to_key: dict[str, str] = {}
    for c in comments_root.iter(q(W, "comment")):
        author = c.get(q(W, "author"), "")
        date = c.get(q(W, "date"), "")
        text = text_of(c)
        key = comment_key(author, date, text)
        paras = list(c.iter(q(W, "p")))
        para_id = paras[-1].get(q(W14, "paraId")) if paras else None
        if para_id:
            para_to_key[para_id] = key
        ext = extended.get(para_id, {})
        comments.append({
            "key": key,
            "word_id": c.get(q(W, "id")),
            "author": author,
            "date": date,
            "text": text,
            "anchor": anchor_map.get(c.get(q(W, "id")), ""),
            "_parent_para": ext.get("parent_para"),
            "resolved": ext.get("done", False),
        })
    for comment in comments:
        parent_para = comment.pop("_parent_para")
        comment["reply_to"] = para_to_key.get(parent_para) if parent_para else None
    return comments


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("docx", help="path to the questionnaire .docx")
    parser.add_argument("--review", help="review file to check for existing C-<key> items")
    parser.add_argument("-o", "--output", help="write JSON here instead of stdout")
    parser.add_argument("--verbose", action="store_true", help="progress to stderr")
    args = parser.parse_args()

    docx = Path(args.docx)
    try:
        comments = list_comments(docx)
    except (FileNotFoundError, zipfile.BadZipFile, ET.ParseError, KeyError) as exc:
        print(f"error: cannot read {docx}: {exc}", file=sys.stderr)
        return 2

    if args.review:
        review = Path(args.review)
        known = review.read_text(encoding="utf-8") if review.is_file() else ""
        for comment in comments:
            comment["in_review"] = f"C-{comment['key']}" in known

    result = {
        "file": str(docx),
        "count": len(comments),
        "new": sum(1 for c in comments if args.review and not c["in_review"] and not c["resolved"]),
        "comments": comments,
    }
    if args.verbose:
        print(f"{len(comments)} comments in {docx}", file=sys.stderr)
    out = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(out + "\n", encoding="utf-8")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
