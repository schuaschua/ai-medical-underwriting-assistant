#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for docx-comments.py (stdlib unittest).

Run from the skill root: uv run scripts/tests/test_docx-comments.py
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


review_status = load("review-status")
docx_comments = load("docx-comments")

REVIEW = """# TechnicalArchitectureAssessment — governance review

Legend: change `status:` yourself to approve.

## Q-1
- source: question 1 "What cloud platform?" (TechnicalArchitectureAssessment.docx)
- owner: Tech Lead
- status: approved

Microsoft Azure, Southeast Asia.
Note: I have derived this answer from ARCHITECTURE-SPINE.md Stack because the spine fixes the platform.

## Q-2
- source: question 2 "Stack?" (TechnicalArchitectureAssessment.docx)
- owner: Tech lead
- status: done

Container Apps.

## C-abcd1234
- source: comment by Reviewer on "Southeast Asia"
- owner: Tech Lead
- status: applied
- posted: C-99990000

Yes, single region.
Note: I have derived this answer from ARCHITECTURE-SPINE.md because it states the region.
"""

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
W14 = 'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"'
W15 = 'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"'

DOCUMENT = f"""<?xml version="1.0"?><w:document {W}><w:body>
<w:p><w:r><w:t>Region: </w:t></w:r><w:commentRangeStart w:id="0"/><w:r><w:t>Southeast Asia</w:t></w:r><w:commentRangeEnd w:id="0"/></w:p>
</w:body></w:document>"""

COMMENTS = f"""<?xml version="1.0"?><w:comments {W} {W14}>
<w:comment w:id="0" w:author="Reviewer" w:date="2026-09-26T10:00:00Z"><w:p w14:paraId="AAAA0001"><w:r><w:t>Is this single region?</w:t></w:r></w:p></w:comment>
<w:comment w:id="1" w:author="Owner" w:date="2026-09-26T11:00:00Z"><w:p w14:paraId="AAAA0002"><w:r><w:t>Yes.</w:t></w:r></w:p></w:comment>
</w:comments>"""

EXTENDED = f"""<?xml version="1.0"?><w15:commentsEx {W15}>
<w15:commentEx w15:paraId="AAAA0001" w15:done="1"/>
<w15:commentEx w15:paraId="AAAA0002" w15:paraIdParent="AAAA0001" w15:done="0"/>
</w15:commentsEx>"""


def make_docx(path: Path, with_comments: bool = True) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", DOCUMENT)
        if with_comments:
            z.writestr("word/comments.xml", COMMENTS)
            z.writestr("word/commentsExtended.xml", EXTENDED)


class DocxCommentsTests(unittest.TestCase):
    def test_lists_comments_with_anchor_thread_and_resolved(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            docx = Path(tmp) / "a.docx"
            make_docx(docx)
            comments = docx_comments.list_comments(docx)
        self.assertEqual(len(comments), 2)
        first, reply = comments
        self.assertEqual(first["anchor"], "Southeast Asia")
        self.assertTrue(first["resolved"])
        self.assertEqual(reply["reply_to"], first["key"])
        self.assertEqual(len(first["key"]), 8)

    def test_no_comments_part_returns_empty(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            docx = Path(tmp) / "b.docx"
            make_docx(docx, with_comments=False)
            self.assertEqual(docx_comments.list_comments(docx), [])

    def test_cli_marks_in_review(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            docx = Path(tmp) / "a.docx"
            make_docx(docx)
            key = docx_comments.list_comments(docx)[1]["key"]
            review = Path(tmp) / "a.review.md"
            review.write_text(f"## C-{key}\n", encoding="utf-8")
            run = subprocess.run([sys.executable, str(SCRIPTS / "docx-comments.py"), str(docx), "--review", str(review)],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            data = json.loads(run.stdout)
            self.assertEqual([c["in_review"] for c in data["comments"]], [False, True])
            self.assertEqual(data["new"], 0)  # the only unlisted comment is resolved

    def test_cli_bad_file(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            bad = Path(tmp) / "bad.docx"
            bad.write_text("not a zip", encoding="utf-8")
            run = subprocess.run([sys.executable, str(SCRIPTS / "docx-comments.py"), str(bad)],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)


if __name__ == "__main__":
    unittest.main()
