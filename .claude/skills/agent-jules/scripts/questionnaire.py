#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Read and write a governance questionnaire's answer table without opening it.

A questionnaire .docx (TechnicalArchitectureAssessment, DataGovernancePrivacyAssessment,
EnterpriseAIRiskAssessment, ...) holds one
table with a header row (No | Question | Answer, in any order) and one data
row per question. Both subcommands work directly on word/document.xml via
zipfile + the standard library's XML parser — no python-docx needed.

    extract <docx>
        Print one JSON entry per question row: a stable id (the "No" column,
        or the row's position when that column is blank), the question text,
        the current answer text, and an 8-hex content hash of the question
        text. Compare a row's hash against the hash recorded the last time it
        was answered (kept in the answer library) to tell whether the
        question itself changed since then.

    write <docx> <answers.json>
        answers.json maps id -> new answer text (a reasoning note included).
        Replaces the matching answer cells in place, keeping each cell's
        existing formatting and paragraph structure. Unmatched ids are
        reported, not treated as errors, so a partial batch is safe.
        Use only when safe-docx tracked changes are unavailable, per
        references/answer-questionnaire.md.

Exit codes: 0 = done (write: all ids matched), 1 = write had unmatched ids,
2 = error (file missing, unreadable, or no table found).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
DOCUMENT_PART = "word/document.xml"

ID_HEADERS = {"no", "no.", "#", "id", "q", "question no", "question no."}
QUESTION_HEADERS_HINT = "question"
ANSWER_HEADERS_HINT = "answer"


def q(tag: str, ns: str = W) -> str:
    return f"{{{ns}}}{tag}"


class QuestionnaireError(Exception):
    """A questionnaire .docx could not be read as expected."""


def _read_document_xml(docx: Path) -> bytes:
    try:
        with zipfile.ZipFile(docx) as z:
            names = set(z.namelist())
            if DOCUMENT_PART not in names:
                raise QuestionnaireError(f"{docx} has no {DOCUMENT_PART}")
            return z.read(DOCUMENT_PART)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise QuestionnaireError(f"cannot read {docx}: {exc}") from exc


def find_table(root: ET.Element) -> ET.Element:
    """The question table: the first whose header row has a Question column (an org's own template may
    open with a cover or document-control table), else the first table."""
    tables = list(root.iter(q("tbl")))
    if not tables:
        raise QuestionnaireError("no table found in the document")
    for tbl in tables:
        first = tbl.find(q("tr"))
        if first is not None and any(QUESTION_HEADERS_HINT in cell_text(tc).lower() for tc in first.findall(q("tc"))):
            return tbl
    return tables[0]


def cell_text(tc: ET.Element) -> str:
    lines = ["".join(t.text or "" for t in p.iter(q("t"))) for p in tc.findall(q("p"))]
    return "\n".join(lines).strip()


def column_indices(header_cells: list[str]) -> dict[str, int]:
    idx = {"id": 0, "question": 1, "answer": 2}
    for i, text in enumerate(header_cells):
        low = text.strip().lower()
        if low in ID_HEADERS:
            idx["id"] = i
        elif QUESTION_HEADERS_HINT in low:
            idx["question"] = i
        elif ANSWER_HEADERS_HINT in low:
            idx["answer"] = i
    return idx


def question_hash(question: str) -> str:
    return hashlib.sha1(question.encode("utf-8")).hexdigest()[:8]


def extract_rows(docx: Path) -> list[dict]:
    document_xml = _read_document_xml(docx)
    root = ET.fromstring(document_xml)
    tbl = find_table(root)
    rows = tbl.findall(q("tr"))
    if not rows:
        return []
    header = [cell_text(tc) for tc in rows[0].findall(q("tc"))]
    idx = column_indices(header)

    result = []
    for position, row in enumerate(rows[1:], start=1):
        cells = row.findall(q("tc"))

        def get(key: str) -> str:
            i = idx[key]
            return cell_text(cells[i]) if i < len(cells) else ""

        question = get("question")
        result.append({
            "id": get("id") or str(position),
            "question": question,
            "answer": get("answer"),
            "hash": question_hash(question),
        })
    return result


def _first_run_props(tc: ET.Element) -> ET.Element | None:
    for r in tc.iter(q("r")):
        rpr = r.find(q("rPr"))
        if rpr is not None:
            return rpr
        break
    return None


def set_cell_text(tc: ET.Element, text: str) -> None:
    """Replace a cell's paragraphs with new text, keeping its formatting run props."""
    rpr = _first_run_props(tc)
    for p in list(tc.findall(q("p"))):
        tc.remove(p)
    for line in (text.split("\n") if text else [""]):
        p = ET.SubElement(tc, q("p"))
        r = ET.SubElement(p, q("r"))
        if rpr is not None:
            r.append(copy.deepcopy(rpr))
        t = ET.SubElement(r, q("t"))
        t.set(q("space", XML_NS), "preserve")
        t.text = line


def write_answers(docx: Path, answers: dict[str, str], output: Path | None) -> dict:
    try:
        with zipfile.ZipFile(docx) as zin:
            items = zin.infolist()
            data = {item.filename: zin.read(item.filename) for item in items}
    except (zipfile.BadZipFile, FileNotFoundError) as exc:
        raise QuestionnaireError(f"cannot read {docx}: {exc}") from exc
    if DOCUMENT_PART not in data:
        raise QuestionnaireError(f"{docx} has no {DOCUMENT_PART}")

    root = ET.fromstring(data[DOCUMENT_PART])
    tbl = find_table(root)
    rows = tbl.findall(q("tr"))
    if not rows:
        raise QuestionnaireError("table has no rows")
    header = [cell_text(tc) for tc in rows[0].findall(q("tc"))]
    idx = column_indices(header)

    remaining = dict(answers)
    written = []
    for position, row in enumerate(rows[1:], start=1):
        cells = row.findall(q("tc"))
        if idx["id"] >= len(cells):
            continue
        row_id = cell_text(cells[idx["id"]]) or str(position)
        if row_id in remaining and idx["answer"] < len(cells):
            set_cell_text(cells[idx["answer"]], remaining.pop(row_id))
            written.append(row_id)

    ET.register_namespace("w", W)
    data[DOCUMENT_PART] = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
                            + ET.tostring(root, encoding="utf-8"))

    dest = output or docx
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in items:
            zout.writestr(item, data[item.filename])
    tmp.replace(dest)

    return {"written": written, "not_found": sorted(remaining)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser("extract", help="print question rows as JSON")
    p_extract.add_argument("docx", help="path to the questionnaire .docx")
    p_extract.add_argument("-o", "--output", help="write JSON here instead of stdout")
    p_extract.add_argument("--verbose", action="store_true", help="progress to stderr")

    p_write = sub.add_parser("write", help="write answers into the answer cells")
    p_write.add_argument("docx", help="path to the questionnaire .docx")
    p_write.add_argument("answers", help="path to a JSON file mapping id -> new answer text")
    p_write.add_argument("-o", "--output", help="write the updated .docx here instead of in place")
    p_write.add_argument("--verbose", action="store_true", help="progress to stderr")

    args = parser.parse_args()
    docx = Path(args.docx)

    try:
        if args.command == "extract":
            rows = extract_rows(docx)
            result = {"file": str(docx), "count": len(rows), "rows": rows}
            if args.verbose:
                print(f"{len(rows)} rows in {docx}", file=sys.stderr)
            out = json.dumps(result, indent=2, ensure_ascii=False)
            if args.output:
                Path(args.output).write_text(out + "\n", encoding="utf-8")
            else:
                print(out)
            return 0

        answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))
        if not isinstance(answers, dict):
            raise QuestionnaireError("answers JSON must be an object mapping id -> answer text")
        output = Path(args.output) if args.output else None
        result = write_answers(docx, answers, output)
        result["file"] = str(output or docx)
        if args.verbose:
            print(f"wrote {len(result['written'])} of {len(answers)} answers", file=sys.stderr)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if result["not_found"] else 0
    except (QuestionnaireError, FileNotFoundError, json.JSONDecodeError, ET.ParseError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
