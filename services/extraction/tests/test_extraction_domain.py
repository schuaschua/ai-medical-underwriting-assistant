"""Story 2.4: the extraction of one page's facts and the quote check, on fakes.

Unit tests of the domain: no database, no `intake` and no model. The model is
a gateway stub (coding-style rule 23).
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta

import pytest
from extraction_fakes import (
    ACTOR,
    PAGE_TEXT,
    STATEMENT,
    TRACE_ID,
    FakePages,
    MemoryRepository,
    StubModel,
    answer,
    fact,
    unavailable,
)

from contracts.audit import AuditAction
from contracts.enums import ActorKind, StageStatus
from contracts.errors import NO_TRACE_ID, DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.extraction import (
    ExtractedFact,
    ExtractFactsCommand,
    FactSetResult,
)
from contracts.text import normalise
from extraction.domain.entities import FactSetKey, PageReading
from extraction.domain.extract import (
    ExtractOptions,
    ExtractPorts,
    check_facts,
    extract_facts,
    is_masked_value,
    list_facts,
)

SECRETS = ("SECRET", "HbA1c", "7.4", "[Person]", "glucose")


def extract(
    case_id: str,
    page_id: str,
    ports: ExtractPorts,
    options: ExtractOptions,
    now: datetime,
    **changes: object,
) -> FactSetResult:
    command = ExtractFactsCommand.model_validate(
        {"case_id": case_id, "page_id": page_id, **changes}
    )
    return asyncio.run(
        extract_facts(
            command, ports=ports, options=options, trace_id=TRACE_ID, now=lambda: now
        )
    )


def refused(
    case_id: str,
    page_id: str,
    ports: ExtractPorts,
    options: ExtractOptions,
    now: datetime,
) -> DomainError:
    with pytest.raises(DomainError) as raised:
        extract(case_id, page_id, ports, options, now)
    return raised.value


# --- The quote check ---------------------------------------------------------------------


PAGE = PageReading(page_number=3, text=PAGE_TEXT)
KEY = FactSetKey(new_id(), new_id())


def checked(*proposals: dict[str, str]) -> list[dict[str, object]]:
    facts = check_facts(
        [ExtractedFact.model_validate(proposal) for proposal in proposals], PAGE, KEY
    ).facts
    return [item.model_dump() for item in facts]


@pytest.mark.parametrize(
    "quote",
    ["hba1c   7.4 %", "HBA1C\n7.4\n%"],
)
def test_story_2_4_a_quote_found_on_the_page_is_verified_with_offsets_into_the_stored_text(
    quote: str,
) -> None:
    (found,) = checked(fact(quote=quote))

    assert found["quote_verified"] is True
    start, end = found["quote_start"], found["quote_end"]
    assert isinstance(start, int) and isinstance(end, int)
    # The page text between the offsets, normalised, is the normalised quote.
    assert normalise(PAGE_TEXT[start:end]) == normalise(quote)
    # The quote is kept as the model gave it.
    assert found["quote"] == quote


def test_story_2_4_a_quote_that_is_not_on_the_page_is_kept_flagged_and_given_no_offsets() -> (
    None
):
    (missing,) = checked(fact("HbA1c 9.9 %", "HbA1c 9.9 %"))

    assert missing["quote_verified"] is False
    assert (missing["quote_start"], missing["quote_end"]) == (None, None)
    assert missing["statement"] == "HbA1c 9.9 %"


def test_story_2_4_a_quote_across_table_cells_is_verified_only_in_the_order_stored() -> (
    None
):
    in_order, out_of_order = check_facts(
        [
            ExtractedFact(statement="x", quote="Fasting plasma glucose 142 mg/dL"),
            ExtractedFact(statement="x", quote="142 Fasting plasma glucose mg/dL"),
        ],
        PAGE,
        KEY,
    ).facts

    assert in_order.quote_verified is True
    assert PAGE_TEXT[in_order.quote_start : in_order.quote_end] == (
        "Fasting plasma glucose\n142\nmg/dL"
    )
    # Never a guess: the words are on the page, but not as quoted.
    assert out_of_order.quote_verified is False
    assert (out_of_order.quote_start, out_of_order.quote_end) == (None, None)


@pytest.mark.parametrize(
    "proposal",
    [
        fact("Name: [Person]", "Patient name [Person]"),
        fact("Contact details", "[Person], [PhoneNumber]."),
    ],
)
def test_story_2_4_a_masked_value_is_never_a_fact(proposal: dict[str, str]) -> None:
    assert is_masked_value(ExtractedFact.model_validate(proposal))

    result = check_facts(
        [ExtractedFact.model_validate(item) for item in (proposal, fact())], PAGE, KEY
    )

    assert [item.statement for item in result.facts] == [STATEMENT]
    assert result.masked_left_out == 1


def test_story_2_4_a_quote_with_a_mask_token_beside_text_is_checked_like_any_other() -> (
    None
):
    page = PageReading(1, "Remarks\n[Person] has type 2 diabetes, on metformin")
    proposal = ExtractedFact(
        statement="Type 2 diabetes", quote="[Person] has type 2 diabetes"
    )

    (stored,) = check_facts([proposal], page, KEY).facts

    assert stored.quote_verified is True
    assert page.text[stored.quote_start : stored.quote_end] == (
        "[Person] has type 2 diabetes"
    )


# --- The extract operation ---------------------------------------------------------------


def test_story_2_4_a_page_is_extracted_into_facts_with_a_done_result_and_its_audit_record(
    case_id: str,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    ports: ExtractPorts,
    options: ExtractOptions,
    fixed_now: datetime,
) -> None:
    pages.add(case_id, "Another page")
    page_id = pages.add(case_id)
    model.answers = [answer(fact(), fact("HbA1c 9.9 %", "HbA1c 9.9 %"))]
    eval_run_id = new_id()

    result = extract(
        case_id, page_id, ports, options, fixed_now, eval_run_id=eval_run_id
    )

    assert result.status is StageStatus.DONE
    assert result.error_code is None
    assert (result.case_id, result.page_id) == (case_id, page_id)
    assert result.unverified_count == 1
    stored = asyncio.run(list_facts(case_id, repository=repository)).facts
    assert [item.fact_id for item in stored] == result.fact_ids
    assert [item.quote_verified for item in stored] == [True, False]
    # The page's number is `intake`'s, never the model's.
    assert {item.page_number for item in stored} == {2}
    assert all(item.statement and item.quote for item in stored)
    # AD-8: who did it, to what, and under which record.
    audit = result.audit
    assert audit.action is AuditAction.FACTS_EXTRACTED
    assert (audit.actor_kind, audit.actor) == (ActorKind.AI, ACTOR)
    assert (audit.case_id, audit.page_id, audit.ref) == (
        case_id,
        page_id,
        result.fact_set_id,
    )
    assert (audit.trace_id, audit.eval_run_id) == (TRACE_ID, eval_run_id)
    assert audit.occurred_at == fixed_now
    assert audit.detail is None
    # One model answer per page, on the text `intake` stores and nothing else.
    assert model.texts == [PAGE_TEXT]
    assert pages.reads == [page_id]


def test_story_2_4_a_masked_value_is_not_stored_and_is_counted_in_the_log(
    case_id: str,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    ports: ExtractPorts,
    options: ExtractOptions,
    fixed_now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    page_id = pages.add(case_id)
    model.answers = [answer(fact("Name: [Person]", "Patient name [Person]"), fact())]

    with caplog.at_level(logging.INFO):
        result = extract(case_id, page_id, ports, options, fixed_now)

    assert len(result.fact_ids) == 1
    assert [item.statement for item in repository.stored] == [STATEMENT]
    assert "proposed=2 facts=1 unverified=0 masked_left_out=1" in caplog.text


@pytest.mark.parametrize(
    "bad_answer",
    [
        "The page mentions diabetes, I think.",
        json.dumps({"facts": [fact()], "fact_set_id": "x"}),
    ],
)
def test_story_2_4_an_answer_that_is_not_the_contracts_shape_fails_the_page_and_stores_nothing(
    bad_answer: str,
    case_id: str,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    ports: ExtractPorts,
    options: ExtractOptions,
    fixed_now: datetime,
) -> None:
    page_id = pages.add(case_id)
    model.answers = [bad_answer]

    result = extract(case_id, page_id, ports, options, fixed_now)

    assert (result.status, result.error_code) == (
        StageStatus.FAILED,
        ErrorCode.INVALID_MODEL_OUTPUT,
    )
    assert result.fact_ids == []
    assert result.audit.action is AuditAction.STAGE_FAILED
    assert (result.audit.page_id, result.audit.ref) == (page_id, result.fact_set_id)
    assert repository.stored == []


@pytest.mark.parametrize(
    "not_a_fact",
    [
        {"statement": "HbA1c 7.4 %"},
        # The model never decides verification, offsets, page numbers or ids.
        {**fact(), "quote_verified": True},
    ],
)
def test_story_2_4_one_proposal_that_cannot_be_a_fact_is_left_out_and_counted_not_the_page_failed(
    not_a_fact: object,
    case_id: str,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    ports: ExtractPorts,
    options: ExtractOptions,
    fixed_now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    page_id = pages.add(case_id)
    model.answers = [json.dumps({"facts": [fact(), not_a_fact, fact("Glucose 142")]})]

    with caplog.at_level(logging.INFO):
        result = extract(case_id, page_id, ports, options, fixed_now)

    # The page is done with the two proposals that are facts; the third is
    # counted in the log, as a masked value is.
    assert (result.status, len(result.fact_ids)) == (StageStatus.DONE, 2)
    assert [item.statement for item in repository.stored] == [STATEMENT, "Glucose 142"]
    assert "proposed=3 facts=2 unverified=0 masked_left_out=0 not_facts_left_out=1" in (
        caplog.text
    )
    assert "SECRET" not in caplog.text


def test_story_2_4_a_model_that_cannot_be_had_fails_the_page_with_model_unavailable(
    case_id: str,
    pages: FakePages,
    model: StubModel,
    ports: ExtractPorts,
    options: ExtractOptions,
    fixed_now: datetime,
) -> None:
    page_id = pages.add(case_id)
    model.answers = [unavailable()]

    result = extract(case_id, page_id, ports, options, fixed_now)

    assert (result.status, result.error_code) == (
        StageStatus.FAILED,
        ErrorCode.MODEL_UNAVAILABLE,
    )


def test_story_2_4_the_deadline_ends_the_work_as_stage_timeout(
    case_id: str,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    ports: ExtractPorts,
    fixed_now: datetime,
) -> None:
    page_id = pages.add(case_id)
    options = ExtractOptions(actor=ACTOR, deadline_seconds=0.01)

    async def scenario() -> FactSetResult:
        # The model never answers: only the deadline ends the call.
        model.hold = asyncio.Event()
        return await extract_facts(
            ExtractFactsCommand(case_id=case_id, page_id=page_id),
            ports=ports,
            options=options,
            now=lambda: fixed_now,
        )

    result = asyncio.run(scenario())

    assert (result.status, result.error_code) == (
        StageStatus.FAILED,
        ErrorCode.STAGE_TIMEOUT,
    )
    assert result.audit.trace_id == NO_TRACE_ID
    assert repository.stored == []


def test_story_2_4_a_repeat_while_the_page_is_being_extracted_is_in_progress(
    case_id: str,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    ports: ExtractPorts,
    options: ExtractOptions,
    fixed_now: datetime,
) -> None:
    page_id = pages.add(case_id)
    command = ExtractFactsCommand(case_id=case_id, page_id=page_id)

    async def scenario() -> tuple[DomainError, FactSetResult]:
        model.hold = asyncio.Event()
        first = asyncio.create_task(
            extract_facts(command, ports=ports, options=options, now=lambda: fixed_now)
        )
        while model.calls == 0:
            await asyncio.sleep(0)
        with pytest.raises(DomainError) as raised:
            await extract_facts(
                command, ports=ports, options=options, now=lambda: fixed_now
            )
        model.hold.set()
        return raised.value, await first

    error, result = asyncio.run(scenario())

    assert error.code is ErrorCode.IN_PROGRESS
    assert error.http_status == 409
    assert result.status is StageStatus.DONE
    # The repeat did no work of its own.
    assert model.calls == 1


def test_story_2_4_a_failed_result_is_repeated_too_and_the_page_is_not_tried_again(
    case_id: str,
    pages: FakePages,
    model: StubModel,
    ports: ExtractPorts,
    options: ExtractOptions,
    fixed_now: datetime,
) -> None:
    page_id = pages.add(case_id)
    model.answers = ["not json", answer()]

    first = extract(case_id, page_id, ports, options, fixed_now)
    again = extract(case_id, page_id, ports, options, fixed_now)

    assert first.status is StageStatus.FAILED
    assert again == first
    assert model.calls == 1


def test_story_2_4_a_key_row_left_running_by_a_dead_process_is_taken_over_and_the_page_extracted(
    case_id: str,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    ports: ExtractPorts,
    options: ExtractOptions,
    fixed_now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    page_id = pages.add(case_id)
    key = FactSetKey(case_id, page_id)
    fact_set_id = new_id()
    asyncio.run(repository.begin(fact_set_id, key, fixed_now))
    margin = timedelta(seconds=options.deadline_seconds + options.stale_margin_seconds)

    still_running = refused(case_id, page_id, ports, options, fixed_now + margin)
    with caplog.at_level(logging.WARNING):
        done = extract(
            case_id, page_id, ports, options, fixed_now + margin + timedelta(seconds=1)
        )

    assert still_running.code is ErrorCode.IN_PROGRESS
    # Extraction has no side effect before it finishes: the work is done
    # again, under the id the key row was given, and the case does not fail.
    assert (done.status, done.error_code) == (StageStatus.DONE, None)
    assert done.fact_set_id == fact_set_id
    assert repository.taken_over == [fact_set_id]
    assert (model.calls, len(repository.stored)) == (1, 1)
    assert "extraction taken over" in caplog.text
    # The page was the case's when the row was begun: it is not asked again.
    assert pages.listings == []


def test_story_2_4_when_a_stale_rows_first_owner_finishes_after_all_one_result_stands(
    case_id: str,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    ports: ExtractPorts,
    options: ExtractOptions,
    fixed_now: datetime,
) -> None:
    page_id = pages.add(case_id)
    command = ExtractFactsCommand(case_id=case_id, page_id=page_id)
    late = fixed_now + timedelta(hours=1)

    async def scenario() -> tuple[FactSetResult, FactSetResult]:
        model.hold = asyncio.Event()
        first = asyncio.create_task(
            extract_facts(command, ports=ports, options=options, now=lambda: fixed_now)
        )
        while model.calls == 0:
            await asyncio.sleep(0)
        # A repeat long after the deadline takes the row over, while its
        # first owner, thought dead, is in fact still at it.
        second = asyncio.create_task(
            extract_facts(command, ports=ports, options=options, now=lambda: late)
        )
        while model.calls == 1:
            await asyncio.sleep(0)
        model.hold.set()
        return await first, await second

    first, second = asyncio.run(scenario())

    # Both finish; the row is settled once, and both answer with that result.
    assert first == second
    assert first.status is StageStatus.DONE
    assert len(repository.stored) == 1
    assert [item.fact_id for item in repository.stored] == first.fact_ids


def test_story_2_4_logs_carry_ids_codes_and_counts_and_never_text_quotes_or_statements(
    case_id: str,
    pages: FakePages,
    model: StubModel,
    ports: ExtractPorts,
    options: ExtractOptions,
    fixed_now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    done_page = pages.add(case_id)
    bad_page = pages.add(case_id)
    model.answers = [
        answer(fact(), fact("Name: [Person]", "[Person]"), fact("x", "not there")),
        f"{PAGE_TEXT} is not JSON",
    ]

    with caplog.at_level(logging.DEBUG):
        extract(case_id, done_page, ports, options, fixed_now)
        extract(case_id, done_page, ports, options, fixed_now)
        extract(case_id, bad_page, ports, options, fixed_now)

    assert done_page in caplog.text
    assert "error_code=invalid_model_output" in caplog.text
    for secret in SECRETS:
        assert secret not in caplog.text
