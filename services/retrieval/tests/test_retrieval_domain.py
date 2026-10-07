"""Story 2.2: the chunker and the ingestion run, on fakes.

No database, no model and no network: the manual is a made-up parsed layout,
the models are a gateway stub and the store is in memory. The same run
against a real PostgreSQL is in `test_retrieval_integration.py`; against the
project's manual and the stand-ins it is tested beside the stand-ins
(`packages/` tests, story 2.2).
"""

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any

import pytest
from retrieval_fakes import (
    CHAT,
    CONTEXT,
    EMBEDDING,
    FOOTER,
    HEADER,
    MARK,
    PDF,
    RULE_A,
    RULE_B,
    RULE_C,
    FakeLayout,
    FakeManual,
    MemoryRepository,
    StubModel,
    context_answer,
    definition,
    layout,
    manual,
    options,
    without_roles,
)

from contracts.enums import ChunkSet
from contracts.errors import ErrorCode
from contracts.rules import rule_ids_defined_in
from retrieval.domain.chunker import (
    FURNITURE_ROLES,
    ManualInvalid,
    chunk_id_for,
    cut_chunks,
    cut_fixed_chunks,
)
from retrieval.domain.entities import (
    EMBEDDING_DIMENSIONS,
    Chunk,
    IngestRun,
    ParsedLayout,
    StoredChunk,
)
from retrieval.domain.ingest import (
    EMBEDDING_PROBE,
    IngestError,
    IngestPorts,
    fingerprint,
    ingest_manual,
)
from retrieval.domain.ports import (
    ModelCallFailed,
    ModelUnavailable,
)

SERVICE_DIR = Path(__file__).resolve().parents[1]


def ingest(ports: IngestPorts, **changes: Any) -> Any:
    return asyncio.run(ingest_manual(ports, options(**changes)))


def failure(ports: IngestPorts, **changes: Any) -> IngestError:
    with pytest.raises(IngestError) as raised:
        ingest(ports, **changes)
    return raised.value


# --- The chunker ---------------------------------------------------------------------------


def test_story_2_2_a_smart_chunk_holds_exactly_one_rule_with_its_place_in_the_manual() -> (
    None
):
    chunks = cut_chunks(manual())

    # One chunk per rule the manual defines, in the manual's order.
    assert [chunk.rule_id for chunk in chunks] == [RULE_A, RULE_B, RULE_C]
    first, second, third = chunks
    assert first.chunk_set is ChunkSet.SMART
    assert first.text == definition(RULE_A, f"{MARK} Mild. See rule {RULE_B}.")
    # The part the definition is printed in, the section's heading as the
    # impairment, and the page of the definition.
    assert (first.section_id, first.section_title) == ("2.4", "Probable rating")
    assert (first.impairment, first.manual_page) == ("Raised blood sugar", 3)
    assert (third.section_id, third.impairment, third.manual_page) == ("3.4", "Gout", 4)
    for chunk in chunks:
        # AD-12: exactly the rule its text defines, by the contracts' function.
        assert (
            list(chunk.rule_ids) == rule_ids_defined_in(chunk.text) == [chunk.rule_id]
        )
    assert second.manual_page == 3
    # The chunk id is derived from the chunk set and the rule: the same on
    # every run, and when the rest of the manual moves. Letters, digits and
    # dashes, a form Azure AI Search takes as a key (Epic 3).
    assert first.chunk_id == chunk_id_for(ChunkSet.SMART, RULE_A) == f"smart-{RULE_A}"
    again = cut_chunks(manual(filler_pages=2))[0]
    assert again.chunk_id == first.chunk_id
    assert again.manual_page == first.manual_page + 2
    assert first.chunk_id.replace("-", "").isalnum()
    # Two definitions the layout model read as one paragraph are two chunks.
    both = f"{definition(RULE_A, 'First band.')} {definition(RULE_B, 'Second band.')}"
    joined = cut_chunks(layout(["1 Gout", "1.4 Probable rating", f"Before. {both}"]))
    assert [(chunk.rule_id, chunk.text) for chunk in joined] == [
        (RULE_A, f"Rule {RULE_A}: First band."),
        (RULE_B, f"Rule {RULE_B}: Second band."),
    ]


