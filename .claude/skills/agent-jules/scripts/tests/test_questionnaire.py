#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for questionnaire.py (stdlib unittest).

Builds a synthetic, made-up questionnaire .docx as a minimal zip (no
python-docx, no real governance content) so extract/write are exercised
without touching the real governance documents.

Run from the skill root: uv run scripts/tests/test_questionnaire.py
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


questionnaire = load("questionnaire")

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

DOCUMENT = f"""<?xml version="1.0"?><w:document {W}><w:body>
<w:tbl>
<w:tr><w:tc><w:p><w:r><w:t>No</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Question</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Answer</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>What made-up widget powers the gizmo?</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:rPr><w:b/></w:rPr><w:t>The sprocket widget.</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:p><w:r><w:t>2</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Who owns the gizmo backlog?</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>OPEN — Test Lead to provide.</w:t></w:r></w:p></w:tc></w:tr>
</w:tbl>
</w:body></w:document>"""


def make_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", DOCUMENT)
        z.writestr("[Content_Types].xml", "<Types/>")


class ExtractTests(unittest.TestCase):
    def test_extracts_rows_with_id_question_answer_and_hash(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            docx = Path(tmp) / "a.docx"
            make_docx(docx)
            rows = questionnaire.extract_rows(docx)
        self.assertEqual(len(rows), 2)
        first, second = rows
        self.assertEqual(first["id"], "1")
        self.assertEqual(first["question"], "What made-up widget powers the gizmo?")
        self.assertEqual(first["answer"], "The sprocket widget.")
        self.assertEqual(len(first["hash"]), 8)
        self.assertEqual(second["answer"], "OPEN — Test Lead to provide.")

    def test_hash_depends_only_on_question_text(self):
        self.assertEqual(
            questionnaire.question_hash("Same question?"),
            questionnaire.question_hash("Same question?"),
        )
        self.assertNotEqual(
            questionnaire.question_hash("Question A?"),
            questionnaire.question_hash("Question B?"),
        )

    def test_missing_document_part_errors(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            docx = Path(tmp) / "empty.docx"
            with zipfile.ZipFile(docx, "w") as z:
                z.writestr("[Content_Types].xml", "<Types/>")
            with self.assertRaises(questionnaire.QuestionnaireError):
                questionnaire.extract_rows(docx)

    def test_cli_bad_file(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            bad = Path(tmp) / "bad.docx"
            bad.write_text("not a zip", encoding="utf-8")
            run = subprocess.run(
                [sys.executable, str(SCRIPTS / "questionnaire.py"), "extract", str(bad)],
                capture_output=True, text=True,
            )
            self.assertEqual(run.returncode, 2)


COVER = """<w:tbl>
<w:tr><w:tc><w:p><w:r><w:t>Document</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>ACME governance form</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:p><w:r><w:t>Version</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>0.1</w:t></w:r></w:p></w:tc></w:tr>
</w:tbl>
"""


class CustomTemplateTests(unittest.TestCase):
    def test_skips_a_cover_table_to_the_question_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AcmeForm.docx"
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("word/document.xml", DOCUMENT.replace("<w:body>\n", "<w:body>\n" + COVER, 1))
            rows = questionnaire.extract_rows(path)
            self.assertEqual([r["id"] for r in rows], ["1", "2"])
            self.assertEqual(rows[0]["question"], "What made-up widget powers the gizmo?")
            answers = Path(tmp) / "answers.json"
            answers.write_text(json.dumps({"2": "The Product Lead."}), encoding="utf-8")
            out = subprocess.run([sys.executable, str(SCRIPTS / "questionnaire.py"), "write", str(path), str(answers)],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
            self.assertIn("ACME governance form", xml)
            self.assertIn("The Product Lead.", xml)
            self.assertEqual(questionnaire.extract_rows(path)[1]["answer"], "The Product Lead.")


class WriteTests(unittest.TestCase):
    def test_write_updates_only_targeted_row_and_keeps_hash_stable(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            docx = Path(tmp) / "a.docx"
            make_docx(docx)
            before = questionnaire.extract_rows(docx)

            answers = Path(tmp) / "answers.json"
            new_text = "A revised sprocket answer.\nSecond line.\nNote: I have derived this answer from TEST because test."
            answers.write_text(json.dumps({"1": new_text}), encoding="utf-8")

            run = subprocess.run(
                [sys.executable, str(SCRIPTS / "questionnaire.py"), "write", str(docx), str(answers)],
                capture_output=True, text=True,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            result = json.loads(run.stdout)
            self.assertEqual(result["written"], ["1"])
            self.assertEqual(result["not_found"], [])

            after = questionnaire.extract_rows(docx)
        self.assertEqual(after[0]["answer"], new_text)
        self.assertEqual(after[0]["hash"], before[0]["hash"])  # question untouched
        self.assertEqual(after[1]["answer"], before[1]["answer"])  # other row untouched

    def test_write_reports_unmatched_ids(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            docx = Path(tmp) / "a.docx"
            make_docx(docx)
            answers = Path(tmp) / "answers.json"
            answers.write_text(json.dumps({"99": "no such row"}), encoding="utf-8")
            run = subprocess.run(
                [sys.executable, str(SCRIPTS / "questionnaire.py"), "write", str(docx), str(answers)],
                capture_output=True, text=True,
            )
            self.assertEqual(run.returncode, 1)
            result = json.loads(run.stdout)
            self.assertEqual(result["not_found"], ["99"])

    def test_write_to_separate_output_leaves_original_untouched(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            docx = Path(tmp) / "a.docx"
            make_docx(docx)
            original_bytes = docx.read_bytes()
            out = Path(tmp) / "out.docx"
            answers = Path(tmp) / "answers.json"
            answers.write_text(json.dumps({"1": "Changed."}), encoding="utf-8")

            run = subprocess.run(
                [sys.executable, str(SCRIPTS / "questionnaire.py"), "write", str(docx), str(answers),
                 "-o", str(out)],
                capture_output=True, text=True,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(docx.read_bytes(), original_bytes)
            self.assertTrue(out.is_file())
            self.assertEqual(questionnaire.extract_rows(out)[0]["answer"], "Changed.")

    def test_resulting_docx_remains_a_valid_zip(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            docx = Path(tmp) / "a.docx"
            make_docx(docx)
            answers = Path(tmp) / "answers.json"
            answers.write_text(json.dumps({"1": "Changed."}), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(SCRIPTS / "questionnaire.py"), "write", str(docx), str(answers)],
                capture_output=True, text=True, check=True,
            )
            with zipfile.ZipFile(docx) as z:
                self.assertIsNone(z.testzip())
                self.assertIn("[Content_Types].xml", z.namelist())


if __name__ == "__main__":
    unittest.main()
