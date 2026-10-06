#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for review-status.py (stdlib unittest).

Run from the skill root: uv run scripts/tests/test_review-status.py
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


class ReviewStatusTests(unittest.TestCase):
    def test_parses_items_and_fields(self):
        items = review_status.parse(REVIEW)
        self.assertEqual([i["id"] for i in items], ["Q-1", "Q-2", "C-abcd1234"])
        self.assertEqual(items[0]["status"], "approved")
        self.assertTrue(items[0]["has_note"])
        self.assertEqual(items[2]["posted"], "C-99990000")

    def test_flags_bad_status_owner_and_missing_note(self):
        problems = review_status.problems_for(review_status.parse(REVIEW))
        q2 = [p["problem"] for p in problems if p["id"] == "Q-2"]
        self.assertEqual(len(q2), 3)
        self.assertFalse([p for p in problems if p["id"] in ("Q-1", "C-abcd1234")])

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            good = Path(tmp) / "good.review.md"
            good.write_text(REVIEW.split("## Q-2")[0], encoding="utf-8")
            run = subprocess.run([sys.executable, str(SCRIPTS / "review-status.py"), str(good)],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)["counts"]["by_status"], {"approved": 1})
            missing = subprocess.run([sys.executable, str(SCRIPTS / "review-status.py"), str(Path(tmp) / "x.md")],
                                     capture_output=True, text=True)
            self.assertEqual(missing.returncode, 2)


if __name__ == "__main__":
    unittest.main()
