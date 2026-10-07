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
import math
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from synthdata_stack import MANUAL_PDF, LocalRetrieval, rule_table

from contracts.models.retrieval import RuleText, SearchItem
from contracts.rules import rule_ids_defined_in
from retrieval.domain.ingest import EMBEDDING_PROBE
from synthdata.foundry_standin import (
    EMBEDDING_DIMENSIONS,
    LOCAL_DEPLOYMENT,
    LOCAL_EMBEDDING_DEPLOYMENT,
    embed_text,
)
from synthdata.foundry_standin import Mode as ModelMode
from synthdata.layout_standin import Mode as LayoutMode
from synthdata.manual_rules import MANUAL
from synthdata.render import FOOTER

pytestmark = pytest.mark.integration

# The rule whose text the changed manual changes, and the one it removes.
CHANGED, REMOVED = "UW-DM-002", "UW-DM-004"
MANUAL_TITLE = MANUAL.title


def changed_manual(folder: Path) -> Path:
    """The project's manual with one rule's text changed and one rule removed.

    Made by editing the PDF, not by the generator: the job must see the
    change in what it reads, whoever made it.
    """
    with pymupdf.open(MANUAL_PDF) as document:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        for page in document:
            rewritten: list[tuple[Any, str]] = []
            for block in page.get_text("blocks"):
                box, text = block[:4], " ".join(block[4].split())
                if text.startswith(f"Rule {REMOVED}:"):
                    # The whole definition goes.
                    page.add_redact_annot(box)
                elif text.startswith(f"Rule {CHANGED}:"):
                    # The definition is set again in its place, two words shorter.
                    page.add_redact_annot(box)
                    rewritten.append((box, text.replace("Probable rating:", "Rating:")))
            page.apply_redactions()
            for box, text in rewritten:
                fitted = page.insert_textbox(box, text, fontsize=10.5, fontname="helv")
                assert fitted >= 0, "the changed definition does not fit its place"
        path = folder / "changed-manual.pdf"
        document.save(path)
    return path


def vector_of(chunk: dict[str, Any]) -> list[float]:
    return [float(value) for value in json.loads(chunk["embedding"])]


def cosine(first: list[float], second: list[float]) -> float:
    dot = sum(a * b for a, b in zip(first, second, strict=True))
    lengths = math.sqrt(sum(a * a for a in first) * sum(b * b for b in second))
    return dot / lengths


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


def test_story_2_2_a_rule_a_chunk_mentions_is_a_reference_not_one_of_its_rule_ids(
    ingested_manual: LocalRetrieval,
) -> None:
    rules = rule_table()
    chunks = ingested_manual.chunks()
    with_references = 0

    for chunk in chunks.values():
        (rule_id,) = chunk["rule_ids"]
        expected = [reference["rule_id"] for reference in rules[rule_id]["references"]]
        # In the order the definition mentions them.
        assert chunk["reference_rule_ids"] == expected
        assert rule_id not in chunk["reference_rule_ids"]
        for reference in expected:
            assert f"see rule {reference}" in chunk["text"]
        with_references += bool(expected)
    # The manual does have cross-references, so the check above saw some.
    assert with_references > 10


def test_story_2_2_no_chunk_holds_page_furniture(
    ingested_manual: LocalRetrieval,
) -> None:
    # Story 2.1's review: the header and the footer are on every page of the
    # manual, in its text layer.
    with pymupdf.open(MANUAL_PDF) as document:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        page_text = " ".join(document[11].get_text().split())
    assert MANUAL_TITLE in page_text and FOOTER in page_text

    for chunk in ingested_manual.chunks().values():
        for text in (chunk["text"], chunk["context_line"]):
            assert MANUAL_TITLE not in text
            assert FOOTER not in text
            assert "SYNTHETIC TEST DOCUMENT" not in text
        assert f"Page {chunk['manual_page']}" not in chunk["text"]


