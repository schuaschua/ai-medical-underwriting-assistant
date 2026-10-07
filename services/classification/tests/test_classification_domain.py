"""Story 1.8: classify one page, with a confidence and a reason.

Unit tests of the domain: the agreement rule and the classify operation, with
in-memory stand-ins and the gateway stub. No database, no model, no network.
"""

import ast
import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from classification_fakes import (
    ACTOR,
    PAGE_TEXT,
    PNG,
    REASON,
    TRACE_ID,
    FakePages,
    MemoryRepository,
    StubModel,
    answer,
    unavailable,
)

from classification.domain.agreement import agree
from classification.domain.classify import (
    ClassifyOptions,
    ClassifyPorts,
    classify_page,
    list_classifications,
)
from classification.domain.entities import ClassificationKey
from classification.domain.ports import ModelCallFailed
from contracts.enums import ClassifierContender, PageType, StageStatus
from contracts.errors import DomainError, ErrorCode
from contracts.ids import is_uuid7, new_id
from contracts.models.classification import (
    ClassificationResult,
    ClassifierOutput,
    ClassifyCommand,
)
from contracts.rules import MEDICAL_PAGE_TYPES, is_medical

DOMAIN_DIR = Path(__file__).resolve().parents[1] / "src" / "classification" / "domain"


def command(case_id: str, page_id: str, **changes: Any) -> ClassifyCommand:
    return ClassifyCommand.model_validate(
        {"case_id": case_id, "page_id": page_id, "contender": "llm", **changes}
    )


def run(
    command_: ClassifyCommand,
    ports: ClassifyPorts,
    options: ClassifyOptions,
    **more: Any,
) -> ClassificationResult:
    return asyncio.run(classify_page(command_, ports=ports, options=options, **more))


def key_of(command_: ClassifyCommand) -> ClassificationKey:
    return ClassificationKey(command_.case_id, command_.page_id, command_.contender)


def runs(*page_types: str) -> list[ClassifierOutput]:
    return [
        ClassifierOutput(page_type=PageType(page_type), reason=f"reason {number}")
        for number, page_type in enumerate(page_types, start=1)
    ]


# --- The agreement rule ----------------------------------------------------------------


def test_story_1_8_when_every_run_agrees_the_confidence_is_one() -> None:
    agreement = agree(runs(*["lab_report"] * 5))

    assert (agreement.page_type, agreement.confidence) == (PageType.LAB_REPORT, 1.0)
    assert agreement.agreeing_runs == 5


def test_story_1_8_confidence_is_the_share_of_runs_that_name_the_most_frequent_type() -> (
    None
):
    agreement = agree(runs("other", "invoice", "invoice", "other", "invoice"))

    # Three of five runs: 0.6, and an invoice is not medical.
    assert (agreement.page_type, agreement.confidence) == (PageType.INVOICE, 0.6)
    assert is_medical(agreement.page_type) is False
    # The reason is one of the agreeing runs': the first of them.
    assert agreement.reason == "reason 2"


@pytest.mark.parametrize(
    ("named", "winner", "confidence"),
    [
        # Two types named equally often: the one that comes first in the
        # contracts' `PageType` order, whatever the order of the runs.
        (("other", "invoice", "invoice", "other"), "invoice", 0.5),
        (("invoice", "other", "other", "invoice"), "invoice", 0.5),
        (("id_document", "lab_report"), "lab_report", 0.5),
        (("other", "invoice", "application_form"), "application_form", 1 / 3),
        (("invoice",), "invoice", 1.0),
    ],
)
def test_story_1_8_a_tie_is_settled_by_the_order_of_the_page_types(
    named: tuple[str, ...], winner: str, confidence: float
) -> None:
    agreement = agree(runs(*named))

    assert agreement.page_type.value == winner
    assert agreement.confidence == pytest.approx(confidence)
    # Deterministic: the same runs, the same answer.
    assert agree(runs(*named)) == agreement
    # The order the rule uses is the one the contracts declare.
    assert [page_type.value for page_type in PageType] == [
        "lab_report",
        "attending_physician_statement",
        "application_form",
        "id_document",
        "invoice",
        "other",
    ]


def test_story_1_8_there_is_no_agreement_without_a_run() -> None:
    with pytest.raises(ValueError, match="without a run"):
        agree([])


# --- Classify a page ---------------------------------------------------------------------


