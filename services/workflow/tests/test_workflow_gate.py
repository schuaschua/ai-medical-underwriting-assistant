"""Story 1.9: the gate routes every classified page, and progress says why a case failed.

Unit tests: the gate's rule, the recording of a route, the orchestrator's
steps after classification, the two activities and the progress payload.
No scheduler, no database, no network.
"""

import asyncio
import inspect
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from durabletask import task
from workflow_fakes import (
    FakeStages,
    MemoryCaseStore,
    activity_task,
    after_start,
    finish_lifecycle,
    starting,
)

from contracts.enums import (
    ClassifierContender,
    PageStatus,
    RetrieverConfig,
)
from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from workflow.adapters.orchestration import (
    ROUTE_PAGE,
    SETTLE_CASE_AFTER_GATE,
    build_case_lifecycle,
    decision_event,
)
from workflow.adapters.scheduler import Activities
from workflow.domain.cases import (
    record_stage_result,
)
from workflow.domain.entities import StartParameters
from workflow.domain.gate import (
    Route,
    route_page,
)
from workflow.domain.lifecycle import new_case

THRESHOLD = 0.90
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


# --- The rule ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("is_medical", "confidence", "route"),
    [
        # Exactly at the threshold counts as "or more".
        (True, 0.90, Route.EXTRACTION),
        (False, 0.90, Route.CUSTOMER),
        # Just under it does not: triage, whatever the label.
        (True, 0.8999, Route.TRIAGE),
        (False, 0.8999, Route.TRIAGE),
    ],
)
def test_story_1_9_the_gate_routes_a_page_by_its_label_and_its_confidence(
    is_medical: bool, confidence: float, route: Route
) -> None:
    assert (
        route_page(is_medical=is_medical, confidence=confidence, threshold=THRESHOLD)
        is route
    )


@pytest.mark.parametrize(
    ("confidence", "threshold"),
    [
        (float("nan"), 0.9),
        (0.95, 1.5),
    ],
)
def test_story_1_9_the_gate_refuses_a_confidence_or_threshold_that_is_no_unit_number(
    confidence: Any, threshold: Any
) -> None:
    # Without this a NaN, which compares false with everything, would be
    # routed as "sure".
    for is_medical in (True, False):
        with pytest.raises(DomainError) as raised:
            route_page(
                is_medical=is_medical, confidence=confidence, threshold=threshold
            )
        assert raised.value.code is ErrorCode.VALIDATION_FAILED


# --- The recording of a route --------------------------------------------------------


def classified_case(
    store: MemoryCaseStore,
    case_id: str,
    readings: dict[int, tuple[str, bool, float]] | None = None,
    pages: int = 3,
    parameters: StartParameters = PARAMETERS,
) -> tuple[FakeStages, list[str]]:
    """A started case whose pages are all classified; returns the stages and the page ids."""
    stages = FakeStages(pages=pages, readings=readings or {})

    async def scenario() -> list[str]:
        await store.start(*starting(new_case(case_id, parameters, NOW)))
        redacted = await stages.redact_document(
            case_id, eval_run_id=None, trace_context={}
        )
        await record_stage_result(redacted, store=store)
        for page_id in redacted.page_ids:
            result = await stages.classify_page(
                case_id,
                page_id,
                ClassifierContender.LLM,
                eval_run_id=None,
                trace_context={},
            )
            await record_stage_result(result, store=store)
        return list(redacted.page_ids)

    return stages, asyncio.run(scenario())


# --- The orchestrator ------------------------------------------------------------------


class RecordingContext:
    """Stands in for the engine's context: it notes what the orchestrator asks for."""

    def __init__(self) -> None:
        self.asked: list[dict[str, Any]] = []
        # Story 1.10: the external events the orchestrator waits for, by name.
        self.awaited: list[str] = []

    def call_activity(self, activity: str, **options: Any) -> object:
        self.asked.append({"activity": activity, **options})
        return activity_task(self, activity, options)

    def wait_for_external_event(self, name: str, **options: Any) -> object:
        self.awaited.append(name)
        return task.CompletableTask[Any]()


# Story 1.10: as the last answer, says the orchestrator is expected to be
# waiting for people now. Its result is then the events it waits for.
WAITING = object()


def waiting_for(*events: str) -> dict[str, list[str]]:
    return {"waiting_for": list(events)}


