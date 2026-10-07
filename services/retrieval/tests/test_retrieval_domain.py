"""Story 2.2: the chunker and the ingestion run, on fakes.

No database, no model and no network: the manual is a made-up parsed layout,
the models are a gateway stub and the store is in memory. The same run
against a real PostgreSQL is in `test_retrieval_integration.py`; against the
project's manual and the stand-ins it is tested beside the stand-ins
(`packages/` tests, story 2.2).
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import replace
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
    vector_for,
    without_roles,
)

from contracts.enums import ChunkSet
from contracts.errors import ErrorCode
from contracts.rules import rule_ids_defined_in
from retrieval.domain.chunker import ManualInvalid, chunk_id_for, cut_chunks
from retrieval.domain.entities import (
    EMBEDDING_DIMENSIONS,
    Chunk,
    IngestRun,
    LayoutPage,
    LayoutParagraph,
    ParsedLayout,
    StoredChunk,
)
from retrieval.domain.ingest import (
    EMBEDDING_PROBE,
    IngestError,
    IngestPorts,
    embedding_text,
    fingerprint,
    ingest_manual,
    parse_context_line,
    plan_ingestion,
    rule_in_its_place,
)
from retrieval.domain.ports import (
    ModelAnswerInvalid,
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


def test_story_2_2_the_chunk_id_is_derived_from_the_chunk_set_and_the_rule() -> None:
    first, _, _ = cut_chunks(manual())

    assert first.chunk_id == chunk_id_for(ChunkSet.SMART, RULE_A) == f"smart-{RULE_A}"
    # The same on every run, and when the rest of the manual moves.
    again = cut_chunks(manual(filler_pages=2))[0]
    assert again.chunk_id == first.chunk_id
    assert again.manual_page == first.manual_page + 2
    # Another chunk set gets ids of its own (Epic 3), in a form Azure AI
    # Search takes as a key: letters, digits and dashes.
    assert chunk_id_for(ChunkSet.FIXED, RULE_A) == f"fixed-{RULE_A}"
    assert first.chunk_id.replace("-", "").isalnum()


def test_story_2_2_page_furniture_is_in_no_chunk_with_or_without_roles() -> None:
    with_roles = cut_chunks(manual())
    # The same manual as a layout model without roles gives it. The one line
    # that only a role tells from a heading is left out: see the test of it.
    no_roles = cut_chunks(without_roles(manual(), "3 Months after diagnosis"))

    assert no_roles == with_roles
    for chunk in with_roles:
        assert HEADER not in chunk.text
        assert FOOTER not in chunk.text
        assert "Page " not in chunk.text


def test_story_2_2_a_header_or_footer_inside_a_rules_text_fails_the_run() -> None:
    # As a layout model might merge a footer into the paragraph above it.
    merged = manual(first=f"{definition(RULE_A)} {FOOTER}")

    with pytest.raises(ManualInvalid) as raised:
        cut_chunks(merged)

    assert (raised.value.reason, raised.value.where) == (
        "page_furniture_in_chunk",
        RULE_A,
    )


def test_story_2_2_two_definitions_in_one_paragraph_are_two_chunks() -> None:
    both = f"{definition(RULE_A, 'First band.')} {definition(RULE_B, 'Second band.')}"
    chunks = cut_chunks(layout(["1 Gout", "1.4 Probable rating", f"Before. {both}"]))

    assert [(chunk.rule_id, chunk.text) for chunk in chunks] == [
        (RULE_A, f"Rule {RULE_A}: First band."),
        (RULE_B, f"Rule {RULE_B}: Second band."),
    ]


def test_story_2_2_white_space_in_a_definition_is_squashed() -> None:
    (chunk,) = cut_chunks(
        layout(["1 Gout", f"Rule  {RULE_A}:\n  Urate  above\n the band. "])
    )

    assert chunk.text == f"Rule {RULE_A}: Urate above the band."
    # A section without a numbered part: the section itself is the place.
    assert (chunk.section_id, chunk.section_title) == ("1", "")


def test_story_2_2_sections_are_found_by_their_printed_numbers_in_order() -> None:
    chunks = cut_chunks(
        layout(
            ["1 First", "2 Second", "3 Third"],
            # The count begins again where the sections really start.
            ["1 First", definition(RULE_A)],
            # Not the next number, so not a heading: a reading.
            ["40 units is the edge of the band", definition(RULE_B)],
            ["2 Second", "9.9 is no part of section 2", definition(RULE_C)],
        )
    )

    assert [
        (chunk.rule_id, chunk.impairment, chunk.section_id) for chunk in chunks
    ] == [
        (RULE_A, "First", "1"),
        (RULE_B, "First", "1"),
        (RULE_C, "Second", "2"),
    ]


@pytest.mark.parametrize(
    ("parsed", "reason", "where"),
    [
        # A rule id defined twice.
        (
            layout(["1 Gout", definition(RULE_A)], [definition(RULE_A, "Again.")]),
            "rule_defined_twice",
            RULE_A,
        ),
        # A definition with no text.
        (layout(["1 Gout", f"Rule {RULE_A}:"]), "definition_without_text", RULE_A),
        (
            layout(["1 Gout", f"Rule {RULE_A}: Rule {RULE_B}: Something."]),
            "definition_without_text",
            RULE_A,
        ),
        # Zero rules.
        (layout(["1 Gout", f"See rule {RULE_A} elsewhere."]), "no_rules", ""),
        (ParsedLayout(pages=(), paragraphs=()), "no_pages", ""),
        # A definition before any numbered section.
        (layout([definition(RULE_A)]), "definition_outside_section", RULE_A),
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
        # A second paragraph that claims a section number already taken.
        (
            layout(["1 Gout", definition(RULE_A)], ["1 Gout again", "More."]),
            "section_number_taken",
            "1",
        ),
        # A heading that skips a number: one was missed.
        (
            layout(["1 Gout", definition(RULE_A)], ["3 Asthma", definition(RULE_C)]),
            "section_number_skipped",
            "3",
        ),
        (layout(["2 Gout", definition(RULE_A)]), "section_number_skipped", "2"),
        # A part of a section the walk is not in.
        (
            layout(["1 Gout", "2.4 Probable rating", definition(RULE_A)]),
            "part_outside_section",
            "2.4",
        ),
        # Without roles, a stray numbered line reads as the next section;
        # the real heading then finds its number taken.
        (without_roles(manual()), "part_outside_section", "2.4"),
        (
            layout(
                ["1 Gout", definition(RULE_A), "2 Weeks after an attack"],
                ["2 Asthma", definition(RULE_C)],
            ),
            "section_number_taken",
            "2",
        ),
        # The rules of one section share one id code: a missed heading puts
        # the next section's rules under this one.
        (
            layout(["1 Gout", definition(RULE_A), definition(RULE_C)]),
            "section_with_two_rule_codes",
            RULE_C,
        ),
        # ... and one code belongs to one section.
        (
            layout(["1 Gout", definition(RULE_A)], ["2 Asthma", definition(RULE_B)]),
            "rule_code_in_two_sections",
            RULE_B,
        ),
        # A rule that is referred to and defined nowhere.
        (
            layout(["1 Gout", definition(RULE_A, "See rule UW-ZZ-009.")]),
            "reference_not_defined",
            "UW-ZZ-009",
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


def test_story_2_2_with_roles_a_numbered_line_is_a_heading_only_where_the_layout_says_so() -> (
    None
):
    # The made-up manual prints `3 Months after diagnosis` in section 2. The
    # layout model gave it no heading role, so it is no heading, and the
    # rules after it stay under section 2.
    assert any(item.text == "3 Months after diagnosis" for item in manual().paragraphs)

    chunks = cut_chunks(manual())

    assert [(chunk.rule_id, chunk.section_id) for chunk in chunks] == [
        (RULE_A, "2.4"),
        (RULE_B, "2.4"),
        (RULE_C, "3.4"),
    ]
    # A restart of the count at 1 is taken only before the first rule: the
    # contents page lists every section before the first one starts.
    with pytest.raises(ManualInvalid, match="section_number_taken"):
        cut_chunks(
            layout(
                ["1 Gout", "2 Asthma"],
                ["1 Gout", definition(RULE_A)],
                ["2 Asthma", definition(RULE_C)],
                ["1 Gout", "Printed again."],
            )
        )


def test_story_2_2_a_heading_joined_to_the_definition_below_it_is_still_a_heading() -> (
    None
):
    chunks = cut_chunks(
        layout(
            [f"1 Gout {definition(RULE_A)}"],
            ["2 Asthma", f"2.4 Probable rating {definition(RULE_C)}"],
        )
    )

    assert [
        (chunk.rule_id, chunk.impairment, chunk.section_id, chunk.text)
        for chunk in chunks
    ] == [
        (RULE_A, "Gout", "1", definition(RULE_A)),
        (RULE_C, "Asthma", "2.4", definition(RULE_C)),
    ]


def test_story_2_2_without_roles_a_repeating_footer_inside_a_definition_is_refused() -> (
    None
):
    # No role says what the footer is: it is known by standing on most pages.
    merged = without_roles(
        manual(first=f"{definition(RULE_A)} {FOOTER}"), "3 Months after diagnosis"
    )

    with pytest.raises(ManualInvalid) as raised:
        cut_chunks(merged)

    assert (raised.value.reason, raised.value.where) == (
        "page_furniture_in_chunk",
        RULE_A,
    )


def test_story_2_2_without_roles_a_line_on_too_few_pages_is_not_furniture() -> None:
    line = "A note that two pages of four happen to share."

    def pages(with_line: int) -> ParsedLayout:
        return layout(
            *[
                [
                    *([line] if page < with_line else []),
                    *(
                        ["1 Gout", definition(RULE_A, f"Mild. {line}")]
                        if page == 0
                        else []
                    ),
                    f"Text of page {page} alone.",
                ]
                for page in range(4)
            ],
            furniture=False,
        )

    # On two pages of four it is text like any other, and may be in a rule.
    (chunk,) = cut_chunks(pages(with_line=2))
    assert line in chunk.text
    # On every page it is furniture, and a rule that holds it is refused.
    with pytest.raises(ManualInvalid, match="page_furniture_in_chunk"):
        cut_chunks(pages(with_line=4))


def test_story_2_2_a_heading_or_a_definition_that_repeats_is_never_furniture() -> None:
    # Every section prints the same part heading: digits aside it is one
    # text on every page, and it is still a heading.
    rules = [RULE_A, RULE_C, "UW-CC-001", "UW-DD-001"]
    parsed = layout(
        *[
            [
                f"{number} Impairment {'ABCD'[number - 1]}",
                f"{number}.4 Probable rating",
                definition(rule, "The same words in every section."),
            ]
            for number, rule in enumerate(rules, start=1)
        ],
        furniture=False,
    )

    chunks = cut_chunks(parsed)

    assert [chunk.section_id for chunk in chunks] == ["1.4", "2.4", "3.4", "4.4"]
    assert [chunk.rule_id for chunk in chunks] == rules


def test_story_2_2_a_page_with_no_text_is_an_error_not_a_partial_index() -> None:
    parsed = manual()
    unread = replace(
        parsed,
        pages=(*parsed.pages[:2], LayoutPage(3, False), *parsed.pages[3:]),
    )

    with pytest.raises(ManualInvalid) as raised:
        cut_chunks(unread)

    assert (raised.value.reason, raised.value.where) == ("page_without_text", "3")
    # Pages that are missing, or a paragraph on a page there is not.
    with pytest.raises(ManualInvalid, match="pages_out_of_order"):
        cut_chunks(replace(parsed, pages=(parsed.pages[0], parsed.pages[2])))
    with pytest.raises(ManualInvalid, match="paragraph_outside_pages"):
        cut_chunks(
            replace(
                parsed,
                paragraphs=(*parsed.paragraphs, LayoutParagraph(99, "Somewhere.")),
            )
        )


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


def test_story_2_2_the_chat_model_is_shown_where_the_rule_sits_and_its_text(
    ports: IngestPorts, model: StubModel
) -> None:
    ingest(ports)

    first = cut_chunks(manual())[0]
    assert rule_in_its_place(first) == (
        "Section: 2 Raised blood sugar\n"
        "Part: 2.4 Probable rating\n"
        "Rule text:\n"
        f"{first.text}"
    )
    assert sorted(model.shown) == sorted(
        rule_in_its_place(chunk) for chunk in cut_chunks(manual())
    )


def test_story_2_2_the_embedded_text_is_the_context_line_followed_by_the_chunk_text(
    ports: IngestPorts, model: StubModel, repository: MemoryRepository
) -> None:
    model.answer = lambda shown: context_answer(
        f"Line for {shown.splitlines()[-1][:14]}"
    )

    ingest(ports)

    chunks = cut_chunks(manual())
    expected = [
        embedding_text(f"Line for {chunk.text[:14]}", chunk.text) for chunk in chunks
    ]
    assert expected[0] == f"Line for Rule {RULE_A}\n{chunks[0].text}"
    # One small call first, then batches of the configured size, every text once.
    assert model.embedded[0] == [EMBEDDING_PROBE]
    assert [len(batch) for batch in model.embedded[1:]] == [2, 1]
    assert [text for batch in model.embedded[1:] for text in batch] == expected
    # Each chunk got the vector of its own text.
    for chunk, text in zip(chunks, expected, strict=True):
        assert list(repository.records[chunk.chunk_id].embedding) == vector_for(text)


def test_story_2_2_a_second_run_changes_nothing_and_calls_no_model(
    ports: IngestPorts,
    repository: MemoryRepository,
    model: StubModel,
    layout_parser: FakeLayout,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ingest(ports)
    before = dict(repository.records)
    calls = model.calls

    with caplog.at_level(logging.INFO):
        report = ingest(ports)

    assert (report.written, report.moved, report.removed, report.unchanged) == (
        0,
        0,
        0,
        3,
    )
    # No chat call and no embedding call, and nothing written at all.
    assert model.calls == calls
    assert len(repository.applies) == 1
    assert repository.records == before
    # The same manual, prompt and deployments as the last run: the run ends
    # before the manual is sent to the layout model again, and says so.
    assert report.skipped is True
    assert layout_parser.parsed_pdfs == [PDF]
    digest = hashlib.sha256(PDF).hexdigest()
    assert f"manual unchanged since the last run: manual_sha256={digest} chunks=3" in (
        caplog.text
    )


def test_story_2_2_an_unchanged_manual_is_parsed_again_when_the_index_is_not_what_the_run_left(
    ports: IngestPorts, repository: MemoryRepository, layout_parser: FakeLayout
) -> None:
    ingest(ports)
    # A chunk went missing behind the job's back: the note of the last run
    # no longer describes what is stored.
    del repository.records[f"smart-{RULE_C}"]

    report = ingest(ports)

    assert (report.skipped, report.written, report.unchanged) == (False, 1, 2)
    assert len(layout_parser.parsed_pdfs) == 2
    assert sorted(repository.records) == [
        f"smart-{RULE_A}",
        f"smart-{RULE_B}",
        f"smart-{RULE_C}",
    ]


def test_story_2_2_a_changed_manual_replaces_what_changed_and_removes_what_is_gone(
    ports: IngestPorts,
    repository: MemoryRepository,
    model: StubModel,
    layout_parser: FakeLayout,
    manual_store: FakeManual,
) -> None:
    ingest(ports)
    before = dict(repository.records)
    model.shown.clear()
    model.embedded.clear()
    # One rule's text changed, one rule removed.
    manual_store.pdf = PDF + b" changed"
    layout_parser.parsed = manual(
        first=definition(RULE_A, "A new band altogether."), with_c=False
    )

    report = ingest(ports)

    assert (report.written, report.moved, report.removed, report.unchanged) == (
        1,
        0,
        1,
        1,
    )
    changed = repository.records[f"smart-{RULE_A}"]
    assert changed.chunk.text == f"Rule {RULE_A}: A new band altogether."
    assert changed.content_hash != before[f"smart-{RULE_A}"].content_hash
    assert changed.embedding != before[f"smart-{RULE_A}"].embedding
    # The removed rule's chunk is gone; the rest is untouched.
    assert f"smart-{RULE_C}" not in repository.records
    assert repository.records[f"smart-{RULE_B}"] is before[f"smart-{RULE_B}"]
    # One chat call and one embedding call, for the changed chunk only,
    # after the one small call that tries the embedding deployment.
    assert len(model.shown) == 1
    assert [len(batch) for batch in model.embedded] == [1, 1]
    # The note now names the changed manual.
    assert repository.run is not None
    assert repository.run.manual_sha256 == hashlib.sha256(manual_store.pdf).hexdigest()
    assert repository.run.chunk_count == 2
    # One transaction for the whole run.
    assert repository.applies[-1] == (
        [f"smart-{RULE_A}"],
        {},
        [f"smart-{RULE_C}"],
    )


def test_story_2_2_a_rule_that_only_moved_to_another_page_costs_no_model_call(
    ports: IngestPorts,
    repository: MemoryRepository,
    model: StubModel,
    layout_parser: FakeLayout,
    manual_store: FakeManual,
) -> None:
    ingest(ports)
    before = dict(repository.records)
    calls = model.calls
    manual_store.pdf = PDF + b" with a page more"
    layout_parser.parsed = manual(filler_pages=1)

    report = ingest(ports)

    assert (report.written, report.moved, report.unchanged) == (0, 3, 0)
    assert model.calls == calls
    for chunk_id, record in repository.records.items():
        assert record.chunk.manual_page == before[chunk_id].chunk.manual_page + 1
        assert record.embedding == before[chunk_id].embedding
        assert record.context_line == before[chunk_id].context_line


def test_story_2_2_another_deployment_or_prompt_writes_every_chunk_again(
    ports: IngestPorts, model: StubModel
) -> None:
    ingest(ports)

    # The hash holds what made the context line and the vector.
    assert ingest(ports, embedding_deployment="another-embedding").written == 3
    assert ingest(ports, embedding_deployment="another-embedding").written == 0
    assert ingest(ports, chat_deployment="another-chat").written == 3
    assert ingest(ports, prompt_digest="digest-2").written == 3


def test_story_2_2_a_renamed_section_writes_its_rules_again_and_nothing_else(
    ports: IngestPorts,
    repository: MemoryRepository,
    model: StubModel,
    layout_parser: FakeLayout,
    manual_store: FakeManual,
) -> None:
    ingest(ports)
    before = dict(repository.records)
    model.shown.clear()
    # The same rule text, under a section heading that was renamed.
    manual_store.pdf = PDF + b" renamed"
    layout_parser.parsed = manual(section_two="High blood sugar")

    report = ingest(ports)

    # The hash covers where the rule is printed: the two rules of that
    # section get a new context line and vector, and their new impairment.
    assert (report.written, report.moved, report.removed, report.unchanged) == (
        2,
        0,
        0,
        1,
    )
    assert repository.applies[-1] == ([f"smart-{RULE_A}", f"smart-{RULE_B}"], {}, [])
    for rule_id in (RULE_A, RULE_B):
        after = repository.records[f"smart-{rule_id}"]
        assert after.chunk.text == before[f"smart-{rule_id}"].chunk.text
        assert after.chunk.impairment == "High blood sugar"
        assert after.content_hash != before[f"smart-{rule_id}"].content_hash
    assert all("Section: 2 High blood sugar" in shown for shown in model.shown)
    assert len(model.shown) == 2
    # The rule of the other section is not touched.
    assert repository.records[f"smart-{RULE_C}"] is before[f"smart-{RULE_C}"]


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


def test_story_2_2_a_run_whose_index_another_run_changed_writes_nothing(
    ports: IngestPorts, repository: MemoryRepository
) -> None:
    ingest(ports)
    kept = dict(repository.records)

    def another_run_got_in_first() -> None:
        repository.records.pop(f"smart-{RULE_C}")

    repository.before_apply = another_run_got_in_first

    error = failure(ports, prompt_digest="digest-2")

    assert (error.code, error.reason) == (
        ErrorCode.IN_PROGRESS,
        "index_changed_by_another_run",
    )
    # What the other run left stands; this run's records are not there.
    assert len(repository.applies) == 1
    assert repository.records[f"smart-{RULE_A}"] is kept[f"smart-{RULE_A}"]


def test_story_2_2_the_plan_names_what_to_add_rewrite_move_and_remove() -> None:
    chunks = cut_chunks(manual())
    first, second, third = chunks
    stored = {
        # As stored, on another page.
        first.chunk_id: _stored(first, page=9),
        # Stored with other text.
        second.chunk_id: replace(_stored(second), content_hash="another"),
        # A rule the manual no longer defines.
        "smart-UW-ZZ-999": replace(_stored(third), chunk_id="smart-UW-ZZ-999"),
    }

    plan = plan_ingestion(chunks, stored, options())

    assert [chunk.rule_id for chunk in plan.write] == [RULE_B, RULE_C]
    assert [chunk.rule_id for chunk in plan.move] == [RULE_A]
    assert plan.remove == ("smart-UW-ZZ-999",)
    assert plan.unchanged == 0


def _stored(chunk: Chunk, page: int | None = None) -> StoredChunk:
    return StoredChunk(
        chunk.chunk_id, fingerprint(chunk, options()), page or chunk.manual_page
    )


# --- Failures: the index is untouched --------------------------------------------------------


def test_story_2_2_a_missing_manual_ends_the_run_saying_so(
    ports: IngestPorts,
    manual_store: FakeManual,
    layout_parser: FakeLayout,
    repository: MemoryRepository,
) -> None:
    manual_store.pdf = None

    error = failure(ports)

    assert (error.code, error.reason) == (ErrorCode.NOT_FOUND, "manual_missing")
    assert layout_parser.parsed_pdfs == []
    assert repository.applies == []


def test_story_2_2_the_store_is_read_before_anything_is_spent(
    ports: IngestPorts,
    manual_store: FakeManual,
    layout_parser: FakeLayout,
    model: StubModel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def down(chunk_set: object) -> None:
        raise RuntimeError("the store cannot be read")

    monkeypatch.setattr(ports.repository, "stored", down)

    with pytest.raises(RuntimeError):
        ingest(ports)

    # Neither the manual, the layout analysis nor a model was spent on a run
    # that could not have been stored.
    assert manual_store.reads == 0
    assert layout_parser.parsed_pdfs == []
    assert model.calls == 0


def test_story_2_2_a_layout_failure_leaves_the_index_untouched(
    ports: IngestPorts,
    layout_parser: FakeLayout,
    repository: MemoryRepository,
    model: StubModel,
    manual_store: FakeManual,
) -> None:
    ingest(ports)
    before = dict(repository.records)
    manual_store.pdf = PDF + b" changed"
    layout_parser.fail = "layout_timeout"

    error = failure(ports)

    assert (error.code, error.reason) == (
        ErrorCode.UPSTREAM_UNAVAILABLE,
        "layout_timeout",
    )
    assert repository.records == before
    assert len(repository.applies) == 1
    # The manual's bytes went to the layout model as they were read.
    assert layout_parser.parsed_pdfs == [PDF, PDF + b" changed"]


@pytest.mark.parametrize("failing", ["context_error", "embed_error"])
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
        (context_answer(""), "context_line_empty"),
        (context_answer("   "), "context_line_empty"),
        (context_answer("One line.\nAnd another."), "context_line_multi_line"),
        (context_answer("One line. And another."), "context_line_multi_line"),
        (context_answer("x" * 301), "context_line_too_long"),
        ("A sentence, not the object asked for.", "context_line_not_json"),
        ("", "context_line_not_json"),
        (json.dumps({"context_line": 7}), "context_line_not_text"),
        (json.dumps(["a line"]), "context_line_not_the_object"),
        (
            json.dumps({"context_line": "A line.", "more": 1}),
            "context_line_not_the_object",
        ),
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


def test_story_2_2_a_context_line_is_kept_as_the_model_wrote_it_but_for_its_ends() -> (
    None
):
    assert parse_context_line(context_answer("  A line. \n"), 300) == "A line."
    assert parse_context_line(context_answer("x" * 300), 300) == "x" * 300


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"dimensions": 1536}, "embedding_wrong_size"),
        ({"dimensions": EMBEDDING_DIMENSIONS + 1}, "embedding_wrong_size"),
        ({"vectors_per_call": 1}, "embedding_count_differs"),
    ],
)
def test_story_2_2_a_vector_of_the_wrong_size_is_refused(
    ports: IngestPorts,
    repository: MemoryRepository,
    model: StubModel,
    change: dict[str, int],
    reason: str,
) -> None:
    for name, value in change.items():
        setattr(model, name, value)

    error = failure(ports)

    assert (error.code, error.reason) == (ErrorCode.INVALID_MODEL_OUTPUT, reason)
    assert repository.applies == []
    if "dimensions" in change:
        # Seen on the one small call, before a hundred chat calls are spent.
        assert model.shown == []


def test_story_2_2_an_embedding_answer_the_gateway_cannot_match_is_invalid_output(
    ports: IngestPorts, model: StubModel, repository: MemoryRepository
) -> None:
    model.embed_error = ModelAnswerInvalid("embedding_index_invalid")

    error = failure(ports)

    assert (error.code, error.reason) == (
        ErrorCode.INVALID_MODEL_OUTPUT,
        "embedding_index_invalid",
    )
    assert repository.applies == []


def test_story_2_2_the_deadline_ends_the_work_but_never_the_storing(
    ports: IngestPorts,
    model: StubModel,
    repository: MemoryRepository,
    layout_parser: FakeLayout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow(shown: str) -> str:
        await asyncio.sleep(5)
        return context_answer()

    answered = model.context_line
    monkeypatch.setattr(model, "context_line", slow)

    late = failure(ports, deadline_seconds=0.02)

    assert (late.code, late.reason) == (ErrorCode.STAGE_TIMEOUT, "ingest_deadline")
    assert repository.applies == []
    # A time-out of something a port called is not the job's deadline.
    monkeypatch.setattr(model, "context_line", answered)

    async def timed_out(pdf: bytes) -> ParsedLayout:
        raise TimeoutError("a socket of the layout client")

    parse = layout_parser.parse
    monkeypatch.setattr(layout_parser, "parse", timed_out)
    with pytest.raises(TimeoutError, match="a socket"):
        ingest(ports, deadline_seconds=30.0)
    monkeypatch.setattr(layout_parser, "parse", parse)
    # The one transaction at the end is outside the deadline: a store that
    # takes longer than what is left of it is still let finish.
    stored = repository.apply

    async def slow_store(*arguments: Any, **named: Any) -> None:
        await asyncio.sleep(0.2)
        await stored(*arguments, **named)

    monkeypatch.setattr(repository, "apply", slow_store)

    report = ingest(ports, deadline_seconds=0.15)

    assert report.written == 3
    assert len(repository.applies) == 1


def test_story_2_2_a_vector_that_is_not_numbers_is_refused(
    ports: IngestPorts, model: StubModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def not_numbers(texts: Any) -> list[list[Any]]:
        return [[float("nan")] * EMBEDDING_DIMENSIONS for _ in texts]

    monkeypatch.setattr(model, "embed", not_numbers)

    assert failure(ports).reason == "embedding_not_numbers"


def test_story_2_2_a_manual_that_cannot_be_cut_fails_the_run_with_its_reason(
    ports: IngestPorts, layout_parser: FakeLayout, repository: MemoryRepository
) -> None:
    layout_parser.parsed = layout(
        ["1 Gout", definition(RULE_A)], [definition(RULE_A, "Again.")]
    )

    error = failure(ports)

    assert (error.code, error.reason, error.where) == (
        ErrorCode.STAGE_FAILED,
        "rule_defined_twice",
        RULE_A,
    )
    assert repository.applies == []


def test_story_2_2_a_store_that_fails_fails_the_run(
    ports: IngestPorts, repository: MemoryRepository
) -> None:
    repository.fail_apply = True

    with pytest.raises(Exception, match="secret-store-detail"):
        ingest(ports)

    assert repository.records == {}


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