def test_story_2_2_a_rule_a_chunk_refers_to_is_a_reference_never_one_of_its_rule_ids() -> (
    None
):
    first, second, third = cut_chunks(manual())

    assert first.reference_rule_ids == (RULE_B,)
    assert second.reference_rule_ids == (RULE_A,)
    # In order of first mention, each once.
    assert third.reference_rule_ids == (RULE_B, RULE_A)
    for chunk in (first, second, third):
        assert not set(chunk.rule_ids) & set(chunk.reference_rule_ids)
    # A mention outside a definition defines nothing: `3.5 ... Under rule`
    # and the table cell that holds an id are no chunks.
    assert len(cut_chunks(manual())) == 3


def test_story_2_2_page_furniture_is_in_no_chunk_and_inside_a_rules_text_fails_the_run() -> (
    None
):
    with_roles = cut_chunks(manual())
    # The same manual as a layout model without roles gives it. The one line
    # that only a role tells from a heading is left out: see the test of it.
    no_roles = cut_chunks(without_roles(manual(), "3 Months after diagnosis"))

    assert no_roles == with_roles
    for chunk in with_roles:
        assert HEADER not in chunk.text
        assert FOOTER not in chunk.text
        assert "Page " not in chunk.text

    # A header or footer inside a rule's text fails the run: as a layout
    # model might merge a footer into the paragraph above it.
    merged = manual(first=f"{definition(RULE_A)} {FOOTER}")

    with pytest.raises(ManualInvalid) as raised:
        cut_chunks(merged)

    assert (raised.value.reason, raised.value.where) == (
        "page_furniture_in_chunk",
        RULE_A,
    )


def test_story_3_2_the_fixed_cut_is_the_body_text_in_order_in_runs_of_a_fixed_size() -> (
    None
):
    size, overlap = 12, 4
    chunks = cut_fixed_chunks(manual(), size, overlap)

    # The body text in reading order, page furniture left out: every chunk
    # but the last has the fixed size, and begins with the last words of
    # the one before it.
    body = [
        word
        for paragraph in manual().paragraphs
        if paragraph.role not in FURNITURE_ROLES
        for word in paragraph.text.split()
    ]
    rebuilt = chunks[0].text.split()
    for chunk in chunks[1:]:
        words = chunk.text.split()
        assert words[:overlap] == rebuilt[-overlap:]
        rebuilt += words[overlap:]
    assert rebuilt == body
    assert all(len(chunk.text.split()) == size for chunk in chunks[:-1])
    for chunk in chunks:
        assert chunk.chunk_set is ChunkSet.FIXED
        assert HEADER not in chunk.text and FOOTER not in chunk.text
        assert "Page " not in chunk.text
        # AD-12: the rules whose definition marker lies inside its text, by
        # the contracts' function; a rule it only mentions is a reference.
        assert list(chunk.rule_ids) == rule_ids_defined_in(chunk.text)
        assert not set(chunk.rule_ids) & set(chunk.reference_rule_ids)
    # Every rule of the manual is defined by a chunk, and referred to by another.
    assert {rule for chunk in chunks for rule in chunk.rule_ids} == {
        RULE_A,
        RULE_B,
        RULE_C,
    }
    assert any(RULE_B in chunk.reference_rule_ids for chunk in chunks)
    # The id is the chunk set and the position: the same on every run of
    # the same manual and settings, sorted in the manual's order, and of
    # the form Azure AI Search takes as a key.
    assert [chunk.chunk_id for chunk in chunks] == [
        f"fixed-{position:04d}" for position in range(1, len(chunks) + 1)
    ]
    assert cut_fixed_chunks(manual(), size, overlap) == chunks
    # Two paragraphs are a line break apart, the words of one a space.
    assert f"1 Introduction\nThis manual is made up. {MARK}" in "\n".join(
        chunk.text for chunk in chunks
    )
    # Section, impairment and page are those of where the chunk starts; one
    # that starts before the first numbered section stands in the front matter.
    assert (chunks[0].section_id, chunks[0].impairment, chunks[0].manual_page) == (
        "0",
        "Front matter",
        1,
    )
    assert {c.impairment for c in chunks if c.manual_page == 3} == {
        "Raised blood sugar"
    }
    last = chunks[-1]
    assert (last.impairment, last.manual_page) == ("Gout", 4)
    assert last.section_id.startswith("3")
    # Without roles the contents page's lines read as headings: what stands
    # before the first section's own heading is front matter all the same.
    no_roles = cut_fixed_chunks(
        without_roles(manual(), "3 Months after diagnosis"), size, overlap
    )
    assert {c.impairment for c in no_roles if c.manual_page == 1} == {"Front matter"}
    assert {c.impairment for c in no_roles if c.manual_page == 3} == {
        "Raised blood sugar"
    }
    # Another size is another cut.
    assert len(cut_fixed_chunks(manual(), 30, overlap)) < len(chunks)
    # It checks its own result: with no overlap, a cut that falls inside a
    # definition marker leaves the rule defined by no chunk.
    split = layout(["1 Gout", f"Before Rule {RULE_A}: Threshold: a reading."])
    assert cut_fixed_chunks(split, 4, 1)[1].rule_ids == (RULE_A,)
    with pytest.raises(ManualInvalid) as raised:
        cut_fixed_chunks(split, 4, 0)
    assert (raised.value.reason, raised.value.where) == (
        "definition_in_no_chunk",
        RULE_A,
    )
    # A marker that only the joining of two paragraphs forms defines nothing,
    # and a footer the layout model merged into a paragraph is refused there.
    for parsed, reason, where in (
        (
            layout(["1 Gout", definition(RULE_A), "See the Rule", f"{RULE_B}: no."]),
            "definition_across_paragraphs",
            RULE_B,
        ),
        (
            layout(["1 Gout", definition(RULE_A), f"Text. {FOOTER}"]),
            "page_furniture_in_chunk",
            "1",
        ),
    ):
        with pytest.raises(ManualInvalid) as raised:
            cut_fixed_chunks(parsed, 12, 4)
        assert (raised.value.reason, raised.value.where) == (reason, where)