def run_lifecycle(
    started: dict[str, Any], answers: list[object]
) -> tuple[list[dict[str, Any]], Any, list[int]]:
    """Run the orchestrator to its end, answering each wait in turn."""
    context = RecordingContext()
    steps: Any = build_case_lifecycle(RETRY, STAGE_RETRY)(
        context,  # type: ignore[arg-type]  # the stand-in has the one method used
        # Stories 2.5 and 2.6: a start names the retriever configurations
        # of the case; each gets a verdict run once every page is final.
        {"retriever_configs": ["r3"], **started},
    )
    assert inspect.isgenerator(steps)
    steps = cast(Any, steps)
    next(steps)
    asked_at_each_wait = [len(context.asked)]
    for answer in answers:
        if answer is WAITING:
            return context.asked, waiting_for(*context.awaited), asked_at_each_wait
        try:
            if isinstance(answer, Exception):
                steps.throw(answer)
            else:
                steps.send(answer)
        except StopIteration as done:
            return context.asked, done.value, asked_at_each_wait
        asked_at_each_wait.append(len(context.asked))
    # Story 2.4: what is left to answer are the extractions of the pages
    # that reached `extracting`; each is answered as done. Stories 2.5 and
    # 2.6: then the verdict runs, each done and recorded, and the step that
    # completes the case.
    finished = finish_lifecycle(steps, context)
    if finished is None:
        raise AssertionError("the orchestrator waited for more than it was answered")
    return context.asked, finished, asked_at_each_wait


def confirmed(threshold: object = THRESHOLD) -> dict[str, Any]:
    """The confirm step's answer: the case is stored, and the threshold in force."""
    return {"outcome": "ok", "case_status": "running", "gate_threshold": threshold}


def redacted(page_ids: list[str]) -> dict[str, Any]:
    return {"outcome": "ok", "case_status": "running", "page_ids": page_ids}


def classified(is_medical: bool, confidence: float) -> dict[str, Any]:
    return {
        "outcome": "ok",
        "case_status": "running",
        "classification_id": new_id(),
        "is_medical": is_medical,
        "confidence": confidence,
    }


def routed(route: str) -> dict[str, str]:
    """A route activity's answer: the route the trail holds for its page."""
    return {"outcome": "ok", "case_status": "running", "route": route}


def settled_waiting(page_ids: list[str], statuses: list[str]) -> dict[str, Any]:
    """A settle activity's answer for a case with a waiting page: its status, and each page's.

    Story 1.10: the lifecycle waits for the pages the settle found waiting.
    """
    return {
        "outcome": "ok",
        "case_status": "awaiting_human",
        "page_statuses": dict(zip(page_ids, statuses, strict=True)),
    }


def routes_asked(asked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [step for step in asked if step["activity"] == ROUTE_PAGE]


# --- The activities ---------------------------------------------------------------------


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


def route_command(
    stages: FakeStages, case_id: str, page_id: str, route: str, **changes: Any
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "eval_run_id": None,
        "page_id": page_id,
        "classification_id": stages.classifications[
            (case_id, page_id)
        ].classification_id,
        "route": route,
        "threshold": 0.9,
        **changes,
    }


def test_story_1_9_a_retry_under_a_changed_setting_keeps_the_route_that_was_stored(
    store: MemoryCaseStore, case_id: str
) -> None:
    # A medical page at 0.6, routed to triage with the threshold 0.9.
    stages, (first, _, _) = classified_case(
        store, case_id, readings={1: ("lab_report", True, 0.6)}
    )
    recorded = route_command(stages, case_id, first, "awaiting_triage")
    # The same page, as a run with the threshold 0.5 would route it.
    under_another_setting = {**recorded, "route": "extracting", "threshold": 0.5}

    with service_loop() as loop:
        context = task.ActivityContext(case_id, 4)
        first_answer = Activities(store, loop, 5.0, stages, 5.0).route_page(
            context, recorded
        )
        # The service was restarted with WORKFLOW_GATE_THRESHOLD=0.5 and the
        # engine runs the activity again, asking for the other route.
        restarted = Activities(store, loop, 5.0, stages, 5.0, gate_threshold=0.5)
        again = restarted.route_page(context, under_another_setting)

    # Nothing is written a second time, and the answer is the stored route,
    # not the one asked for: the case is settled on what the page was given.
    assert first_answer == again == routed("awaiting_triage")
    assert store.pages[first].page_status is PageStatus.AWAITING_TRIAGE
    routes = [
        recording.audit.model_dump(mode="json")["detail"]
        for _, recording in after_start(store)
        if recording.audit.action.value == "page.routed"
    ]
    assert routes == [{"route": "awaiting_triage", "threshold": 0.9}]
    # And the orchestration, replayed with that answer, settles the case as
    # waiting for a human, though `extracting` alone would leave it running.
    asked, result, _ = run_lifecycle(
        {"case_id": case_id, "classifier_contender": "llm"},
        [
            confirmed(0.5),
            redacted([first]),
            [classified(True, 0.6)],
            [again],
            settled_waiting([first], ["awaiting_triage"]),
            WAITING,
        ],
    )
    assert routes_asked(asked)[0]["input"]["route"] == "extracting"
    # It asks for the settle, and then waits for the underwriter (story 1.10).
    assert asked[-1]["activity"] == SETTLE_CASE_AFTER_GATE
    assert result == waiting_for(decision_event(first, PageStatus.AWAITING_TRIAGE))
