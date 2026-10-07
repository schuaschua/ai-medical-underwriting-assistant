"""Story 2.2: the manual, ingested by `retrieval`'s job, against the rule table.

The job runs as it really runs, over the project's manual in a blob container
of the test's own and a real PostgreSQL, with this package's stand-ins where
Document Intelligence and the Foundry deployments would be. Only here, outside
`services/`, are its chunks compared with the answer key's rule table: the job
itself never reads it (spine AD-17).

Run `docker compose up --detach --wait` first.
"""

import json
import logging
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from synthdata_stack import MANUAL_PDF, LocalRetrieval, rule_table

from contracts.rules import rule_ids_defined_in
from synthdata.foundry_standin import (
    EMBEDDING_DIMENSIONS,
)

pytestmark = pytest.mark.integration

# The rule whose text the changed manual changes, and the one it removes.
CHANGED, REMOVED = "UW-DM-002", "UW-DM-004"


def vector_of(chunk: dict[str, Any]) -> list[float]:
    return [float(value) for value in json.loads(chunk["embedding"])]


# --- First run -----------------------------------------------------------------------


def test_story_2_2_the_manual_is_ingested_as_one_smart_chunk_per_rule_of_the_rule_table(
    ingested_manual: LocalRetrieval,
) -> None:
    rules = rule_table()
    chunks = ingested_manual.chunks()

    # Every rule of the table is defined by exactly one chunk, and no chunk
    # defines a rule the table lacks.
    defined = [rule_id for chunk in chunks.values() for rule_id in chunk["rule_ids"]]
    assert sorted(defined) == sorted(rules)
    assert len(chunks) == len(rules) == 111
    for chunk_id, chunk in chunks.items():
        (rule_id,) = chunk["rule_ids"]
        rule = rules[rule_id]
        # The id is derived from the chunk set and the rule (Epic 3 loads
        # the same records into another store).
        assert chunk_id == f"smart-{rule_id}"
        assert chunk["chunk_set"] == "smart"
        # Exactly one rule: its definition, and nothing of another's.
        assert rule_ids_defined_in(chunk["text"]) == [rule_id]
        assert chunk["text"].startswith(f"Rule {rule_id}: {rule['impairment']}")
        assert rule["threshold"]["words"] in chunk["text"]
        # Its parent section, its impairment and the page of its definition,
        # as the generator recorded them in the answer key.
        assert chunk["section_id"] == f"{rule['section']}.4"
        assert chunk["impairment"] == rule["impairment"]
        assert chunk["manual_page"] == rule["manual_page"]
        # One context line by the chat model, saying where the rule sits.
        line = chunk["context_line"]
        assert line and "\n" not in line
        assert f"section {rule['section']}, {rule['impairment']}" in line
        # A 3,072-dimension vector.
        assert len(vector_of(chunk)) == EMBEDDING_DIMENSIONS == 3072


# --- Reruns ----------------------------------------------------------------------------


def test_story_2_2_a_second_run_changes_nothing_and_calls_no_model(
    ingested_manual: LocalRetrieval, caplog: pytest.LogCaptureFixture
) -> None:
    before = ingested_manual.chunks()
    calls = ingested_manual.model_calls

    with caplog.at_level(logging.INFO):
        status = ingested_manual.ingest()

    assert status == 0
    # Same ids, text and vectors; not one row was written again.
    assert ingested_manual.chunks() == before
    # No chat call and no embedding call.
    assert ingested_manual.model_calls == calls
    assert "written=0 moved=0 removed=0 unchanged=111 skipped=yes" in caplog.text
    # The manual is the one the index was built from: it was not sent to the
    # layout model a second time.
    assert ingested_manual.layout.submits == 1
    assert "manual unchanged since the last run" in caplog.text


# --- Failures --------------------------------------------------------------------------


def test_story_2_2_a_manual_that_defines_a_rule_twice_is_refused_whole(
    retrieval: LocalRetrieval, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The manual with its first rule page printed a second time at the end.
    with (
        pymupdf.open(MANUAL_PDF) as document,  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        pymupdf.open(MANUAL_PDF) as copy,  # type: ignore[no-untyped-call]  # as above
    ):
        first = next(
            number
            for number, page in enumerate(document)
            if "Rule UW-DM-001:" in page.get_text()
        )
        document.insert_pdf(copy, from_page=first, to_page=first)
        twice = tmp_path / "twice.pdf"
        document.save(twice)
    retrieval.upload(twice)

    with caplog.at_level(logging.INFO):
        status = retrieval.ingest()

    assert status == 1
    assert (
        "ingestion failed: code=stage_failed reason=rule_defined_twice where=UW-DM-001"
        in caplog.text
    )
    # An error, not a partial index.
    assert retrieval.chunks() == {}
    assert retrieval.model_calls == 0