def test_story_1_8_a_page_is_classified_with_type_medical_confidence_reason_and_contender(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
    fixed_now: datetime,
) -> None:
    page_id = pages.add(case_id)
    eval_run_id = new_id()

    result = run(
        command(case_id, page_id, eval_run_id=eval_run_id),
        ports,
        options,
        trace_id=TRACE_ID,
        now=lambda: fixed_now,
    )

    assert (result.status, result.error_code) == (StageStatus.DONE, None)
    classification = result.classification
    assert classification is not None
    assert classification.model_dump(mode="json") == {
        "classification_id": result.classification_id,
        "case_id": case_id,
        "page_id": page_id,
        "contender": "llm",
        "page_type": "lab_report",
        "is_medical": True,
        "confidence": 1.0,
        "reason": REASON,
    }
    assert is_uuid7(result.classification_id)
    # AD-8: `page.classified`, by the service and the model deployment, about
    # the page, pointing at the classification, with no detail.
    assert result.audit.model_dump(mode="json") == {
        "actor_kind": "ai",
        "actor": ACTOR,
        "action": "page.classified",
        "occurred_at": "2026-10-07T12:00:00Z",
        "case_id": case_id,
        "page_id": page_id,
        "ref": result.classification_id,
        "detail": None,
        "trace_id": TRACE_ID,
        "eval_run_id": eval_run_id,
    }
    # The model was run five times on the one page, as `intake` holds it:
    # its text and its picture, and nothing else.
    assert model.calls == 5
    assert {(page.text, page.image) for page in model.pages} == {(PAGE_TEXT, PNG)}
    assert pages.reads == [page_id]
    # Stored, and the stored result is the one that was answered.
    assert repository.result_of(key_of(command(case_id, page_id))) == result
    listed = asyncio.run(list_classifications(case_id, repository=repository))
    assert listed.classifications == [classification]


def test_story_1_8_five_agreeing_runs_give_confidence_one(
    ports: ClassifyPorts, options: ClassifyOptions, pages: FakePages, case_id: str
) -> None:
    page_id = pages.add(case_id)

    result = run(command(case_id, page_id), ports, options)

    assert result.classification is not None
    assert result.classification.confidence == 1.0


def test_story_1_8_three_runs_against_two_give_the_majority_type_at_confidence_0_6(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    case_id: str,
) -> None:
    page_id = pages.add(case_id)
    model.answers = [
        answer("other", "a payslip"),
        answer("invoice", "amounts to pay"),
        answer("invoice", "an invoice number"),
        answer("other", "no heading"),
        answer("invoice", "a total due"),
    ]

    result = run(command(case_id, page_id), ports, options)

    classification = result.classification
    assert classification is not None
    assert (classification.page_type.value, classification.confidence) == (
        "invoice",
        0.6,
    )
    assert classification.is_medical is False
    # One of the agreeing runs' reasons, never a dissenting one's.
    assert classification.reason in {
        "amounts to pay",
        "an invoice number",
        "a total due",
    }


@pytest.mark.parametrize("page_type", list(PageType))
def test_story_1_8_medical_or_not_comes_from_the_one_mapping_never_from_the_model(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    case_id: str,
    page_type: PageType,
) -> None:
    page_id = pages.add(case_id)
    model.answers = [answer(page_type.value)]

    result = run(command(case_id, page_id), ports, options)

    assert result.classification is not None
    assert result.classification.is_medical is (page_type in MEDICAL_PAGE_TYPES)
    # The model is never asked: an answer that says so itself is not valid.
    assert "is_medical" not in ClassifierOutput.model_fields


def test_story_1_8_the_number_of_runs_is_a_setting_and_they_run_a_bounded_number_at_once(
    ports: ClassifyPorts, pages: FakePages, model: StubModel, case_id: str
) -> None:
    page_id = pages.add(case_id)
    options = ClassifyOptions(actor=ACTOR, runs=7, max_concurrent_runs=3)

    result = run(command(case_id, page_id), ports, options)

    assert model.calls == 7
    # Concurrently, and never more than the setting allows.
    assert model.most_running == 3
    assert result.classification is not None
    assert result.classification.confidence == 1.0
    # The default is five runs, all at once.
    defaults = ClassifyOptions(actor=ACTOR)
    assert (defaults.runs, defaults.max_concurrent_runs) == (5, 5)
    assert (defaults.deadline_seconds, defaults.stale_margin_seconds) == (180.0, 60.0)