def test_story_2_2_the_chunk_record_fills_the_contracts_search_item_and_rule_text(
    ingested_manual: LocalRetrieval,
) -> None:
    chunk = ingested_manual.chunks()["smart-UW-DM-001"]

    # What story 2.3 answers with is all in the record.
    item = SearchItem(
        chunk_id=chunk["chunk_id"],
        rule_ids=chunk["rule_ids"],
        rank=1,
        score=1.0,
        text=chunk["text"],
        manual_page=chunk["manual_page"],
        impairment=chunk["impairment"],
    )
    rule = RuleText(
        rule_id=chunk["rule_ids"][0],
        chunk_id=chunk["chunk_id"],
        chunk_set=chunk["chunk_set"],
        text=chunk["text"],
        manual_page=chunk["manual_page"],
        impairment=chunk["impairment"],
        reference_rule_ids=chunk["reference_rule_ids"],
    )
    assert (item.rule_ids, rule.rule_id) == (["UW-DM-001"], "UW-DM-001")


def test_story_2_2_the_embedded_text_is_the_context_line_followed_by_the_chunk_text(
    ingested_manual: LocalRetrieval,
) -> None:
    chunks = ingested_manual.chunks()
    model = ingested_manual.model

    # One chat call per chunk, and every embedding call on the one embedding
    # deployment, each text the context line and then the chunk text.
    assert model.calls == len(chunks)
    assert {request["model"] for request in model.requests} == {LOCAL_DEPLOYMENT}
    assert {request["model"] for request in model.embedding_requests} == {
        LOCAL_EMBEDDING_DEPLOYMENT
    }
    # The first embedding call is the small one that tries the deployment
    # before any context line is written.
    assert model.embedding_requests[0]["input"] == [EMBEDDING_PROBE]
    embedded = [
        text for request in model.embedding_requests[1:] for text in request["input"]
    ]
    assert sorted(embedded) == sorted(
        f"{chunk['context_line']}\n{chunk['text']}" for chunk in chunks.values()
    )
    # The stored vector is the vector of that text.
    chunk = chunks["smart-UW-DM-001"]
    assert vector_of(chunk) == pytest.approx(
        embed_text(f"{chunk['context_line']}\n{chunk['text']}"), abs=1e-6
    )
    # Its nearest neighbours by the stand-in's vectors are the rules that
    # share its words: the other bands of the diabetes sections.
    nearest = sorted(
        chunks.values(),
        key=lambda other: -cosine(vector_of(chunk), vector_of(other)),
    )[:4]
    assert nearest[0]["chunk_id"] == "smart-UW-DM-001"
    assert all("diabetes mellitus" in other["impairment"] for other in nearest)


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


def test_story_2_2_a_layout_without_roles_gives_the_same_chunks(
    ingested_manual: LocalRetrieval,
) -> None:
    before = ingested_manual.chunks()
    calls = ingested_manual.model_calls
    # As the real service may answer: no paragraph says what it is. The
    # manual is unchanged, so the note of the last run is taken away to have
    # it parsed again.
    ingested_manual.layout.roles = False
    ingested_manual.forget_last_run()

    assert ingested_manual.ingest() == 0

    # Cut from numbers and repetition alone, the chunks are the same ones:
    # nothing is written and no model is asked.
    assert ingested_manual.layout.submits == 2
    assert ingested_manual.chunks() == before
    assert ingested_manual.model_calls == calls


def test_story_2_2_a_changed_manual_replaces_what_changed_and_removes_what_is_gone(
    ingested_manual: LocalRetrieval, tmp_path: Path
) -> None:
    before = ingested_manual.chunks()
    chat, embeddings = (
        ingested_manual.model.calls,
        ingested_manual.model.embedding_calls,
    )
    ingested_manual.upload(changed_manual(tmp_path))

    assert ingested_manual.ingest() == 0

    after = ingested_manual.chunks()
    changed, removed = f"smart-{CHANGED}", f"smart-{REMOVED}"
    # The removed rule's chunk is gone.
    assert sorted(set(before) - set(after)) == [removed]
    # The changed chunk was written and embedded again.
    assert "Probable rating:" in before[changed]["text"]
    assert "Probable rating:" not in after[changed]["text"]
    assert after[changed]["embedding"] != before[changed]["embedding"]
    assert after[changed]["content_hash"] != before[changed]["content_hash"]
    # The rest is untouched, down to the row version.
    for chunk_id in set(after) - {changed}:
        assert after[chunk_id] == before[chunk_id]
    # One chat call and one embedding call: for the changed chunk only.
    # (After the one small embedding call every run with work to do begins with.)
    assert ingested_manual.model.calls == chat + 1
    assert ingested_manual.model.embedding_calls == embeddings + 2