@pytest.mark.parametrize(
    ("parsed", "reason", "where"),
    [
        # A rule id defined twice.
        (
            layout(["1 Gout", definition(RULE_A)], [definition(RULE_A, "Again.")]),
            "rule_defined_twice",
            RULE_A,
        ),
        # A definition cut before the end of its sentence.
        (
            layout(["1 Gout", f"Rule {RULE_A}: Threshold: a reading in the"]),
            "definition_cut_short",
            RULE_A,
        ),
        # A rule the layout model took for a footer.
        (
            layout(["1 Gout", (definition(RULE_A), "pageFooter")]),
            "definition_in_page_furniture",
            RULE_A,
        ),
    ],
)
def test_story_2_2_the_chunker_checks_its_own_result_and_fails_loudly(
    parsed: ParsedLayout, reason: str, where: str
) -> None:
    with pytest.raises(ManualInvalid) as raised:
        cut_chunks(parsed)

    assert (raised.value.reason, raised.value.where) == (reason, where)
    # A code and an id, never text of the manual.
    assert MARK not in str(raised.value)


# --- The run ---------------------------------------------------------------------------------


def test_story_2_2_a_first_run_stores_one_record_per_rule(
    ports: IngestPorts, repository: MemoryRepository, model: StubModel
) -> None:
    report = ingest(ports)

    assert (report.pages, report.chunks, report.written) == (4, 3, 3)
    assert (report.moved, report.removed, report.unchanged) == (0, 0, 0)
    assert sorted(repository.records) == [
        f"smart-{RULE_A}",
        f"smart-{RULE_B}",
        f"smart-{RULE_C}",
    ]
    record = repository.records[f"smart-{RULE_A}"]
    # The record is the one source for every store: the chunk, the context
    # line the chat model wrote, the vector and what made them.
    assert record.context_line == CONTEXT
    assert len(record.embedding) == EMBEDDING_DIMENSIONS
    assert (record.chat_deployment, record.embedding_deployment) == (CHAT, EMBEDDING)
    assert record.content_hash == fingerprint(record.chunk, options())
    # All of it in one transaction, with a note of what it was built from.
    assert len(repository.applies) == 1
    assert repository.run == IngestRun(
        manual_sha256=hashlib.sha256(PDF).hexdigest(),
        prompt_digest="digest-1",
        chat_deployment=CHAT,
        embedding_deployment=EMBEDDING,
        chunk_count=3,
    )