# --- Idempotency (AD-6) ----------------------------------------------------------------


def test_story_1_8_a_repeat_after_the_end_answers_the_stored_result_without_a_model_call(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
) -> None:
    page_id = pages.add(case_id)
    first = run(command(case_id, page_id), ports, options)
    calls, reads, listings = model.calls, len(pages.reads), len(pages.listings)
    # Whatever the model would say now, it is not asked.
    model.answers = [answer("invoice", "changed its mind")]

    again = run(command(case_id, page_id, eval_run_id=new_id()), ports, options)

    assert again == first
    assert (model.calls, len(pages.reads), len(pages.listings)) == (
        calls,
        reads,
        listings,
    )
    assert len(repository.rows) == 1
    listed = asyncio.run(list_classifications(case_id, repository=repository))
    assert len(listed.classifications) == 1


def test_story_1_8_a_repeat_after_a_failed_end_answers_the_stored_failure(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    case_id: str,
) -> None:
    page_id = pages.add(case_id)
    model.answers = [unavailable()]
    first = run(command(case_id, page_id), ports, options)
    calls = model.calls
    model.answers = [answer()]

    again = run(command(case_id, page_id), ports, options)

    # The key is settled: the page is not classified a second time.
    assert again == first
    assert again.status is StageStatus.FAILED
    assert model.calls == calls


def test_story_1_8_a_repeat_while_running_is_in_progress(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
) -> None:
    page_id = pages.add(case_id)

    async def scenario() -> tuple[DomainError, ClassificationResult, int]:
        model.hold = asyncio.Event()
        first = asyncio.create_task(
            classify_page(command(case_id, page_id), ports=ports, options=options)
        )
        while model.calls < 5:
            await asyncio.sleep(0)
        # The key row was inserted as running before any work.
        (row,) = repository.rows.values()
        assert row.running
        with pytest.raises(DomainError) as raised:
            await classify_page(command(case_id, page_id), ports=ports, options=options)
        calls_during = model.calls
        model.hold.set()
        return raised.value, await first, calls_during

    error, result, calls_during = asyncio.run(scenario())

    assert (error.code, error.http_status) == (ErrorCode.IN_PROGRESS, 409)
    # The repeat started no run of its own, and the first went on to its end.
    assert calls_during == 5
    assert result.status is StageStatus.DONE
    assert len(repository.rows) == 1


def test_story_1_8_commands_that_arrive_together_classify_the_page_once(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
) -> None:
    page_id = pages.add(case_id)

    async def scenario() -> list[Any]:
        return await asyncio.gather(
            *(
                classify_page(command(case_id, page_id), ports=ports, options=options)
                for _ in range(4)
            ),
            return_exceptions=True,
        )

    outcomes = asyncio.run(scenario())

    done = [item for item in outcomes if isinstance(item, ClassificationResult)]
    refused = [item for item in outcomes if isinstance(item, DomainError)]
    assert len(done) == 1
    assert [error.code for error in refused] == [ErrorCode.IN_PROGRESS] * 3
    assert model.calls == 5
    assert len(repository.rows) == 1


def test_story_1_8_the_key_is_case_page_and_contender(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    repository: MemoryRepository,
    case_id: str,
) -> None:
    first_page, second_page = pages.add(case_id), pages.add(case_id)

    first = run(command(case_id, first_page), ports, options)
    second = run(command(case_id, second_page), ports, options)

    # Another page of the case is another classification, with an id of its own.
    assert first.classification_id != second.classification_id
    assert set(repository.rows) == {
        ClassificationKey(case_id, first_page, ClassifierContender.LLM),
        ClassificationKey(case_id, second_page, ClassifierContender.LLM),
    }