# --- Failures --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "changes", "reason"),
    [
        (LayoutMode.FAIL, {}, "layout_failed_InternalServerError"),
        (LayoutMode.REJECT, {}, "layout_submit_status_400"),
        (LayoutMode.THROTTLED, {}, "layout_submit_status_429"),
        # An analysis that never ends: Document Intelligence times out.
        (LayoutMode.HANG, {"layout_deadline_seconds": 0.05}, "layout_timeout"),
    ],
)
def test_story_2_2_when_layout_parsing_fails_the_job_ends_non_zero_and_the_index_is_untouched(
    ingested_manual: LocalRetrieval,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    mode: LayoutMode,
    changes: dict[str, Any],
    reason: str,
) -> None:
    before = ingested_manual.chunks()
    ingested_manual.upload(changed_manual(tmp_path))
    ingested_manual.layout.mode = mode

    with caplog.at_level(logging.INFO):
        status = ingested_manual.ingest(**changes)

    assert status == 1
    assert f"ingestion failed: code=upstream_unavailable reason={reason}" in caplog.text
    assert ingested_manual.chunks() == before


@pytest.mark.parametrize(
    ("mode", "dimensions", "code", "reason"),
    [
        # The chat model, then the embedding model, fail after their retries.
        (ModelMode.THROTTLED, 3072, "model_unavailable", "model_unavailable"),
        # A context line that is not the answer asked for.
        (ModelMode.INVALID, 3072, "invalid_model_output", "context_line_not_json"),
        # A vector of the wrong size.
        (ModelMode.OK, 1536, "invalid_model_output", "embedding_wrong_size"),
    ],
)
def test_story_2_2_when_a_model_fails_or_answers_badly_the_job_fails_and_the_index_is_whole(
    ingested_manual: LocalRetrieval,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    mode: ModelMode,
    dimensions: int,
    code: str,
    reason: str,
) -> None:
    before = ingested_manual.chunks()
    ingested_manual.upload(changed_manual(tmp_path))
    ingested_manual.model.mode = mode
    ingested_manual.model.embedding_dimensions = dimensions

    with caplog.at_level(logging.INFO):
        status = ingested_manual.ingest()

    assert status == 1
    assert f"ingestion failed: code={code} reason={reason}" in caplog.text
    # A failure leaves the previous index whole: nothing rewritten or removed.
    assert ingested_manual.chunks() == before
    # No text of the manual in the job's log.
    assert "HbA1c" not in caplog.text
    assert "Rule UW-" not in caplog.text


def test_story_2_2_when_the_embedding_model_is_unavailable_no_chat_call_is_spent(
    retrieval: LocalRetrieval, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The chat model would answer; the embedding model is throttled on every call.
    stand_in = retrieval.model
    answered = stand_in.embed

    def throttled(body: dict[str, Any]) -> Any:
        stand_in.mode = ModelMode.THROTTLED
        try:
            return answered(body)
        finally:
            stand_in.mode = ModelMode.OK

    monkeypatch.setattr(stand_in, "embed", throttled)

    assert retrieval.ingest() == 1
    # The one small embedding call and its three retries found it, before
    # any of the 111 context lines was asked for.
    assert stand_in.embedding_calls == 4
    assert stand_in.calls == 0
    assert retrieval.chunks() == {}


def test_story_2_2_without_the_manual_in_its_container_the_job_says_so(
    retrieval: LocalRetrieval, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        status = retrieval.ingest(manual_blob_name="another-manual.pdf")

    assert status == 1
    assert "ingestion failed: code=not_found reason=manual_missing" in caplog.text
    assert retrieval.layout.submits == 0
    assert retrieval.model_calls == 0
    assert retrieval.chunks() == {}


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
