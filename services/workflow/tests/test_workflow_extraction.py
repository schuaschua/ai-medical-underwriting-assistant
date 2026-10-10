"""Story 2.4: extraction in the lifecycle, the case whose last page becomes final, and decisions told again.

Since stories 2.5 and 2.6 the last page becoming final does not complete
the case: its verdict runs come first (test_workflow_verdict.py).

Unit tests, with in-memory stand-ins: no database, no scheduler, no network.
The orchestrator is stepped by hand, as the engine would step it.
"""

import asyncio
import inspect
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from durabletask import task
from workflow_fakes import (
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
    redaction_done,
    starting,
)

from contracts.enums import (
    ClassifierContender,
    PageStatus,
    RetrieverConfig,
)
from contracts.errors import DomainError
from contracts.ids import new_id
from contracts.models.workflow import DecisionRequest
from contracts.operations import get_operation
from workflow.adapters.dapr import StageClient, build_http_client
from workflow.adapters.orchestration import (
    EXTRACT_FACTS,
    build_case_lifecycle,
    decision_event,
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
from workflow.domain.recording import RecordOutcome
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


@pytest.mark.parametrize("page_status", ["awaiting_triage"])
def test_story_2_4_a_page_is_extracted_only_from_extracting(
    store: MemoryCaseStore, case_id: str, page_status: str
) -> None:
    (page_id,) = gated_case(store, case_id, [Route.EXTRACTION])
    store.pages[page_id].page_status = PageStatus(page_status)
    before = actions(store)

    assert record(store, facts_done(case_id, page_id)) is RecordOutcome.OUT_OF_ORDER

    assert store.pages[page_id].page_status.value == page_status
    assert actions(store) == before


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
        # Stories 2.5 and 2.6: the retriever configurations of the case,
        # each of which gets a verdict run once every page is final.
        {
            "case_id": case_id,
            "classifier_contender": "llm",
            "retriever_configs": ["r3"],
            **started,
        },
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


# --- Telling an orchestration of a stored decision ------------------------------------------


def tell_again(
    teller: DecisionTeller, store: MemoryCaseStore, engine: FakeEngine, now: datetime
) -> int:
    return asyncio.run(
        tell_untold_decisions(teller, store=store, engine=engine, now=lambda: now)
    )


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