# --- Failures: stored, and never passed on ------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        # An unknown page type.
        json.dumps({"page_type": "utility_bill", "reason": "a bill"}),
        # Text around the object.
        'Here is my answer: {"page_type": "lab_report", "reason": "a table"}',
        '{"page_type": "lab_report", "reason": "a table"} I hope this helps.',
        # No JSON at all, and no answer at all.
        "This is a laboratory report.",
        "",
        # Not the object that was asked for.
        json.dumps({"page_type": "lab_report"}),
        json.dumps({"page_type": "lab_report", "reason": ""}),
        json.dumps({"page_type": "lab_report", "reason": "one\ntwo"}),
        json.dumps({"page_type": "lab_report", "reason": "r", "is_medical": False}),
        json.dumps({"page_type": "lab_report", "reason": "r", "confidence": 0.99}),
        json.dumps([{"page_type": "lab_report", "reason": "r"}]),
        "null",
    ],
    ids=[
        "unknown-page-type",
        "text-before",
        "text-after",
        "no-json",
        "empty",
        "no-reason",
        "blank-reason",
        "two-line-reason",
        "says-medical-itself",
        "says-confidence-itself",
        "a-list",
        "null",
    ],
)
def test_story_1_8_an_answer_that_fails_validation_fails_the_stage_and_is_never_passed_on(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
    bad: str,
) -> None:
    page_id = pages.add(case_id)
    # Four good runs and one bad one: the bad one is not out-voted.
    model.answers = [answer(), answer(), bad, answer(), answer()]

    result = run(command(case_id, page_id), ports, options)

    assert (result.status, result.error_code) == (
        StageStatus.FAILED,
        ErrorCode.INVALID_MODEL_OUTPUT,
    )
    assert result.classification is None
    # A `stage.failed` record for the page, by the same actor.
    assert (result.audit.action.value, result.audit.page_id, result.audit.actor) == (
        "stage.failed",
        page_id,
        ACTOR,
    )
    assert result.audit.ref == result.classification_id
    # Nothing is stored as a classification, and the read lists none.
    assert repository.stored == {}
    listed = asyncio.run(list_classifications(case_id, repository=repository))
    assert listed.classifications == []
    assert repository.result_of(key_of(command(case_id, page_id))) == result


def test_story_1_8_a_model_that_is_unavailable_fails_the_stage_as_model_unavailable(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
) -> None:
    page_id = pages.add(case_id)
    # The gateway gave up after its retries, on one of the runs.
    model.answers = [answer(), unavailable()]

    result = run(command(case_id, page_id), ports, options)

    assert (result.status, result.error_code) == (
        StageStatus.FAILED,
        ErrorCode.MODEL_UNAVAILABLE,
    )
    assert result.classification is None
    assert repository.stored == {}


def test_story_1_8_a_run_that_fails_stops_the_runs_still_under_way(
    ports: ClassifyPorts, pages: FakePages, model: StubModel, case_id: str
) -> None:
    page_id = pages.add(case_id)
    model.answers = [unavailable()]
    options = ClassifyOptions(actor=ACTOR, runs=10, max_concurrent_runs=2)

    result = run(command(case_id, page_id), ports, options)

    assert result.error_code is ErrorCode.MODEL_UNAVAILABLE
    # The runs that were waiting for their turn were never started.
    assert model.calls < 10
    assert model.running == 0


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ModelCallFailed("model_status_401"), "reason=model_status_401"),
        (RuntimeError("secret detail of the failure"), "reason=RuntimeError"),
    ],
)
def test_story_1_8_any_other_failure_of_a_run_fails_the_stage_as_stage_failed(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    case_id: str,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    reason: str,
) -> None:
    page_id = pages.add(case_id)
    model.answers = [error]

    with caplog.at_level(logging.INFO):
        result = run(command(case_id, page_id), ports, options)

    assert (result.status, result.error_code) == (
        StageStatus.FAILED,
        ErrorCode.STAGE_FAILED,
    )
    # A code or the error's type, never its message (security rule 31).
    assert reason in caplog.text
    assert "secret detail" not in caplog.text


def test_story_1_8_the_stage_ends_itself_at_its_deadline_as_stage_timeout(
    ports: ClassifyPorts,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
) -> None:
    page_id = pages.add(case_id)
    # The model never answers.
    model.hold = asyncio.Event()
    options = ClassifyOptions(actor=ACTOR, deadline_seconds=0.05)

    result = run(command(case_id, page_id), ports, options)

    assert (result.status, result.error_code) == (
        StageStatus.FAILED,
        ErrorCode.STAGE_TIMEOUT,
    )
    # One deadline for all runs of the page together; none is left running.
    assert model.calls == 5
    assert model.running == 0
    assert repository.result_of(key_of(command(case_id, page_id))) == result
    assert repository.stored == {}
    # AD-6: the setting defaults to 180 seconds.
    assert ClassifyOptions(actor=ACTOR).deadline_seconds == 180.0


