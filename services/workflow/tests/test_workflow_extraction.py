"""Story 2.4: extraction in the lifecycle, the case that completes by a stage result, and decisions told again.

Unit tests, with in-memory stand-ins: no database, no scheduler, no network.
The orchestrator is stepped by hand, as the engine would step it.
"""

import asyncio
import contextlib
import inspect
import json
import logging
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
from durabletask import task
from fastapi.testclient import TestClient
from workflow_fakes import (
    EXTRACTED,
    EXTRACTION_FAILED,
    TRACE_ID,
    FakeEngine,
    FakeStages,
    MemoryCaseStore,
    SidecarStandIn,
    StoreDown,
    activity_task,
    after_start,
    classification_done,
    facts_done,
    facts_failed,
    finish_extractions,
    pending_extractions,
    redaction_done,
    starting,
)

from contracts.audit import AuditAction
from contracts.enums import (
    CaseStatus,
    ClassifierContender,
    PageStatus,
    RetrieverConfig,
    StopAfter,
)
from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.workflow import DecisionRequest
from contracts.operations import get_operation
from workflow.adapters import orchestration
from workflow.adapters.dapr import StageClient, build_http_client
from workflow.adapters.db import SqlCaseStore
from workflow.adapters.http import app as app_module
from workflow.adapters.http.app import (
    create_app,
    decision_teller,
    tell_decisions_again,
    telling_decisions_again,
)
from workflow.adapters.orchestration import (
    CASE_LIFECYCLE,
    EXTRACT_FACTS,
    MARK_CASE_FAILED,
    SETTLE_CASE_AFTER_GATE,
    build_case_lifecycle,
    decision_event,
)
from workflow.adapters.scheduler import (
    Activities,
    ActivityFailed,
    SchedulerEngine,
    build_worker,
)
from workflow.domain.cases import (
    record_route,
    record_stage_result,
    settle_case_after_gate,
)
from workflow.domain.decisions import (
    DecisionTeller,
    record_decision,
    tell_untold_decisions,
)
from workflow.domain.entities import StartParameters
from workflow.domain.gate import Route
from workflow.domain.lifecycle import new_case
from workflow.domain.ports import Told
from workflow.domain.recording import RecordOutcome, plan_recording
from workflow.settings import Settings

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
PARAMETERS = StartParameters(
    classifier_contender=ClassifierContender.LLM,
    retriever_configs=(RetrieverConfig.R3,),
    stop_after=None,
    eval_run_id=None,
)
RETRY = task.RetryPolicy(
    first_retry_interval=timedelta(seconds=1), max_number_of_attempts=3
)
STAGE_RETRY = task.RetryPolicy(
    first_retry_interval=timedelta(seconds=1), max_number_of_attempts=9
)
CONFIRMED = {"outcome": "ok", "case_status": "running", "gate_threshold": 0.9}
ROUTE_READINGS = {
    "extracting": (True, 0.95),
    "awaiting_customer": (False, 1.0),
    "awaiting_triage": (True, 0.6),
}


def gated_case(
    store: MemoryCaseStore,
    case_id: str,
    routes: list[Route],
    parameters: StartParameters = PARAMETERS,
) -> list[str]:
    """A started case whose pages the gate has routed and settled; its page ids, in page order."""
    page_ids = [new_id() for _ in routes]

    async def scenario() -> None:
        await store.start(*starting(new_case(case_id, parameters, NOW)))
        await record_stage_result(redaction_done(case_id, page_ids), store=store)
        classified = [classification_done(case_id, page_id) for page_id in page_ids]
        for done in classified:
            await record_stage_result(done, store=store)
        for page_id, route, done in zip(page_ids, routes, classified, strict=True):
            await record_route(
                case_id, page_id, done.classification_id, route, 0.9, store=store
            )
        await settle_case_after_gate(case_id, store=store, trace_id=None)

    asyncio.run(scenario())
    return page_ids


def record(store: MemoryCaseStore, result: Any) -> RecordOutcome:
    return asyncio.run(record_stage_result(result, store=store, now=lambda: NOW))


def decide(
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    page_id: str,
    decision: str,
    actor: str,
    now: datetime = NOW,
) -> Any:
    return asyncio.run(
        record_decision(
            case_id,
            page_id,
            DecisionRequest.model_validate({"decision": decision, "actor": actor}),
            store=store,
            engine=engine,
            trace_id=TRACE_ID,
            now=lambda: now,
        )
    )


def actions(store: MemoryCaseStore) -> list[str]:
    return [recording.audit.action.value for _, recording in after_start(store)]


# --- The recording of an extraction result ----------------------------------------------


def test_story_2_4_a_done_page_stage_result_lets_the_case_follow_its_pages(
    case_id: str,
) -> None:
    page_id = new_id()

    assert plan_recording(facts_done(case_id, page_id)).follows_pages is True
    assert plan_recording(classification_done(case_id, page_id)).follows_pages is True
    # A failed result names the case status itself; a redaction has no page.
    failed = plan_recording(facts_failed(case_id, page_id))
    assert (failed.follows_pages, failed.case_status) == (False, CaseStatus.FAILED)
    assert plan_recording(redaction_done(case_id, [page_id])).follows_pages is False
    # No recording ever names `completed`: the pages say when a case is done.
    assert plan_recording(facts_done(case_id, page_id)).case_status is None


def test_story_2_4_a_done_extraction_moves_its_page_to_extracted_with_one_event(
    store: MemoryCaseStore, case_id: str
) -> None:
    first, second = gated_case(store, case_id, [Route.EXTRACTION, Route.EXTRACTION])
    result = facts_done(case_id, first)

    assert record(store, result) is RecordOutcome.RECORDED
    # The activity ran again: the same result writes nothing new.
    assert record(store, result) is RecordOutcome.DUPLICATE

    assert store.pages[first].page_status is PageStatus.EXTRACTED
    assert store.pages[second].page_status is PageStatus.EXTRACTING
    # Another page is still in work: the case goes on running.
    assert store.cases[case_id].case_status is CaseStatus.RUNNING
    assert actions(store).count("facts.extracted") == 1
    _, recording = after_start(store)[-1]
    audit = recording.audit
    assert (audit.action, audit.page_id, audit.ref) == (
        AuditAction.FACTS_EXTRACTED,
        first,
        result.fact_set_id,
    )
    assert audit.actor == "extraction:chat-main"


