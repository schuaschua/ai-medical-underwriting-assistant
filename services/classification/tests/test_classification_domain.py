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
from contracts.enums import PageType, StageStatus
from contracts.errors import DomainError, ErrorCode
from contracts.ids import is_uuid7, new_id
from contracts.models.classification import (
    ClassificationResult,
    ClassifierOutput,
    ClassifyCommand,
)
from contracts.rules import MEDICAL_PAGE_TYPES

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


@pytest.mark.parametrize(
    ("named", "winner", "confidence"),
    [
        # Two types named equally often: the one that comes first in the
        # contracts' `PageType` order, whatever the order of the runs.
        (("other", "invoice", "application_form"), "application_form", 1 / 3),
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
        "error_code": None,
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


@pytest.mark.parametrize("page_type", [PageType.LAB_REPORT, PageType.INVOICE])
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


# --- Idempotency (AD-6) ----------------------------------------------------------------


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


# --- Failures: stored, and never passed on ------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        # An unknown page type.
        json.dumps({"page_type": "utility_bill", "reason": "a bill"}),
        # Text around the object.
        'Here is my answer: {"page_type": "lab_report", "reason": "a table"}',
        # Not the object that was asked for.
        json.dumps({"page_type": "lab_report", "reason": "r", "is_medical": False}),
    ],
    ids=["unknown-page-type", "text-before", "says-medical-itself"],
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


# --- Reads, logs and the shape of the domain -----------------------------------------------


@pytest.mark.parametrize("outcome", ["invalid"])
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