def test_story_1_8_a_timeout_of_one_call_before_the_deadline_is_not_the_stages_timeout(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    case_id: str,
) -> None:
    page_id = pages.add(case_id)
    model.answers = [TimeoutError("one call")]

    result = run(command(case_id, page_id), ports, options)

    assert result.error_code is ErrorCode.STAGE_FAILED


@pytest.mark.parametrize("problem", ["fail_read", "gone"])
def test_story_1_8_a_page_that_cannot_be_read_after_it_was_listed_fails_the_stage(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    case_id: str,
    problem: str,
) -> None:
    page_id = pages.add(case_id)
    setattr(pages, problem, True)

    result = run(command(case_id, page_id), ports, options)

    # Settled, so the key row is not left running; the model was never asked.
    assert (result.status, result.error_code) == (
        StageStatus.FAILED,
        ErrorCode.STAGE_FAILED,
    )
    assert model.calls == 0


def test_story_1_8_a_stale_running_row_is_settled_as_failed_by_the_next_repeat(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
    fixed_now: datetime,
) -> None:
    page_id = pages.add(case_id)
    key = key_of(command(case_id, page_id))
    # A process began the classification and died: its row is still running.
    classification_id = new_id()
    asyncio.run(repository.begin(classification_id, key, fixed_now))

    # Within the deadline plus the margin it may still be at work.
    with pytest.raises(DomainError) as raised:
        run(
            command(case_id, page_id),
            ports,
            options,
            now=lambda: fixed_now + timedelta(seconds=240),
        )
    assert raised.value.code is ErrorCode.IN_PROGRESS

    result = run(
        command(case_id, page_id),
        ports,
        options,
        now=lambda: fixed_now + timedelta(seconds=241),
    )

    # Settled as `intake` settles a stale redaction: failed, as it would have
    # been at its deadline, under the id the row was given. No work is done.
    assert (result.status, result.error_code) == (
        StageStatus.FAILED,
        ErrorCode.STAGE_TIMEOUT,
    )
    assert result.classification_id == classification_id
    assert (model.calls, pages.reads) == (0, [])
    assert repository.result_of(key) == result
    # And from then on the stored failure is the answer.
    assert run(command(case_id, page_id), ports, options) == result


def test_story_1_8_a_classification_that_was_settled_meanwhile_keeps_the_first_result(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
) -> None:
    page_id = pages.add(case_id)
    key = key_of(command(case_id, page_id))

    async def scenario() -> tuple[ClassificationResult, ClassificationResult]:
        model.hold = asyncio.Event()
        slow = asyncio.create_task(
            classify_page(command(case_id, page_id), ports=ports, options=options)
        )
        while model.calls < 5:
            await asyncio.sleep(0)
        # A repeat long after: the row looks stale and is settled as failed.
        started = repository.rows[key].started_at
        settled = await classify_page(
            command(case_id, page_id),
            ports=ports,
            options=options,
            now=lambda: started + timedelta(seconds=300),
        )
        # Then the slow one finishes after all.
        model.hold.set()
        return settled, await slow

    settled, late = asyncio.run(scenario())

    assert settled.status is StageStatus.FAILED
    # The late result is not stored and not passed on: the first one stands.
    assert late == settled
    assert repository.stored == {}