def test_story_2_4_the_last_page_becoming_final_completes_the_case_with_its_event_last(
    store: MemoryCaseStore, case_id: str
) -> None:
    first, second = gated_case(store, case_id, [Route.EXTRACTION, Route.EXTRACTION])

    record(store, facts_done(case_id, first))
    assert "case.completed" not in actions(store)
    record(store, facts_done(case_id, second, trace_id="1" * 32))

    assert store.cases[case_id].case_status is CaseStatus.COMPLETED
    # `case.completed` once, after the result that completed the case, with
    # the lifecycle as its actor and the trace of that result.
    assert actions(store)[-2:] == ["facts.extracted", "case.completed"]
    assert actions(store).count("case.completed") == 1
    completed = after_start(store)[-1][1].audit
    assert (completed.actor, completed.page_id, completed.ref) == (
        "workflow:case-lifecycle",
        None,
        case_id,
    )
    assert completed.trace_id == "1" * 32


@pytest.mark.parametrize(
    ("decisions", "final"),
    [
        ([("discard", "customer")], "discarded"),
        ([("keep", "customer"), ("deny", "underwriter")], "denied"),
    ],
)
def test_story_2_4_a_case_completes_when_every_page_is_extracted_discarded_or_denied(
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    decisions: list[tuple[str, str]],
    final: str,
) -> None:
    extracting, waiting = gated_case(store, case_id, [Route.EXTRACTION, Route.CUSTOMER])
    for decision, actor in decisions:
        decide(store, engine, case_id, waiting, decision, actor)
    # The decided page is final; one page is still being extracted.
    assert store.cases[case_id].case_status is CaseStatus.RUNNING

    record(store, facts_done(case_id, extracting))

    assert store.pages[waiting].page_status.value == final
    assert store.cases[case_id].case_status is CaseStatus.COMPLETED
    assert actions(store)[-1] == "case.completed"


def test_story_2_4_an_extraction_done_while_a_page_waits_leaves_the_case_waiting(
    store: MemoryCaseStore, case_id: str
) -> None:
    extracting, _ = gated_case(store, case_id, [Route.EXTRACTION, Route.TRIAGE])
    assert store.cases[case_id].case_status is CaseStatus.AWAITING_HUMAN

    record(store, facts_done(case_id, extracting))

    assert store.cases[case_id].case_status is CaseStatus.AWAITING_HUMAN
    assert "case.completed" not in actions(store)


