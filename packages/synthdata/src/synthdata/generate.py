"""Build each case's answer-key entry and write the PDFs and the entries to disk."""

from pathlib import Path

from contracts.rules import is_medical
from synthdata.cases import CASES
from synthdata.model import (
    PAGE_TYPE_OF_LAYOUT,
    AnswerKeyEntry,
    CaseDefinition,
    ExpectedPage,
    PlantedIdentifier,
)
from synthdata.render import RenderedCase, render_case

CASES_FOLDER = "cases"
# AD-17: the answer key has its own folder, which no service code reads.
ANSWER_KEY_FOLDER = "answer-key/cases"


def answer_key_for(case: CaseDefinition, rendered: RenderedCase) -> AnswerKeyEntry:
    """Record what was planted where, from the text the renderer wrote on each page."""
    identifiers: list[PlantedIdentifier] = []
    for category, value in case.identifiers():
        pages = tuple(
            number
            for number, text in enumerate(rendered.page_texts, start=1)
            if value in text
        )
        if not pages:
            raise ValueError(f"{case.case_id}: {category} is not on any page")
        identifiers.append(
            PlantedIdentifier(category=category, value=value, pages=pages)
        )
    pages_expected = tuple(
        ExpectedPage(
            page_number=number,
            page_type=PAGE_TYPE_OF_LAYOUT[spec.layout],
            is_medical=is_medical(PAGE_TYPE_OF_LAYOUT[spec.layout]),
            layout=spec.layout,
            rotation=spec.rotation,
        )
        for number, spec in enumerate(case.pages, start=1)
    )
    return AnswerKeyEntry(
        case_id=case.case_id,
        file_name=case.file_name,
        summary=case.summary,
        pages=pages_expected,
        identifiers=tuple(identifiers),
    )


def write_all(data_dir: Path) -> list[Path]:
    """Write every case PDF and answer-key entry under `data_dir`; return the files written."""
    cases_dir = data_dir / CASES_FOLDER
    key_dir = data_dir / ANSWER_KEY_FOLDER
    cases_dir.mkdir(parents=True, exist_ok=True)
    key_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for case in CASES:
        rendered = render_case(case)
        entry = answer_key_for(case, rendered)
        pdf_path = cases_dir / case.file_name
        key_path = key_dir / f"{case.case_id}.json"
        pdf_path.write_bytes(rendered.pdf)
        key_path.write_text(entry.model_dump_json(indent=2) + "\n", encoding="utf-8")
        written += [pdf_path, key_path]
    return written