def test_story_1_8_a_failure_that_cannot_be_stored_is_upstream_unavailable(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    page_id = pages.add(case_id)
    repository.fail_finish = True

    with caplog.at_level(logging.INFO), pytest.raises(DomainError) as raised:
        run(command(case_id, page_id), ports, options)

    # Neither the result nor its failure could be stored: `workflow` sends
    # the command again, and the row is settled then.
    assert raised.value.code is ErrorCode.UPSTREAM_UNAVAILABLE
    assert repository.rows[key_of(command(case_id, page_id))].running
    assert "stored=false type=StoreDown" in caplog.text
    assert "secret-store-detail" not in caplog.text


def test_story_1_8_a_request_that_is_cancelled_releases_the_key_row_for_the_repeat(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    page_id = pages.add(case_id)

    async def scenario() -> None:
        model.hold = asyncio.Event()
        request = asyncio.create_task(
            classify_page(command(case_id, page_id), ports=ports, options=options)
        )
        while model.calls < 5:
            await asyncio.sleep(0)
        # The caller goes away, or the service is stopping with the page in flight.
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())

    # Nothing is stored as a failure, and the key row is not left running:
    # it is gone, so the command sent again classifies the page.
    assert repository.rows == {}
    assert len(repository.released) == 1
    assert model.running == 0
    assert "classification released:" in caplog.text
    assert "reason=cancelled released=true" in caplog.text
    model.hold = None
    again = run(command(case_id, page_id), ports, options)
    assert again.status is StageStatus.DONE
    assert model.calls == 10


def test_story_1_8_when_intake_cannot_be_reached_for_the_page_the_command_can_be_sent_again(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
) -> None:
    page_id = pages.add(case_id)
    # Listed a moment ago, and now `intake` does not answer.
    pages.unavailable_reads = 1

    with pytest.raises(DomainError) as raised:
        run(command(case_id, page_id), ports, options)

    # Answered as `upstream_unavailable`, which `workflow` retries, and the
    # key row is released: no failure is stored for a passing fault.
    assert raised.value.code is ErrorCode.UPSTREAM_UNAVAILABLE
    assert repository.rows == {}
    assert model.calls == 0
    # The repeat does the work.
    again = run(command(case_id, page_id), ports, options)
    assert again.status is StageStatus.DONE
    assert len(repository.rows) == 1


def test_story_1_8_a_key_row_that_cannot_be_released_is_left_for_the_stale_rule(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    repository: MemoryRepository,
    case_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    page_id = pages.add(case_id)
    pages.unavailable_reads = 1
    repository.fail_release = True

    with caplog.at_level(logging.WARNING), pytest.raises(DomainError) as raised:
        run(command(case_id, page_id), ports, options)

    assert raised.value.code is ErrorCode.UPSTREAM_UNAVAILABLE
    assert repository.rows[key_of(command(case_id, page_id))].running
    assert "reason=upstream_unavailable:StoreDown released=false" in caplog.text
    assert "secret-store-detail" not in caplog.text


def test_story_1_8_a_done_result_that_cannot_be_stored_at_first_is_stored_on_a_second_try(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    page_id = pages.add(case_id)
    repository.failing_finishes = 1

    with caplog.at_level(logging.WARNING):
        result = run(command(case_id, page_id), ports, options)

    # The model's work is not thrown away for one failed write.
    assert result.status is StageStatus.DONE
    assert repository.finishes == 2
    assert model.calls == 5
    assert repository.result_of(key_of(command(case_id, page_id))) == result
    assert "classification store retried:" in caplog.text
    assert "type=StoreDown" in caplog.text


def test_story_1_8_a_done_result_that_cannot_be_stored_twice_ends_as_failed(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    repository: MemoryRepository,
    case_id: str,
) -> None:
    page_id = pages.add(case_id)
    repository.failing_finishes = 2

    result = run(command(case_id, page_id), ports, options)

    # Twice refused, then the failure itself could be stored.
    assert (result.status, result.error_code) == (
        StageStatus.FAILED,
        ErrorCode.STAGE_FAILED,
    )
    assert repository.finishes == 3
    assert repository.stored == {}


def test_story_1_8_a_domain_error_of_a_run_is_logged_by_its_code(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    case_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    page_id = pages.add(case_id)
    model.answers = [DomainError(ErrorCode.INTERNAL_ERROR, "SECRET detail")]

    with caplog.at_level(logging.INFO):
        result = run(command(case_id, page_id), ports, options)

    assert result.error_code is ErrorCode.STAGE_FAILED
    assert "reason=internal_error" in caplog.text
    assert "reason=DomainError" not in caplog.text
    assert "SECRET" not in caplog.text


# --- What is refused before any work -----------------------------------------------------


def test_story_1_8_an_unknown_page_is_not_found_and_leaves_no_key_row(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
) -> None:
    pages.add(case_id)

    with pytest.raises(DomainError) as raised:
        run(command(case_id, new_id()), ports, options)

    assert (raised.value.code, raised.value.http_status) == (ErrorCode.NOT_FOUND, 404)
    assert repository.rows == {}
    assert (model.calls, pages.reads) == (0, [])


def test_story_1_8_a_page_of_another_case_is_not_found(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
) -> None:
    pages.add(case_id)
    other_case = new_id()
    other_page = pages.add(other_case)

    with pytest.raises(DomainError) as raised:
        run(command(case_id, other_page), ports, options)

    # `intake` holds the page, but not for this case: it is never read.
    assert raised.value.code is ErrorCode.NOT_FOUND
    assert repository.rows == {}
    assert (model.calls, pages.reads) == (0, [])


def test_story_1_8_a_case_intake_does_not_hold_is_not_found(
    ports: ClassifyPorts, options: ClassifyOptions, repository: MemoryRepository
) -> None:
    with pytest.raises(DomainError) as raised:
        run(command(new_id(), new_id()), ports, options)

    assert raised.value.code is ErrorCode.NOT_FOUND
    assert repository.rows == {}


def test_story_1_8_the_doc_intelligence_contender_is_refused_as_not_available(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
) -> None:
    page_id = pages.add(case_id)

    with pytest.raises(DomainError) as raised:
        run(command(case_id, page_id, contender="doc-intelligence"), ports, options)

    # Story 4.2 builds it. 422, so `workflow` does not send it again; nothing
    # is asked of `intake` or the model, and no key row is left.
    assert (raised.value.code, raised.value.http_status) == (
        ErrorCode.VALIDATION_FAILED,
        422,
    )
    assert repository.rows == {}
    assert (model.calls, pages.listings) == (0, [])


# --- Reads, logs and the shape of the domain -----------------------------------------------


def test_story_1_8_the_read_lists_only_the_cases_own_classifications(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    repository: MemoryRepository,
    case_id: str,
) -> None:
    other_case = new_id()
    first = run(command(case_id, pages.add(case_id)), ports, options)
    second = run(command(case_id, pages.add(case_id)), ports, options)
    run(command(other_case, pages.add(other_case)), ports, options)

    listed = asyncio.run(list_classifications(case_id, repository=repository))

    assert listed.case_id == case_id
    assert listed.classifications == [first.classification, second.classification]
    # A case with none: an empty list, not an error.
    empty = asyncio.run(list_classifications(new_id(), repository=repository))
    assert empty.classifications == []


def test_story_1_8_the_trace_context_goes_on_to_intake_with_every_read(
    ports: ClassifyPorts, options: ClassifyOptions, pages: FakePages, case_id: str
) -> None:
    page_id = pages.add(case_id)
    context = {"traceparent": f"00-{TRACE_ID}-b7ad6b7169203331-01"}

    result = run(command(case_id, page_id), ports, options, trace_context=context)

    assert pages.trace_contexts == [context, context]
    # Without a trace the audit record carries the "no trace" id.
    assert result.audit.trace_id == "0" * 32


@pytest.mark.parametrize("outcome", ["done", "invalid", "unavailable", "repeat"])
def test_story_1_8_logs_carry_ids_codes_counts_and_timings_never_text_reason_or_output(
    ports: ClassifyPorts,
    options: ClassifyOptions,
    pages: FakePages,
    model: StubModel,
    case_id: str,
    caplog: pytest.LogCaptureFixture,
    outcome: str,
) -> None:
    page_id = pages.add(case_id)
    if outcome == "invalid":
        model.answers = ["SECRET-MODEL-OUTPUT not json"]
    if outcome == "unavailable":
        model.answers = [unavailable()]

    with caplog.at_level(logging.DEBUG):
        result = run(command(case_id, page_id), ports, options)
        if outcome == "repeat":
            run(command(case_id, page_id), ports, options)

    assert f"case_id={case_id}" in caplog.text
    assert f"page_id={page_id}" in caplog.text
    assert f"classification_id={result.classification_id}" in caplog.text
    for secret in ("SECRET", "lab_report", "laboratory", "HbA1c"):
        assert secret not in caplog.text
    if outcome == "done":
        assert "runs=5 agreeing_runs=5 duration_ms=" in caplog.text
    if outcome in ("invalid", "unavailable"):
        assert f"error_code={result.error_code}" in caplog.text


def test_story_1_8_the_domain_imports_no_framework_orm_or_http_library() -> None:
    # coding-style rule 9: the domain is framework-free; adapters call it.
    forbidden = {
        "fastapi",
        "starlette",
        "sqlalchemy",
        "httpx",
        "httpx2",
        "openai",
        "psycopg",
        "alembic",
        "azure",
        "opentelemetry",
    }
    for source in DOMAIN_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert name.split(".")[0] not in forbidden, (source.name, name)
                assert not name.startswith("classification.adapters"), source.name