def test_story_2_4_a_page_accepted_later_is_extracted_then_and_completes_the_case(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    extracting, unsure = gated_case(store, case_id, [Route.EXTRACTION, Route.TRIAGE])
    record(store, facts_done(case_id, extracting))

    decide(store, engine, case_id, unsure, "accept", "underwriter")
    assert store.pages[unsure].page_status is PageStatus.EXTRACTING
    assert store.cases[case_id].case_status is CaseStatus.RUNNING
    record(store, facts_done(case_id, unsure))

    assert store.cases[case_id].case_status is CaseStatus.COMPLETED
    assert actions(store)[-3:] == ["page.accepted", "facts.extracted", "case.completed"]


def test_story_2_4_a_failed_extraction_fails_its_page_and_the_case_with_stage_failed(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    failing, waiting, other = gated_case(
        store, case_id, [Route.EXTRACTION, Route.CUSTOMER, Route.EXTRACTION]
    )

    assert record(store, facts_failed(case_id, failing)) is RecordOutcome.RECORDED

    assert store.pages[failing].page_status is PageStatus.FAILED
    assert store.cases[case_id].case_status is CaseStatus.FAILED
    _, recording = after_start(store)[-1]
    assert recording.audit.action is AuditAction.STAGE_FAILED
    assert recording.error_code is ErrorCode.INVALID_MODEL_OUTPUT
    assert "case.completed" not in actions(store)
    # The waiting page takes no decision, and the other page no result.
    with pytest.raises(DomainError) as refused:
        decide(store, engine, case_id, waiting, "discard", "customer")
    assert refused.value.code is ErrorCode.NOT_AWAITING_DECISION
    assert store.pages[waiting].page_status is PageStatus.AWAITING_CUSTOMER
    assert record(store, facts_done(case_id, other)) is RecordOutcome.CASE_FAILED
    assert store.pages[other].page_status is PageStatus.EXTRACTING


@pytest.mark.parametrize(
    "page_status", ["uploaded", "classified", "awaiting_triage", "extracted", "denied"]
)
def test_story_2_4_a_page_is_extracted_only_from_extracting(
    store: MemoryCaseStore, case_id: str, page_status: str
) -> None:
    (page_id,) = gated_case(store, case_id, [Route.EXTRACTION])
    store.pages[page_id].page_status = PageStatus(page_status)
    before = actions(store)

    assert record(store, facts_done(case_id, page_id)) is RecordOutcome.OUT_OF_ORDER

    assert store.pages[page_id].page_status.value == page_status
    assert actions(store) == before


def test_story_2_4_a_case_told_to_stop_after_the_gate_is_not_reopened_by_a_stage_result(
    store: MemoryCaseStore, case_id: str
) -> None:
    (page_id,) = gated_case(
        store,
        case_id,
        [Route.EXTRACTION],
        parameters=replace(PARAMETERS, stop_after=StopAfter.GATE),
    )
    assert store.cases[case_id].case_status is CaseStatus.COMPLETED

    # Nothing commands it; should a result come all the same, the page moves
    # and the case, complete already, gets no second `case.completed`.
    record(store, facts_done(case_id, page_id))

    assert store.cases[case_id].case_status is CaseStatus.COMPLETED
    assert actions(store).count("case.completed") == 1


# --- The extract activity -------------------------------------------------------------


@contextmanager
def service_loop() -> Iterator[asyncio.AbstractEventLoop]:
    """An event loop on a thread of its own, as the service's is to the worker."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


def extracting_case(
    store: MemoryCaseStore, stages: FakeStages, case_id: str
) -> list[str]:
    """A started case, redacted by the stand-in stage, whose every page is `extracting`."""

    async def scenario() -> list[str]:
        await store.start(*starting(new_case(case_id, PARAMETERS, NOW)))
        redacted = await stages.redact_document(
            case_id, eval_run_id=None, trace_context={}
        )
        await record_stage_result(redacted, store=store)
        for page_id in redacted.page_ids:
            done = classification_done(case_id, page_id)
            await record_stage_result(done, store=store)
            await record_route(
                case_id,
                page_id,
                done.classification_id,
                Route.EXTRACTION,
                0.9,
                store=store,
            )
        return list(redacted.page_ids)

    return asyncio.run(scenario())


def extract(
    store: MemoryCaseStore,
    stages: FakeStages,
    case_id: str,
    page_id: str,
    times: int = 1,
    **command: Any,
) -> list[dict[str, str]]:
    asked: dict[str, str | None] = {
        "case_id": case_id,
        "page_id": page_id,
        "eval_run_id": None,
        **command,
    }
    with service_loop() as loop:
        activities = Activities(store, loop, 5.0, stages, 5.0)
        return [
            activities.extract_facts(task.ActivityContext(case_id, 9), asked)
            for _ in range(times)
        ]


def test_story_2_4_the_extract_activity_commands_the_stage_and_records_its_result(
    store: MemoryCaseStore, case_id: str
) -> None:
    stages = FakeStages(pages=2)
    first, second = extracting_case(store, stages, case_id)
    eval_run_id = new_id()

    # The engine ran the activity twice: the stage answers the repeat with
    # its stored result, and recording it again writes nothing new.
    answers = extract(store, stages, case_id, first, times=2, eval_run_id=eval_run_id)

    # Ids and statuses only (AD-6): nothing of the facts is handed on.
    assert answers == [EXTRACTED] * 2
    assert store.pages[first].page_status is PageStatus.EXTRACTED
    assert store.pages[second].page_status is PageStatus.EXTRACTING
    assert actions(store).count("facts.extracted") == 1
    assert [(call[0], call[1], call[2]) for call in stages.extract_calls] == [
        (case_id, first, eval_run_id)
    ] * 2
    # The last page: the same activity's recording completes the case.
    assert extract(store, stages, case_id, second) == [EXTRACTED]
    assert store.cases[case_id].case_status is CaseStatus.COMPLETED
    assert actions(store)[-1] == "case.completed"


def test_story_2_4_a_failed_extraction_is_answered_as_a_failed_case_not_retried(
    store: MemoryCaseStore, case_id: str
) -> None:
    stages = FakeStages(pages=2, failing_extraction_page_numbers=frozenset({1}))
    first, second = extracting_case(store, stages, case_id)

    assert extract(store, stages, case_id, first, times=2) == [EXTRACTION_FAILED] * 2
    # The other page's result comes to a failed case: answered so, too.
    assert extract(store, stages, case_id, second) == [EXTRACTION_FAILED]

    assert store.cases[case_id].case_status is CaseStatus.FAILED
    assert actions(store).count("stage.failed") == 1
    assert store.pages[second].page_status is PageStatus.EXTRACTING


@pytest.mark.parametrize("step", ["in_progress", "down"])
def test_story_2_4_in_progress_or_no_answer_fails_the_activity_so_the_command_is_sent_again(
    store: MemoryCaseStore, case_id: str, step: str
) -> None:
    stages = FakeStages(pages=1, extract_script=[step])
    (page_id,) = extracting_case(store, stages, case_id)

    with pytest.raises(ActivityFailed) as failed:
        extract(store, stages, case_id, page_id)
    again = extract(store, stages, case_id, page_id)

    assert failed.value.activity == EXTRACT_FACTS
    assert again == [EXTRACTED]
    assert len(stages.extract_calls) == 2


def test_story_2_4_a_page_the_stage_does_not_hold_is_refused_without_a_retry(
    store: MemoryCaseStore, case_id: str
) -> None:
    stages = FakeStages(pages=1)
    extracting_case(store, stages, case_id)

    answers = extract(store, stages, case_id, new_id())

    assert answers == [{"outcome": "refused", "reason": "not_found"}]
    assert len(stages.extract_calls) == 1


def test_story_2_4_a_result_the_trail_cannot_take_is_refused_not_left_running(
    store: MemoryCaseStore, case_id: str
) -> None:
    stages = FakeStages(pages=1)
    (page_id,) = extracting_case(store, stages, case_id)
    # The page is not `extracting`: its result contradicts what is stored.
    store.pages[page_id].page_status = PageStatus.AWAITING_TRIAGE

    assert extract(store, stages, case_id, page_id) == [
        {"outcome": "refused", "reason": "out_of_order"}
    ]


def test_story_2_4_the_worker_runs_the_extract_activity_under_the_name_the_engine_keeps(
    store: MemoryCaseStore, settings: Settings
) -> None:
    loop = asyncio.new_event_loop()
    try:
        activities = Activities(store, loop, 1.0, FakeStages())
        worker = build_worker(settings, activities)
    finally:
        loop.close()

    assert task.get_name(activities.extract_facts) == EXTRACT_FACTS == "extract_facts"
    assert worker is not None
    # The orchestration keeps its one name: it was changed in place.
    assert CASE_LIFECYCLE == "case_lifecycle"


# --- The client module: `extraction` through the Dapr sidecar ----------------------------


def stage_call(sidecar: SidecarStandIn, case_id: str, page_id: str) -> Any:
    async def scenario() -> Any:
        settings = Settings(applicationinsights_connection_string=None)
        client = StageClient(build_http_client(settings, sidecar.transport()), settings)
        try:
            return await client.extract_facts(
                case_id,
                page_id,
                eval_run_id=None,
                trace_context={"traceparent": f"00-{TRACE_ID}-b7ad6b7169203331-01"},
            )
        finally:
            await client.aclose()

    return asyncio.run(scenario())


def test_story_2_4_extraction_is_commanded_through_the_sidecar_by_app_id_with_ids_only(
    case_id: str,
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=1))
    redacted = asyncio.run(
        sidecar.stages.redact_document(case_id, eval_run_id=None, trace_context={})
    )
    (page_id,) = redacted.page_ids

    result = stage_call(sidecar, case_id, page_id)

    assert result == sidecar.stages.fact_sets[(case_id, page_id)]
    (request,) = sidecar.requests
    operation = get_operation("extract_facts")
    assert (request.method, request.url.path) == (
        "POST",
        f"/v1.0/invoke/extraction/method{operation.path}",
    )
    # AD-6: ids only; the stage reads the page from `intake` itself.
    assert json.loads(request.content) == {
        "case_id": case_id,
        "page_id": page_id,
        "eval_run_id": None,
    }
    assert TRACE_ID in request.headers["traceparent"]


@pytest.mark.parametrize(
    ("step", "code"),
    [
        ("in_progress", ErrorCode.IN_PROGRESS),
        ("not_found", ErrorCode.NOT_FOUND),
        ("down", ErrorCode.UPSTREAM_UNAVAILABLE),
    ],
)
def test_story_2_4_the_stages_own_answers_are_passed_on_with_their_codes(
    case_id: str, step: str, code: ErrorCode
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=1, extract_script=[step]))

    with pytest.raises(DomainError) as raised:
        stage_call(sidecar, case_id, new_id())

    assert raised.value.code is code


def test_story_2_4_a_result_about_another_page_is_never_recorded(case_id: str) -> None:
    other_page = new_id()

    def answers_about_another_page(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=facts_done(case_id, other_page).model_dump(mode="json")
        )

    sidecar = SidecarStandIn()
    sidecar.handle = answers_about_another_page  # type: ignore[method-assign,assignment]  # this test's stage answers wrongly

    with pytest.raises(DomainError) as raised:
        stage_call(sidecar, case_id, new_id())

    assert raised.value.code is ErrorCode.VALIDATION_FAILED


# --- The orchestrator --------------------------------------------------------------------


class SteppedContext:
    """Stands in for the engine's context: activities asked for, and events waited for."""

    def __init__(self) -> None:
        self.asked: list[dict[str, Any]] = []
        self.waits: dict[str, list[task.CompletableTask[Any]]] = {}

    def call_activity(self, activity: str, **options: Any) -> object:
        self.asked.append({"activity": activity, **options})
        return activity_task(self, activity, options)

    def wait_for_external_event(self, name: str, **options: Any) -> object:
        waiting = task.CompletableTask[Any]()
        self.waits.setdefault(name, []).append(waiting)
        return waiting

    def open_waits(self) -> list[str]:
        return [
            name
            for name, waits in self.waits.items()
            if any(not waiting.is_complete for waiting in waits)
        ]

    def extractions_asked(self) -> list[str]:
        """The page ids extraction was asked for, in the order asked."""
        return [
            step["input"]["page_id"]
            for step in self.asked
            if step["activity"] == EXTRACT_FACTS
        ]


def run_to_the_pages(
    case_id: str, routes: list[str], **started: Any
) -> tuple[SteppedContext, Any, list[str], Any]:
    """Run the orchestrator through the gate, and the settle if there is one.

    Returns it where it stands, each page going its own way; the last value
    is its result if it has ended already, else None.
    """
    context = SteppedContext()
    page_ids = [new_id() for _ in routes]
    steps: Any = build_case_lifecycle(RETRY, STAGE_RETRY)(
        context,  # type: ignore[arg-type]  # the stand-in has the methods used
        {"case_id": case_id, "classifier_contender": "llm", **started},
    )
    assert inspect.isgenerator(steps)
    steps = cast(Any, steps)
    next(steps)
    answers: list[object] = [
        CONFIRMED,
        {"outcome": "ok", "case_status": "running", "page_ids": page_ids},
        [
            {
                "outcome": "ok",
                "case_status": "running",
                "classification_id": new_id(),
                "is_medical": ROUTE_READINGS[route][0],
                "confidence": ROUTE_READINGS[route][1],
            }
            for route in routes
        ],
        [
            {"outcome": "ok", "case_status": "running", "route": route}
            for route in routes
        ],
    ]
    if set(routes) != {"extracting"}:
        answers.append(
            {
                "outcome": "ok",
                "case_status": "awaiting_human",
                "page_statuses": dict(zip(page_ids, routes, strict=True)),
            }
        )
    try:
        for answer in answers:
            steps.send(answer)
    except StopIteration as done:
        return context, steps, page_ids, done.value
    return context, steps, page_ids, None


def tell(context: SteppedContext, steps: Any, name: str, payload: object) -> Any:
    """Hand the orchestrator one external event, as the engine does; its result if it ends."""
    waiting = next(
        (wait for wait in context.waits.get(name, []) if not wait.is_complete), None
    )
    if waiting is None:
        return None
    waiting.complete(payload)
    try:
        steps.send(waiting)
    except StopIteration as done:
        return done.value
    return None


def test_story_2_4_every_page_the_gate_sends_on_gets_one_extraction_command(
    case_id: str,
) -> None:
    eval_run_id = new_id()
    context, steps, page_ids, result = run_to_the_pages(
        case_id, ["extracting"] * 3, eval_run_id=eval_run_id
    )

    # All three were asked for together, each with ids only (AD-6) and the
    # stage's retry policy; no settle, and nobody is waited for.
    assert result is None
    assert context.asked[-3:] == [
        {
            "activity": EXTRACT_FACTS,
            "input": {
                "case_id": case_id,
                "eval_run_id": eval_run_id,
                "page_id": page_id,
            },
            "retry_policy": STAGE_RETRY,
        }
        for page_id in page_ids
    ]
    assert SETTLE_CASE_AFTER_GATE not in [step["activity"] for step in context.asked]
    assert context.open_waits() == []

    # The orchestration ends when the case is final: with the last extraction.
    first, second, _ = pending_extractions(context)
    for _, asked in (first, second):
        asked.complete(EXTRACTED)
        steps.send(asked)
    assert finish_extractions(steps, context) == {
        "case_id": case_id,
        "case_status": "completed",
    }
    assert context.extractions_asked() == page_ids


def test_story_2_4_waits_and_extractions_run_side_by_side(case_id: str) -> None:
    context, steps, (sent_on, unsure, non_medical), result = run_to_the_pages(
        case_id, ["extracting", "awaiting_triage", "awaiting_customer"]
    )

    # One page is extracted while the two others wait for people.
    assert result is None
    assert context.extractions_asked() == [sent_on]
    assert context.open_waits() == [
        decision_event(unsure, PageStatus.AWAITING_TRIAGE),
        decision_event(non_medical, PageStatus.AWAITING_CUSTOMER),
    ]
    # An accept while the first extraction is still under way: the accepted
    # page gets its own command at once, not when the other is done.
    assert tell(context, steps, context.open_waits()[0], "accept") is None
    assert context.extractions_asked() == [sent_on, unsure]
    assert len(pending_extractions(context)) == 2
    # The extractions finish while the customer still has not answered: the
    # lifecycle goes on waiting for that one page.
    assert finish_extractions(steps, context) is None
    assert context.open_waits() == [
        decision_event(non_medical, PageStatus.AWAITING_CUSTOMER)
    ]
    # Kept, then accepted: extracted then, and the case ends with it.
    assert tell(context, steps, context.open_waits()[0], "keep") is None
    assert tell(context, steps, context.open_waits()[0], "accept") is None
    assert context.extractions_asked() == [sent_on, unsure, non_medical]
    assert finish_extractions(steps, context) == {
        "case_id": case_id,
        "case_status": "completed",
    }


def test_story_2_4_a_page_gets_one_extraction_command_however_often_its_decision_is_told(
    case_id: str,
) -> None:
    context, steps, (page_id,), _ = run_to_the_pages(case_id, ["awaiting_triage"])
    name = decision_event(page_id, PageStatus.AWAITING_TRIAGE)

    # The event is raised by the request and again by the service itself.
    assert tell(context, steps, name, "accept") is None
    assert tell(context, steps, name, "accept") is None

    assert context.extractions_asked() == [page_id]
    assert context.open_waits() == []


def test_story_2_4_a_discarded_or_denied_page_is_final_without_an_extraction(
    case_id: str,
) -> None:
    context, steps, (first, second), _ = run_to_the_pages(
        case_id, ["awaiting_customer", "awaiting_triage"]
    )

    assert (
        tell(
            context,
            steps,
            decision_event(first, PageStatus.AWAITING_CUSTOMER),
            "discard",
        )
        is None
    )
    result = tell(
        context, steps, decision_event(second, PageStatus.AWAITING_TRIAGE), "deny"
    )

    assert result == {"case_id": case_id, "case_status": "completed"}
    assert context.extractions_asked() == []


def test_story_2_4_a_failed_extraction_ends_the_lifecycle_while_another_page_waits(
    case_id: str,
) -> None:
    context, steps, (sent_on, waiting), _ = run_to_the_pages(
        case_id, ["extracting", "awaiting_customer"]
    )
    asked_before = len(context.asked)

    result = finish_extractions(steps, context, {sent_on: EXTRACTION_FAILED})

    # The recording of the failed result failed the case, with its one
    # `stage.failed` event: the orchestration ends, and marks nothing again.
    assert result == {"case_id": case_id, "case_status": "failed"}
    assert len(context.asked) == asked_before
    # The wait for the other page's decision is simply left: a failed case
    # takes no decision, so nothing would ever have answered it.
    assert context.open_waits() == [
        decision_event(waiting, PageStatus.AWAITING_CUSTOMER)
    ]


@pytest.mark.parametrize(
    "answer",
    [
        {"outcome": "refused", "reason": "not_found"},
        {"outcome": "refused", "reason": "out_of_order"},
        "not an answer",
        None,
        task.TaskFailedError("failed", RuntimeError("x")),
    ],
    ids=["not-found", "out-of-order", "not-a-mapping", "nothing", "every-retry-failed"],
)
def test_story_2_4_an_extraction_that_is_refused_or_never_answers_marks_the_case_failed(
    case_id: str, answer: object
) -> None:
    context, steps, _, _ = run_to_the_pages(case_id, ["extracting", "extracting"])
    (_, asked), _ = pending_extractions(context)
    if isinstance(answer, Exception):
        asked.fail("failed", answer)
    else:
        asked.complete(answer)

    # The orchestrator asks for the case to be marked failed, and ends.
    steps.send(asked)
    assert context.asked[-1] == {
        "activity": MARK_CASE_FAILED,
        "input": {"case_id": case_id, "eval_run_id": None},
        "retry_policy": RETRY,
    }
    with pytest.raises(StopIteration) as done:
        steps.send({"outcome": "ok", "recorded": "recorded"})
    assert done.value.value == {"case_id": case_id, "case_status": "failed"}


def test_story_2_4_a_case_told_to_stop_after_the_gate_is_not_extracted(
    case_id: str,
) -> None:
    context = SteppedContext()
    page_id = new_id()
    steps: Any = build_case_lifecycle(RETRY, STAGE_RETRY)(
        context,  # type: ignore[arg-type]  # the stand-in has the methods used
        {"case_id": case_id, "classifier_contender": "llm", "stop_after": "gate"},
    )
    steps = cast(Any, steps)
    next(steps)
    with pytest.raises(StopIteration) as done:
        for answer in (
            CONFIRMED,
            {"outcome": "ok", "case_status": "running", "page_ids": [page_id]},
            [
                {
                    "outcome": "ok",
                    "case_status": "running",
                    "classification_id": new_id(),
                    "is_medical": True,
                    "confidence": 0.95,
                }
            ],
            [{"outcome": "ok", "case_status": "running", "route": "extracting"}],
            {"outcome": "ok", "case_status": "completed"},
        ):
            steps.send(answer)

    assert done.value.value == {"case_id": case_id, "case_status": "completed"}
    assert context.extractions_asked() == []


@pytest.mark.parametrize("status", ["uploaded", "classified"])
def test_story_2_4_a_page_in_a_status_the_lifecycle_cannot_go_on_from_fails_the_case(
    case_id: str, status: str
) -> None:
    context = SteppedContext()
    first, second = new_id(), new_id()
    steps: Any = build_case_lifecycle(RETRY, STAGE_RETRY)(
        context,  # type: ignore[arg-type]  # the stand-in has the methods used
        {"case_id": case_id, "classifier_contender": "llm"},
    )
    steps = cast(Any, steps)
    next(steps)
    for answer in (
        CONFIRMED,
        {"outcome": "ok", "case_status": "running", "page_ids": [first, second]},
        [
            {
                "outcome": "ok",
                "case_status": "running",
                "classification_id": new_id(),
                "is_medical": False,
                "confidence": 1.0,
            }
        ]
        * 2,
        [{"outcome": "ok", "case_status": "running", "route": "awaiting_customer"}] * 2,
        # The settle reads one page as it should be, and the other in a
        # status that is neither in work, nor waiting, nor final.
        {
            "outcome": "ok",
            "case_status": "awaiting_human",
            "page_statuses": {first: "awaiting_customer", second: status},
        },
    ):
        steps.send(answer)

    # Not skipped, with the lifecycle ending on a case still `running`: the
    # case is marked failed, and nothing is waited for or extracted.
    assert context.asked[-1] == {
        "activity": MARK_CASE_FAILED,
        "input": {"case_id": case_id, "eval_run_id": None},
        "retry_policy": RETRY,
    }
    assert context.open_waits() == []
    assert context.extractions_asked() == []
    with pytest.raises(StopIteration) as done:
        steps.send({"outcome": "ok", "recorded": "recorded"})
    assert done.value.value == {"case_id": case_id, "case_status": "failed"}


def test_story_2_4_the_lifecycle_is_replayed_the_same_way(case_id: str) -> None:
    def run() -> tuple[list[str], list[int], Any]:
        context, steps, page_ids, _ = run_to_the_pages(
            case_id, ["awaiting_triage", "extracting", "awaiting_customer"]
        )
        tell(
            context,
            steps,
            decision_event(page_ids[2], PageStatus.AWAITING_CUSTOMER),
            "discard",
        )
        tell(
            context,
            steps,
            decision_event(page_ids[0], PageStatus.AWAITING_TRIAGE),
            "accept",
        )
        result = finish_extractions(steps, context)
        place = {page_id: index for index, page_id in enumerate(page_ids)}
        return (
            [step["activity"] for step in context.asked],
            [place[page_id] for page_id in context.extractions_asked()],
            result["case_status"],
        )

    assert run() == run()
    assert run()[1:] == ([1, 0], "completed")


def test_story_2_4_the_orchestration_neither_polls_nor_has_a_timer_and_says_how_it_changes() -> (
    None
):
    source = inspect.getsource(orchestration.build_case_lifecycle)
    for forbidden in ("create_timer", "sleep", "datetime", "random", "await "):
        assert forbidden not in source
    # The notes say that the body was changed in place in this story, why
    # that was safe, and how the next change must be made.
    notes = inspect.getdoc(orchestration) or ""
    assert "Story 2.4 changed what this body yields" in notes
    assert "changed in place" in notes
    assert "nothing is deployed" in notes
    assert "a new name\nbeside `CASE_LIFECYCLE`" in notes


# --- Telling an orchestration of a stored decision ------------------------------------------


def waiting_case(store: MemoryCaseStore, case_id: str) -> str:
    (page_id,) = gated_case(store, case_id, [Route.TRIAGE])
    return page_id


def test_story_2_4_a_decision_whose_event_was_raised_is_marked_as_told(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    page_id = waiting_case(store, case_id)

    recorded = decide(store, engine, case_id, page_id, "accept", "underwriter")
    # The same decision again raises the event again and marks nothing twice.
    decide(
        store,
        engine,
        case_id,
        page_id,
        "accept",
        "underwriter",
        now=NOW + timedelta(hours=1),
    )

    assert store.told_decisions == {recorded.decision_id: NOW}
    assert len(engine.told) == 2


def test_story_2_4_a_decision_whose_event_could_not_be_raised_carries_no_mark(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    page_id = waiting_case(store, case_id)
    engine.fail_events = True

    with pytest.raises(DomainError) as raised:
        decide(store, engine, case_id, page_id, "accept", "underwriter")

    # Stored, and the page moved on; but the orchestration does not know.
    assert raised.value.code is ErrorCode.UPSTREAM_UNAVAILABLE
    assert store.pages[page_id].page_status is PageStatus.EXTRACTING
    assert len(store.decisions) == 1
    assert store.told_decisions == {}


def test_story_2_4_a_mark_that_cannot_be_written_does_not_fail_the_decision(
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    page_id = waiting_case(store, case_id)
    store.fail_marks = True

    with caplog.at_level(logging.WARNING):
        recorded = decide(store, engine, case_id, page_id, "accept", "underwriter")

    assert recorded.page_status is PageStatus.EXTRACTING
    assert len(engine.told) == 1
    assert "decision told but not marked" in caplog.text
    assert "secret-store-detail" not in caplog.text


def tell_again(
    teller: DecisionTeller, store: MemoryCaseStore, engine: FakeEngine, now: datetime
) -> int:
    return asyncio.run(
        tell_untold_decisions(teller, store=store, engine=engine, now=lambda: now)
    )


def test_story_2_4_workflow_itself_tells_the_orchestration_of_a_decision_whose_event_was_lost(
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    page_id = waiting_case(store, case_id)
    engine.fail_events = True
    with pytest.raises(DomainError):
        decide(store, engine, case_id, page_id, "accept", "underwriter")
    engine.fail_events = False
    (decision,) = store.decisions
    teller = DecisionTeller(grace_seconds=30.0)

    # Nobody repeats the decision. Within the grace time it is left alone:
    # the request that stored it may still be telling it.
    assert tell_again(teller, store, engine, NOW + timedelta(seconds=30)) == 0
    assert engine.told == []
    with caplog.at_level(logging.INFO):
        told = tell_again(teller, store, engine, NOW + timedelta(seconds=31))

    # The same event the request would have raised: the page, the status it
    # had for the decision, and the decision.
    assert told == 1
    assert engine.told == [
        (case_id, page_id, PageStatus.AWAITING_TRIAGE, decision.decision)
    ]
    assert store.told_decisions == {decision.decision_id: NOW + timedelta(seconds=31)}
    assert f"decision told again: case_id={case_id} page_id={page_id}" in caplog.text
    # Told and marked: the next look finds nothing to do.
    assert tell_again(teller, store, engine, NOW + timedelta(minutes=5)) == 0
    assert len(engine.told) == 1


def untold_decision(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str, routes: list[Route]
) -> list[str]:
    """A waiting case whose first page was accepted while the engine was away; its page ids."""
    page_ids = gated_case(store, case_id, routes)
    engine.fail_events = True
    with pytest.raises(DomainError):
        decide(store, engine, case_id, page_ids[0], "accept", "underwriter")
    engine.fail_events = False
    engine.tell_attempts = 0
    return page_ids


def test_story_2_4_a_decision_is_never_given_up_however_long_the_scheduler_is_away(
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (page_id,) = untold_decision(store, engine, case_id, [Route.TRIAGE])
    (decision,) = store.decisions
    teller = DecisionTeller(grace_seconds=0.0)
    later = NOW + timedelta(minutes=1)
    # The scheduler is away for a long time: many more looks than any
    # count of attempts would allow.
    engine.failing_events = 200

    with caplog.at_level(logging.WARNING):
        for _ in range(200):
            with pytest.raises(StoreDown):
                tell_again(teller, store, engine, later)

    # A log line for each look, with ids, counts and the error's type.
    lines = [
        record.getMessage()
        for record in caplog.records
        if "decision not told again" in record.getMessage()
    ]
    assert len(lines) == 200
    assert all(decision.decision_id in line for line in lines)
    assert all("type=StoreDown" in line for line in lines)
    assert "secret-store-detail" not in caplog.text
    assert store.told_decisions == {}
    # The scheduler is back: the very next look tells it, in this process.
    assert tell_again(teller, store, engine, later) == 1
    assert decision.decision_id in store.told_decisions
    assert [told[1] for told in engine.told] == [page_id]


def test_story_2_4_a_look_that_cannot_reach_the_scheduler_ends_there(
    store: MemoryCaseStore, engine: FakeEngine
) -> None:
    for _ in range(3):
        untold_decision(store, engine, new_id(), [Route.TRIAGE])
    engine.failing_events = 1
    teller = DecisionTeller(grace_seconds=0.0)
    later = NOW + timedelta(minutes=1)

    with pytest.raises(StoreDown):
        tell_again(teller, store, engine, later)

    # One attempt, not one per decision: the others would meet the same engine.
    assert engine.tell_attempts == 1
    assert tell_again(teller, store, engine, later) == 3


def test_story_2_4_the_wait_between_looks_grows_to_a_cap_and_comes_back() -> None:
    teller = DecisionTeller(interval_seconds=15.0, max_interval_seconds=100.0)

    assert [teller.wait_after(failed) for failed in range(6)] == [
        15.0,
        30.0,
        60.0,
        100.0,
        100.0,
        100.0,
    ]
    # However long the outage: no overflow, and never over the cap.
    assert teller.wait_after(10_000) == 100.0


def test_story_2_4_one_look_takes_the_oldest_untold_decisions_up_to_its_batch(
    store: MemoryCaseStore, engine: FakeEngine
) -> None:
    engine.fail_events = True
    pages = []
    for minutes in range(3):
        case_id = new_id()
        page_id = waiting_case(store, case_id)
        with pytest.raises(DomainError):
            decide(
                store,
                engine,
                case_id,
                page_id,
                "deny",
                "underwriter",
                now=NOW + timedelta(minutes=minutes),
            )
        pages.append(page_id)
    engine.fail_events = False
    teller = DecisionTeller(grace_seconds=0.0, batch_size=2)
    later = NOW + timedelta(hours=1)

    assert [tell_again(teller, store, engine, later) for _ in range(3)] == [2, 1, 0]

    assert [told[1] for told in engine.told] == pages


@pytest.mark.parametrize("found", [Told.MISSING, Told.DEAD])
def test_story_2_4_a_decision_whose_case_has_no_orchestration_fails_the_case_and_is_not_marked_told(
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    found: Told,
    caplog: pytest.LogCaptureFixture,
) -> None:
    accepted, waiting = untold_decision(
        store, engine, case_id, [Route.TRIAGE, Route.CUSTOMER]
    )
    (decision,) = store.decisions
    # The orchestration is gone, or failed or was terminated: nothing will
    # ever extract the accepted page.
    engine.orchestration = found
    teller = DecisionTeller(grace_seconds=0.0)
    later = NOW + timedelta(minutes=1)
    assert store.cases[case_id].case_status is CaseStatus.AWAITING_HUMAN

    with caplog.at_level(logging.ERROR):
        told = tell_again(teller, store, engine, later)

    # Not told, and not marked as told. The case does not stay waiting: it
    # is failed through the lifecycle's own path, with its one case-level
    # `stage.failed` event, and the log says why.
    assert told == 0
    assert store.told_decisions == {}
    assert store.cases[case_id].case_status is CaseStatus.FAILED
    _, recording = after_start(store)[-1]
    assert (recording.audit.action, recording.audit.page_id, recording.audit.ref) == (
        AuditAction.STAGE_FAILED,
        None,
        case_id,
    )
    assert recording.audit.actor == "workflow:case-lifecycle"
    assert recording.error_code is ErrorCode.STAGE_FAILED
    assert (
        f"decision has no orchestration to tell: case_id={case_id} "
        f"page_id={accepted} decision_id={decision.decision_id} "
        f"orchestration={found.value} case_failed=recorded case_status=failed"
    ) in caplog.text
    # The pages stay as they were, and the waiting one takes no decision.
    assert store.pages[accepted].page_status is PageStatus.EXTRACTING
    assert store.pages[waiting].page_status is PageStatus.AWAITING_CUSTOMER
    # A failed case's decisions are looked for no more: no second event,
    # and the engine is not asked again.
    engine.tell_attempts = 0
    assert tell_again(teller, store, engine, later + timedelta(hours=1)) == 0
    assert engine.tell_attempts == 0
    assert actions(store).count("stage.failed") == 1


@pytest.mark.parametrize("found", [Told.MISSING, Told.DEAD])
def test_story_2_4_the_request_that_stores_a_decision_fails_a_case_nothing_runs_too(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str, found: Told
) -> None:
    page_id = waiting_case(store, case_id)
    engine.orchestration = found

    # The decision is stored and answered; the case has no lifecycle.
    recorded = decide(store, engine, case_id, page_id, "accept", "underwriter")

    assert recorded.page_status is PageStatus.EXTRACTING
    assert store.told_decisions == {}
    assert store.cases[case_id].case_status is CaseStatus.FAILED
    assert actions(store)[-2:] == ["page.accepted", "stage.failed"]


def test_story_2_4_a_decision_that_completed_its_case_is_marked_when_no_orchestration_is_left(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    # The one waiting page is denied while the engine is away: the case is
    # complete in the store, and its orchestration then turns out to be gone.
    page_id = waiting_case(store, case_id)
    engine.fail_events = True
    with pytest.raises(DomainError):
        decide(store, engine, case_id, page_id, "deny", "underwriter")
    engine.fail_events = False
    assert store.cases[case_id].case_status is CaseStatus.COMPLETED
    engine.orchestration = Told.MISSING
    teller = DecisionTeller(grace_seconds=0.0)

    tell_again(teller, store, engine, NOW + timedelta(minutes=1))

    # A completed case cannot fail and has nothing left to wake: the
    # decision is marked, so it is not looked at for ever.
    assert store.cases[case_id].case_status is CaseStatus.COMPLETED
    assert len(store.told_decisions) == 1
    assert "stage.failed" not in actions(store)


def test_story_2_4_a_decision_of_a_lifecycle_that_ended_as_it_should_is_marked(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    untold_decision(store, engine, case_id, [Route.TRIAGE])
    engine.orchestration = Told.ENDED

    told = tell_again(
        DecisionTeller(grace_seconds=0.0), store, engine, NOW + timedelta(minutes=1)
    )

    # Nothing was raised, and nothing needs to be: marked, and the case kept.
    assert (told, len(store.told_decisions)) == (0, 1)
    assert store.cases[case_id].case_status is CaseStatus.RUNNING


def test_story_2_4_the_service_looks_for_untold_decisions_on_a_timer_of_its_own_and_backs_off(
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    untold_decision(store, engine, case_id, [Route.TRIAGE])
    store.decisions[0] = replace(
        store.decisions[0], occurred_at=datetime(2020, 1, 1, tzinfo=UTC)
    )
    # The scheduler is away for the first three looks.
    engine.failing_events = 3
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 6:
            raise asyncio.CancelledError

    async def scenario() -> None:
        await tell_decisions_again(
            DecisionTeller(
                interval_seconds=7.5, max_interval_seconds=25.0, grace_seconds=0.0
            ),
            store,
            engine,
            sleep=sleep,
        )

    with caplog.at_level(logging.WARNING), pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())

    # Its interval before the first look; after each failed look twice as
    # long, up to the cap; back to the interval once a look has worked.
    assert waits == [7.5, 15.0, 25.0, 25.0, 7.5, 7.5]
    assert len(engine.told) == 1
    assert "decisions not looked for: failed_looks=3 next_look_seconds=25" in (
        caplog.text
    )
    assert "type=StoreDown" in caplog.text
    assert "secret-store-detail" not in caplog.text
    # The timer is the service's: the orchestration has none (AD-5).
    assert "tell_decisions_again" not in inspect.getsource(orchestration)


def test_story_2_4_a_database_that_is_away_is_a_failed_look_too(
    store: MemoryCaseStore, engine: FakeEngine
) -> None:
    store.fail = True

    with pytest.raises(StoreDown):
        tell_again(DecisionTeller(), store, engine, NOW)


def test_story_2_4_the_tell_again_settings_have_defaults_and_bounds() -> None:
    settings = Settings(applicationinsights_connection_string=None)

    assert (
        settings.decision_tell_interval_seconds,
        settings.decision_tell_max_interval_seconds,
        settings.decision_tell_grace_seconds,
        settings.decision_tell_batch_size,
    ) == (15.0, 300.0, 30.0, 50)
    assert decision_teller(settings) == DecisionTeller()
    for bad in (
        {"decision_tell_interval_seconds": 0},
        {"decision_tell_max_interval_seconds": 0},
        {"decision_tell_grace_seconds": -1},
        {"decision_tell_batch_size": 0},
    ):
        with pytest.raises(ValueError):
            Settings(**bad)  # type: ignore[arg-type]  # the wrong value is the point


def test_story_2_4_every_tell_again_setting_reaches_the_task_the_app_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        applicationinsights_connection_string=None,
        decision_tell_interval_seconds=3.5,
        decision_tell_max_interval_seconds=77.0,
        decision_tell_grace_seconds=11.0,
        decision_tell_batch_size=7,
    )
    started: list[tuple[DecisionTeller, object, object]] = []

    @contextlib.asynccontextmanager
    async def capture(
        teller: DecisionTeller, store: object, engine: object
    ) -> AsyncIterator[None]:
        started.append((teller, store, engine))
        yield

    @contextlib.asynccontextmanager
    async def no_worker(*arguments: object, **options: object) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(app_module, "telling_decisions_again", capture)
    monkeypatch.setattr(app_module, "running_worker", no_worker)

    # The app as the server builds it: real adapters, none of them connected.
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        assert client.get("/health").status_code == 200

    ((teller, store, engine),) = started
    # All four values, none of them a default.
    assert teller == DecisionTeller(
        interval_seconds=3.5,
        max_interval_seconds=77.0,
        grace_seconds=11.0,
        batch_size=7,
    )
    assert teller != DecisionTeller()
    assert isinstance(store, SqlCaseStore)
    assert isinstance(engine, SchedulerEngine)


def test_story_2_4_the_task_started_with_the_app_looks_and_is_cancelled_with_it(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    untold_decision(store, engine, case_id, [Route.TRIAGE])
    store.decisions[0] = replace(
        store.decisions[0], occurred_at=datetime(2020, 1, 1, tzinfo=UTC)
    )

    async def scenario() -> None:
        teller = DecisionTeller(interval_seconds=0.001, grace_seconds=0.0)
        async with telling_decisions_again(teller, store, engine):
            while not engine.told:
                await asyncio.sleep(0)

    asyncio.run(scenario())

    assert len(engine.told) == 1
    assert len(store.told_decisions) == 1