def test_story_2_2_a_run_that_would_remove_too_many_chunks_is_refused(
    ports: IngestPorts,
    repository: MemoryRepository,
    model: StubModel,
    layout_parser: FakeLayout,
    manual_store: FakeManual,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ingest(ports)
    before = dict(repository.records)
    calls = model.calls
    # A manual that lost a rule, or one that was read badly: a third of the set.
    manual_store.pdf = PDF + b" shorter"
    layout_parser.parsed = manual(with_c=False)

    with caplog.at_level(logging.WARNING):
        error = failure(ports, max_removed_share=0.1)

    assert (error.code, error.reason, error.where) == (
        ErrorCode.STAGE_FAILED,
        "too_many_chunks_removed",
        "1",
    )
    # Refused before a model is asked, and the good chunks are all there.
    assert model.calls == calls
    assert repository.records == before
    # The log names what would have gone, by id.
    assert f"chunks to remove: count=1 of=3 chunk_ids=smart-{RULE_C}" in caplog.text
    # With the override the same run goes through, and still says what it removes.
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        report = ingest(ports, max_removed_share=0.1, allow_large_removal=True)
    assert report.removed == 1
    assert f"smart-{RULE_C}" not in repository.records
    assert f"chunk_ids=smart-{RULE_C}" in caplog.text


def _stored(chunk: Chunk, page: int | None = None) -> StoredChunk:
    return StoredChunk(
        chunk.chunk_id, fingerprint(chunk, options()), page or chunk.manual_page
    )


# --- Failures: the index is untouched --------------------------------------------------------


@pytest.mark.parametrize("failing", ["embed_error"])
def test_story_2_2_a_model_that_is_unavailable_fails_the_run_and_writes_nothing(
    ports: IngestPorts, repository: MemoryRepository, model: StubModel, failing: str
) -> None:
    setattr(model, failing, ModelUnavailable())

    error = failure(ports)

    assert (error.code, error.reason) == (
        ErrorCode.MODEL_UNAVAILABLE,
        "model_unavailable",
    )
    assert repository.records == {}
    assert repository.applies == []
    if failing == "embed_error":
        # Found by the one small embedding call: no context line was paid for.
        assert model.shown == []
        assert model.embedded == [[EMBEDDING_PROBE]]
    # A call the model refuses is a failure too, with the gateway's code.
    setattr(model, failing, ModelCallFailed("model_status_404"))
    refused = failure(ports)
    assert (refused.code, refused.reason) == (
        ErrorCode.STAGE_FAILED,
        "model_status_404",
    )
    assert repository.applies == []


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (context_answer("One line.\nAnd another."), "context_line_multi_line"),
    ],
)
def test_story_2_2_a_context_line_that_is_not_one_line_of_text_is_refused(
    ports: IngestPorts,
    repository: MemoryRepository,
    model: StubModel,
    answer: str,
    reason: str,
) -> None:
    model.answer = answer

    error = failure(ports)

    assert (error.code, error.reason) == (ErrorCode.INVALID_MODEL_OUTPUT, reason)
    # Refused, never cut or mended: no chunk is embedded and nothing stored.
    assert model.embedded == [[EMBEDDING_PROBE]]
    assert repository.applies == []


def test_story_2_2_the_run_logs_ids_and_counts_never_text_of_the_manual(
    ports: IngestPorts, caplog: pytest.LogCaptureFixture, model: StubModel
) -> None:
    with caplog.at_level(logging.DEBUG):
        ingest(ports)
        model.answer = "not json"
        failure(ports, prompt_digest="digest-2")

    assert "pages=4 chunks=3 chunk_set=smart" in caplog.text
    assert "SECRET" not in caplog.text
    assert "Raised blood sugar" not in caplog.text


# --- The domain stays what it is --------------------------------------------------------------


def test_story_2_2_the_domain_imports_no_framework_and_no_adapter() -> None:
    for source in (SERVICE_DIR / "src" / "retrieval" / "domain").glob("*.py"):
        text = source.read_text()
        for name in ("fastapi", "sqlalchemy", "httpx", "openai", "azure", "pgvector"):
            assert f"import {name}" not in text, source.name
            assert f"from {name}" not in text, source.name
        assert "retrieval.adapters" not in text, source.name
