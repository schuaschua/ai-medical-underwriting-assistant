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
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from durabletask import task
from fastapi.testclient import TestClient
from pydantic import ValidationError
from workflow_fakes import (
    FakeStages,
    MemoryCaseStore,
    after_start,
    classification_failed,
    redaction_failed,
    starting,
)

from contracts.audit import AuditAction, RouteDetail
from contracts.enums import (
    ActorKind,
    CaseStatus,
    ClassifierContender,
    PageStatus,
    RetrieverConfig,
    StopAfter,
)
from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.workflow import CaseProgress
from workflow.adapters import orchestration
from workflow.adapters.orchestration import (
    CLASSIFY_PAGE,
    MARK_CASE_FAILED,
    ROUTE_PAGE,
    SETTLE_CASE_AFTER_GATE,
    build_case_lifecycle,
    decision_event,
)
from workflow.adapters.scheduler import Activities, ActivityFailed, build_worker
from workflow.domain import case_status, gate
from workflow.domain.case_status import case_status_after_gate
from workflow.domain.cases import (
    record_route,
    record_stage_result,
    settle_case_after_gate,
)
from workflow.domain.entities import StartParameters
from workflow.domain.gate import (
    GATE_ACTOR,
    Route,
    route_page,
    route_recording,
)
from workflow.domain.lifecycle import new_case
from workflow.domain.recording import PageChange, RecordOutcome
from workflow.domain.transitions import page_statuses_before
from workflow.settings import Settings

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
CONFIRMED = {"outcome": "ok", "case_status": "running", "gate_threshold": 0.9}
ROUTED = {"outcome": "ok", "case_status": "running", "route": "extracting"}
MARKED = {"outcome": "ok", "recorded": "recorded"}


# --- The rule ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("is_medical", "confidence", "route"),
    [
        # Medical and sure: extraction.
        (True, 0.95, Route.EXTRACTION),
        (True, 1.0, Route.EXTRACTION),
        # Not medical and sure: the customer is asked.
        (False, 1.0, Route.CUSTOMER),
        (False, 0.95, Route.CUSTOMER),
        # Not sure, whatever the label: triage.
        (True, 0.6, Route.TRIAGE),
        (False, 0.6, Route.TRIAGE),
        (True, 0.0, Route.TRIAGE),
        # Exactly at the threshold counts as "or more".
        (True, 0.90, Route.EXTRACTION),
        (False, 0.90, Route.CUSTOMER),
        # Just under it does not.
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
        (float("inf"), 0.9),
        (1.01, 0.9),
        (-0.01, 0.9),
        (0.95, float("nan")),
        (0.95, 1.5),
        (0.95, -0.1),
        (True, 0.9),
        (0.95, True),
        ("0.95", 0.9),
        (None, 0.9),
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


@pytest.mark.parametrize("threshold", [float("nan"), 1.5, -0.1])
def test_story_1_9_a_route_is_not_recorded_with_a_threshold_that_is_no_unit_number(
    case_id: str, threshold: float
) -> None:
    with pytest.raises(DomainError) as raised:
        route_recording(
            case_id, new_id(), new_id(), Route.TRIAGE, threshold, occurred_at=NOW
        )
    assert raised.value.code is ErrorCode.VALIDATION_FAILED


def test_story_1_9_each_route_is_the_page_status_it_gives() -> None:
    assert {route: route.page_status for route in Route} == {
        Route.EXTRACTION: PageStatus.EXTRACTING,
        Route.CUSTOMER: PageStatus.AWAITING_CUSTOMER,
        Route.TRIAGE: PageStatus.AWAITING_TRIAGE,
    }
    # Each is a status the transition table lets follow `classified`.
    for route in Route:
        assert PageStatus.CLASSIFIED in page_statuses_before(route.page_status)


def test_story_1_9_another_threshold_routes_the_same_page_differently() -> None:
    def route(threshold: float) -> Route:
        return route_page(is_medical=True, confidence=0.6, threshold=threshold)

    assert route(0.5) is Route.EXTRACTION
    assert route(0.6) is Route.EXTRACTION
    assert route(0.9) is Route.TRIAGE
    # The ends of the range: everything is sure, or only certainty is.
    assert route_page(is_medical=False, confidence=0.0, threshold=0.0) is Route.CUSTOMER
    assert route_page(is_medical=True, confidence=0.99, threshold=1.0) is Route.TRIAGE


def test_story_1_9_the_threshold_is_a_setting_that_defaults_to_ninety_percent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings().gate_threshold == 0.90

    monkeypatch.setenv("WORKFLOW_GATE_THRESHOLD", "0.5")

    assert Settings().gate_threshold == 0.5


@pytest.mark.parametrize("value", ["-0.01", "1.01", "90", "nan", "high"])
def test_story_1_9_a_threshold_outside_zero_to_one_is_refused_at_start_up(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("WORKFLOW_GATE_THRESHOLD", value)

    with pytest.raises(ValidationError):
        Settings()


def test_story_1_9_the_rule_is_pure() -> None:
    # The orchestration calls it, so it may use no clock, random value or I/O.
    source = (
        inspect.getsource(gate.route_page)
        + inspect.getsource(case_status.case_status_after_gate)
        + inspect.getsource(case_status.case_status_following)
    )

    for forbidden in ("datetime", "now", "random", "await ", "open(", "store"):
        assert forbidden not in source


@pytest.mark.parametrize(
    ("routes", "stop_after", "expected"),
    [
        ([Route.EXTRACTION, Route.EXTRACTION], None, CaseStatus.RUNNING),
        ([Route.EXTRACTION, Route.CUSTOMER], None, CaseStatus.AWAITING_HUMAN),
        ([Route.TRIAGE, Route.EXTRACTION], None, CaseStatus.AWAITING_HUMAN),
        ([Route.TRIAGE, Route.CUSTOMER], None, CaseStatus.AWAITING_HUMAN),
        # A case told to stop after the gate ends there, whatever its pages wait for.
        ([Route.EXTRACTION], StopAfter.GATE, CaseStatus.COMPLETED),
        ([Route.TRIAGE, Route.CUSTOMER], StopAfter.GATE, CaseStatus.COMPLETED),
    ],
)
def test_story_1_9_the_case_status_after_the_gate(
    routes: list[Route], stop_after: StopAfter | None, expected: CaseStatus
) -> None:
    # Story 1.10: the rule reads the page statuses the routes gave.
    page_statuses = [route.page_status for route in routes]

    assert case_status_after_gate(page_statuses, stop_after) is expected


# --- The recording of a route --------------------------------------------------------


def test_story_1_9_a_route_is_recorded_as_page_routed_by_the_gate(case_id: str) -> None:
    page_id, classification_id, eval_run_id = new_id(), new_id(), new_id()

    recording = route_recording(
        case_id,
        page_id,
        classification_id,
        Route.CUSTOMER,
        THRESHOLD,
        occurred_at=NOW,
        eval_run_id=eval_run_id,
    )

    audit = recording.audit
    assert audit.action is AuditAction.PAGE_ROUTED
    assert (audit.actor_kind, audit.actor) == (ActorKind.AI, GATE_ACTOR)
    assert GATE_ACTOR == "workflow:gate"
    assert (audit.case_id, audit.page_id, audit.ref) == (
        case_id,
        page_id,
        classification_id,
    )
    assert audit.detail == RouteDetail(
        route=PageStatus.AWAITING_CUSTOMER, threshold=THRESHOLD
    )
    assert audit.occurred_at == NOW
    assert audit.eval_run_id == eval_run_id
    # The page's status change, from `classified` only; the case's status is
    # settled once every page is routed, not here.
    assert recording.page_change == PageChange(
        page_id, PageStatus.AWAITING_CUSTOMER, only_from=PageStatus.CLASSIFIED
    )
    assert recording.case_status is None
    assert recording.error_code is None


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


def test_story_1_9_a_recorded_route_moves_the_page_with_one_event_however_often(
    store: MemoryCaseStore, case_id: str
) -> None:
    stages, (first, second, _) = classified_case(store, case_id)
    ref = stages.classifications[(case_id, first)].classification_id
    events_before = len(after_start(store))

    async def route(to: Route = Route.TRIAGE) -> tuple[RecordOutcome, Route | None]:
        return await record_route(
            case_id, first, ref, to, THRESHOLD, store=store, now=lambda: NOW
        )

    outcomes = [
        asyncio.run(route()),
        asyncio.run(route()),
        # Asked again with another route: the stored one is what it answers.
        asyncio.run(route(Route.EXTRACTION)),
    ]

    # The route activity ran again: no second event, and the status is unchanged.
    assert outcomes == [
        (RecordOutcome.RECORDED, Route.TRIAGE),
        (RecordOutcome.DUPLICATE, Route.TRIAGE),
        (RecordOutcome.DUPLICATE, Route.TRIAGE),
    ]
    assert len(after_start(store)) == events_before + 1
    assert store.pages[first].page_status is PageStatus.AWAITING_TRIAGE
    assert store.pages[second].page_status is PageStatus.CLASSIFIED
    # The page's `page.routed` event comes after its `page.classified` event.
    trail = asyncio.run(store.audit_trail(case_id, 100))
    assert trail is not None
    actions = [e.action.value for e in trail.events if e.page_id == first]
    assert actions == ["page.classified", "page.routed"]


def test_story_1_9_only_a_classified_page_of_the_case_is_routed(
    store: MemoryCaseStore, case_id: str
) -> None:
    _, (first, _, _) = classified_case(store, case_id)

    async def route(page_id: str, route: Route, case: str = case_id) -> RecordOutcome:
        outcome, stored = await record_route(
            case, page_id, new_id(), route, THRESHOLD, store=store, now=lambda: NOW
        )
        assert stored is (route if outcome is RecordOutcome.RECORDED else None)
        return outcome

    assert asyncio.run(route(first, Route.TRIAGE)) is RecordOutcome.RECORDED
    # Routed already, and now under another reference: the gate does not take
    # the place of the underwriter's accept (`awaiting_triage` to `extracting`).
    assert asyncio.run(route(first, Route.EXTRACTION)) is RecordOutcome.OUT_OF_ORDER
    assert store.pages[first].page_status is PageStatus.AWAITING_TRIAGE
    for unknown in (
        route(new_id(), Route.TRIAGE),
        route(first, Route.TRIAGE, new_id()),
    ):
        with pytest.raises(DomainError) as raised:
            asyncio.run(unknown)
        assert raised.value.code is ErrorCode.NOT_FOUND


def test_story_1_9_a_failed_case_takes_no_route(
    store: MemoryCaseStore, case_id: str
) -> None:
    stages, (first, second, _) = classified_case(store, case_id)
    failed = classification_failed(case_id, second)

    async def scenario() -> tuple[RecordOutcome, Route | None]:
        # Another result fails the case; the first page is still to be routed.
        store.pages[second].page_status = PageStatus.UPLOADED
        await record_stage_result(failed, store=store)
        return await record_route(
            case_id,
            first,
            stages.classifications[(case_id, first)].classification_id,
            Route.EXTRACTION,
            THRESHOLD,
            store=store,
        )

    assert asyncio.run(scenario()) == (RecordOutcome.CASE_FAILED, None)
    assert store.pages[first].page_status is PageStatus.CLASSIFIED


def gated_case(
    store: MemoryCaseStore,
    case_id: str,
    routes: list[Route],
    parameters: StartParameters = PARAMETERS,
) -> tuple[FakeStages, list[str]]:
    """A started case whose pages the gate has routed, and that is not settled yet."""
    stages, page_ids = classified_case(
        store, case_id, pages=len(routes), parameters=parameters
    )

    async def route_all() -> None:
        for page_id, route in zip(page_ids, routes, strict=True):
            await record_route(
                case_id,
                page_id,
                stages.classifications[(case_id, page_id)].classification_id,
                route,
                THRESHOLD,
                store=store,
            )

    asyncio.run(route_all())
    return stages, page_ids


@pytest.mark.parametrize(
    ("routes", "stop_after", "kept"),
    [
        ([Route.CUSTOMER, Route.EXTRACTION], None, CaseStatus.AWAITING_HUMAN),
        ([Route.TRIAGE], None, CaseStatus.AWAITING_HUMAN),
        ([Route.EXTRACTION, Route.EXTRACTION], None, CaseStatus.RUNNING),
        ([Route.CUSTOMER, Route.EXTRACTION], StopAfter.GATE, CaseStatus.COMPLETED),
    ],
)
def test_story_1_9_the_case_is_given_its_status_after_the_gate(
    store: MemoryCaseStore,
    case_id: str,
    routes: list[Route],
    stop_after: StopAfter | None,
    kept: CaseStatus,
) -> None:
    # Story 1.10: the status is not handed in. It is worked out from the
    # stored pages, by the rule every decision uses too.
    gated_case(store, case_id, routes, replace(PARAMETERS, stop_after=stop_after))

    async def settle() -> CaseStatus:
        return (
            await settle_case_after_gate(case_id, store=store, trace_id=None)
        ).case_status

    # Safe to repeat: the second call finds the status set.
    assert [asyncio.run(settle()), asyncio.run(settle())] == [kept, kept]
    assert store.cases[case_id].case_status is kept


def test_story_1_9_a_failed_case_stays_failed_after_the_gate(
    store: MemoryCaseStore, case_id: str
) -> None:
    async def scenario() -> CaseStatus:
        await store.start(*starting(new_case(case_id, PARAMETERS, NOW)))
        await record_stage_result(redaction_failed(case_id), store=store)
        return (
            await settle_case_after_gate(case_id, store=store, trace_id=None)
        ).case_status

    assert asyncio.run(scenario()) is CaseStatus.FAILED
    with pytest.raises(DomainError) as raised:
        asyncio.run(settle_case_after_gate(new_id(), store=store, trace_id=None))
    assert raised.value.code is ErrorCode.NOT_FOUND


# --- The orchestrator ------------------------------------------------------------------


class RecordingContext:
    """Stands in for the engine's context: it notes what the orchestrator asks for."""

    def __init__(self) -> None:
        self.asked: list[dict[str, Any]] = []
        # Story 1.10: the external events the orchestrator waits for, by name.
        self.awaited: list[str] = []

    def call_activity(self, activity: str, **options: Any) -> object:
        self.asked.append({"activity": activity, **options})
        return task.CompletableTask[Any]()

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
        started,
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
    raise AssertionError("the orchestrator waited for more than it was answered")


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


def test_story_1_9_after_classification_every_page_is_routed_and_the_case_settled(
    case_id: str,
) -> None:
    eval_run_id = new_id()
    page_ids = [new_id(), new_id(), new_id(), new_id()]
    answers = [
        classified(True, 0.95),
        classified(False, 1.0),
        classified(True, 0.6),
        classified(False, 0.90),
    ]
    routes = ["extracting", "awaiting_customer", "awaiting_triage", "awaiting_customer"]
    started = {
        "case_id": case_id,
        "eval_run_id": eval_run_id,
        "classifier_contender": "llm",
    }

    asked, result, asked_at_each_wait = run_lifecycle(
        started,
        [
            CONFIRMED,
            redacted(page_ids),
            answers,
            [routed(route) for route in routes],
            settled_waiting(page_ids, routes),
            WAITING,
        ],
    )

    # One route activity per page, in document order: ids, the route the rule
    # gave and the threshold it used, and no more (AD-6).
    assert routes_asked(asked) == [
        {
            "activity": ROUTE_PAGE,
            "input": {
                "case_id": case_id,
                "eval_run_id": eval_run_id,
                "page_id": page_id,
                "classification_id": answer["classification_id"],
                "route": route,
                "threshold": THRESHOLD,
            },
            "retry_policy": RETRY,
        }
        for page_id, answer, route in zip(page_ids, answers, routes, strict=True)
    ]
    # All four were asked for together, after every page was classified.
    # After the settle nothing more is asked for: the lifecycle waits.
    assert asked_at_each_wait == [1, 2, 6, 10, 11, 11]
    # Then the case: a page waits for a person. The status is not handed
    # in: the activity works it out from the stored pages (story 1.10).
    assert asked[-1] == {
        "activity": SETTLE_CASE_AFTER_GATE,
        "input": {"case_id": case_id},
        "retry_policy": RETRY,
    }
    # The lifecycle does not end there: it waits for the decision about
    # each waiting page (story 1.10).
    assert result == waiting_for(
        decision_event(page_ids[1], PageStatus.AWAITING_CUSTOMER),
        decision_event(page_ids[2], PageStatus.AWAITING_TRIAGE),
        decision_event(page_ids[3], PageStatus.AWAITING_CUSTOMER),
    )


def test_story_1_9_a_case_whose_pages_all_go_to_extraction_goes_on_running(
    case_id: str,
) -> None:
    asked, result, _ = run_lifecycle(
        {"case_id": case_id, "classifier_contender": "llm"},
        [
            CONFIRMED,
            redacted([new_id(), new_id()]),
            [classified(True, 0.95), classified(True, 0.90)],
            [routed("extracting")] * 2,
        ],
    )

    assert [step["input"]["route"] for step in routes_asked(asked)] == [
        "extracting"
    ] * 2
    # Nothing to settle: the case is `running` already.
    assert SETTLE_CASE_AFTER_GATE not in [step["activity"] for step in asked]
    assert result == {"case_id": case_id, "case_status": "running"}


@pytest.mark.parametrize(
    ("reading", "route"),
    [((True, 0.95), "extracting"), ((False, 0.3), "awaiting_triage")],
)
def test_story_1_9_a_case_started_with_stop_after_gate_ends_completed(
    case_id: str, reading: tuple[bool, float], route: str
) -> None:
    asked, result, _ = run_lifecycle(
        {"case_id": case_id, "classifier_contender": "llm", "stop_after": "gate"},
        [
            CONFIRMED,
            redacted([new_id()]),
            [classified(*reading)],
            [routed(route)],
            {"outcome": "ok", "case_status": "completed"},
        ],
    )

    # The page is routed like any other, and then the case ends.
    assert [step["input"]["route"] for step in routes_asked(asked)] == [route]
    assert asked[-1]["input"] == {"case_id": case_id}
    assert result == {"case_id": case_id, "case_status": "completed"}


def test_story_1_9_a_case_is_routed_with_the_threshold_its_confirm_step_answered(
    case_id: str,
) -> None:
    started = {"case_id": case_id, "classifier_contender": "llm"}
    page_id = new_id()
    pages: list[object] = [redacted([page_id]), [classified(True, 0.6)]]

    strict, _, _ = run_lifecycle(
        started,
        [
            confirmed(0.9),
            *pages,
            [routed("awaiting_triage")],
            settled_waiting([page_id], ["awaiting_triage"]),
            WAITING,
        ],
    )
    lenient, result, _ = run_lifecycle(
        started, [confirmed(0.5), *pages, [routed("extracting")]]
    )

    # The threshold is in the case's history, with the confirm step's answer:
    # the orchestration has none of its own, so a replay of either case
    # routes as its first run did, whatever the setting is by then.
    assert routes_asked(strict)[0]["input"]["route"] == "awaiting_triage"
    assert routes_asked(strict)[0]["input"]["threshold"] == 0.9
    assert routes_asked(lenient)[0]["input"]["route"] == "extracting"
    assert routes_asked(lenient)[0]["input"]["threshold"] == 0.5
    assert result["case_status"] == "running"
    assert "gate_threshold" not in inspect.signature(build_case_lifecycle).parameters


@pytest.mark.parametrize(
    "answer",
    [
        {"outcome": "ok", "case_status": "running"},
        confirmed(None),
        confirmed("0.9"),
        confirmed(True),
        confirmed(1.5),
        confirmed(-0.1),
        confirmed(float("nan")),
    ],
    ids=["none", "null", "text", "bool", "over", "under", "nan"],
)
def test_story_1_9_a_case_confirmed_without_a_valid_threshold_is_marked_failed(
    case_id: str, answer: dict[str, Any]
) -> None:
    asked, result, _ = run_lifecycle(
        {"case_id": case_id, "classifier_contender": "llm"}, [answer, MARKED]
    )

    # Nothing is assumed in the threshold's place, and no stage is commanded.
    assert [step["activity"] for step in asked][1:] == [MARK_CASE_FAILED]
    assert result == {"case_id": case_id, "case_status": "failed"}


def test_story_1_9_the_case_is_settled_on_the_routes_the_trail_holds(
    case_id: str,
) -> None:
    # A retry: the route activity of the one page had recorded
    # `awaiting_triage` on an earlier run. Asked now to record `extracting`,
    # it answers with what is stored, and the case is settled on that.
    page_id = new_id()
    asked, result, _ = run_lifecycle(
        {"case_id": case_id, "classifier_contender": "llm"},
        [
            CONFIRMED,
            redacted([page_id]),
            [classified(True, 0.95)],
            [routed("awaiting_triage")],
            settled_waiting([page_id], ["awaiting_triage"]),
            WAITING,
        ],
    )

    assert routes_asked(asked)[0]["input"]["route"] == "extracting"
    assert asked[-1] == {
        "activity": SETTLE_CASE_AFTER_GATE,
        "input": {"case_id": case_id},
        "retry_policy": RETRY,
    }
    # And it waits for the decision the stored route asks for (story 1.10).
    assert result == waiting_for(decision_event(page_id, PageStatus.AWAITING_TRIAGE))


@pytest.mark.parametrize(
    "answer",
    [
        {"outcome": "ok", "case_status": "running"},
        {"outcome": "ok", "case_status": "running", "route": "denied"},
        {"outcome": "ok", "case_status": "running", "route": None},
    ],
    ids=["no-route", "not-a-route", "null"],
)
def test_story_1_9_a_route_answer_that_names_no_stored_route_settles_nothing(
    case_id: str, answer: dict[str, Any]
) -> None:
    asked, result, _ = run_lifecycle(
        {"case_id": case_id, "classifier_contender": "llm"},
        [CONFIRMED, redacted([new_id()]), [classified(False, 1.0)], [answer], MARKED],
    )

    assert SETTLE_CASE_AFTER_GATE not in [step["activity"] for step in asked]
    assert asked[-1]["activity"] == MARK_CASE_FAILED
    assert result == {"case_id": case_id, "case_status": "failed"}


def test_story_1_9_the_orchestrator_stays_deterministic(case_id: str) -> None:
    page_ids = [new_id(), new_id()]
    answers: list[object] = [
        CONFIRMED,
        redacted(page_ids),
        [classified(True, 0.95), classified(False, 0.4)],
        [routed("extracting"), routed("awaiting_triage")],
        settled_waiting(page_ids, ["extracting", "awaiting_triage"]),
        WAITING,
    ]
    started = {"case_id": case_id, "classifier_contender": "llm"}

    # Replayed from its history, the same answers lead to the same requests.
    assert run_lifecycle(started, answers) == run_lifecycle(started, answers)
    # It holds sequencing and the call of the pure rule: no clock, no random
    # value, no I/O, and no threshold or routing table of its own.
    source = inspect.getsource(orchestration.build_case_lifecycle)
    for forbidden in ("datetime", "time.", "random", "uuid", "new_id", "await "):
        assert forbidden not in source
    # The threshold comes from the history, and the comparison is the rule's alone.
    whole_module = inspect.getsource(orchestration)
    assert "0.9" not in whole_module
    assert ">=" not in whole_module
    assert "confidence <" not in whole_module
    assert "settings.gate_threshold" not in whole_module


@pytest.mark.parametrize(
    ("answer", "marked"),
    [
        # A route was refused: the page was not `classified`, or not the case's.
        ([ROUTED, {"outcome": "refused", "reason": "out_of_order"}], True),
        # Every retry of one route activity failed.
        (task.TaskFailedError("activity failed", RuntimeError("secret")), True),
        # The case failed while its pages were being routed: it has its event.
        ([ROUTED, {"outcome": "ok", "case_status": "failed"}], False),
    ],
    ids=["refused", "retries-exhausted", "case-failed"],
)
def test_story_1_9_a_route_that_cannot_be_recorded_ends_the_case_as_failed(
    case_id: str, answer: object, marked: bool
) -> None:
    about = {"case_id": case_id, "eval_run_id": None}
    answers = [
        CONFIRMED,
        redacted([new_id(), new_id()]),
        [classified(True, 0.95), classified(False, 0.95)],
        answer,
    ]

    asked, result, _ = run_lifecycle(
        {**about, "classifier_contender": "llm"},
        [*answers, MARKED] if marked else answers,
    )

    assert result == {"case_id": case_id, "case_status": "failed"}
    assert ([step["activity"] for step in asked].count(MARK_CASE_FAILED) == 1) is marked
    assert SETTLE_CASE_AFTER_GATE not in [step["activity"] for step in asked]
    if marked:
        assert asked[-1]["input"] == about


@pytest.mark.parametrize(
    "answer",
    [
        {"outcome": "ok", "case_status": "running"},
        {**classified(True, 0.95), "confidence": "high"},
        {**classified(True, 0.95), "confidence": True},
        {**classified(True, 0.95), "confidence": float("nan")},
        {**classified(True, 0.95), "confidence": 1.2},
        {**classified(True, 0.95), "confidence": -0.5},
        {**classified(True, 0.95), "is_medical": "yes"},
        {**classified(True, 0.95), "classification_id": None},
    ],
    ids=[
        "nothing",
        "confidence-text",
        "confidence-bool",
        "confidence-nan",
        "confidence-over",
        "confidence-under",
        "label-text",
        "no-id",
    ],
)
def test_story_1_9_a_classification_without_what_the_gate_needs_routes_no_page(
    case_id: str, answer: dict[str, Any]
) -> None:
    asked, result, _ = run_lifecycle(
        {"case_id": case_id, "classifier_contender": "llm"},
        [
            CONFIRMED,
            redacted([new_id(), new_id()]),
            [classified(True, 0.95), answer],
            MARKED,
        ],
    )

    # No page is routed on a guess: the case is marked failed instead.
    assert routes_asked(asked) == []
    assert asked[-1]["activity"] == MARK_CASE_FAILED
    assert result == {"case_id": case_id, "case_status": "failed"}


@pytest.mark.parametrize(
    "settled",
    [
        {"outcome": "refused", "reason": "not_found"},
        task.TaskFailedError("activity failed", RuntimeError("secret")),
    ],
    ids=["refused", "retries-exhausted"],
)
def test_story_1_9_a_case_that_cannot_be_settled_is_marked_failed(
    case_id: str, settled: object
) -> None:
    asked, result, _ = run_lifecycle(
        {"case_id": case_id, "classifier_contender": "llm"},
        [
            CONFIRMED,
            redacted([new_id()]),
            [classified(False, 1.0)],
            [routed("awaiting_customer")],
            settled,
            MARKED,
        ],
    )

    assert asked[-1]["activity"] == MARK_CASE_FAILED
    assert result == {"case_id": case_id, "case_status": "failed"}


def test_story_1_9_a_case_that_failed_before_it_was_settled_ends_as_failed(
    case_id: str,
) -> None:
    _, result, _ = run_lifecycle(
        {"case_id": case_id, "classifier_contender": "llm"},
        [
            CONFIRMED,
            redacted([new_id()]),
            [classified(False, 1.0)],
            [routed("awaiting_customer")],
            {"outcome": "ok", "case_status": "failed"},
        ],
    )

    assert result == {"case_id": case_id, "case_status": "failed"}


def test_story_1_9_the_worker_runs_the_gate_activities_under_the_names_the_engine_keeps(
    store: MemoryCaseStore, settings: Settings
) -> None:
    loop = asyncio.new_event_loop()
    try:
        activities = Activities(store, loop, 1.0, FakeStages())
        worker = build_worker(settings, activities)
    finally:
        loop.close()

    assert task.get_name(activities.route_page) == ROUTE_PAGE == "route_page"
    assert (
        task.get_name(activities.settle_case_after_gate)
        == SETTLE_CASE_AFTER_GATE
        == "settle_case_after_gate"
    )
    assert worker is not None


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


def test_story_1_9_the_confirm_activity_hands_the_case_the_threshold_in_force(
    store: MemoryCaseStore, case_id: str
) -> None:
    asyncio.run(store.start(*starting(new_case(case_id, PARAMETERS, NOW))))

    with service_loop() as loop:
        context = task.ActivityContext(case_id, 1)
        by_default = Activities(store, loop, 5.0, FakeStages()).confirm_case_started(
            context, case_id
        )
        changed = Activities(
            store, loop, 5.0, FakeStages(), 5.0, gate_threshold=0.5
        ).confirm_case_started(context, case_id)

    # The engine keeps this answer in the case's history (AD-7).
    assert by_default == {
        "outcome": "ok",
        "case_status": "running",
        "gate_threshold": Settings().gate_threshold,
    }
    assert changed["gate_threshold"] == 0.5


def test_story_1_9_the_classify_activity_hands_on_what_the_gate_needs_and_no_more(
    store: MemoryCaseStore, case_id: str
) -> None:
    stages = FakeStages(pages=1, readings={1: ("invoice", False, 0.8)})
    asyncio.run(store.start(*starting(new_case(case_id, PARAMETERS, NOW))))

    with service_loop() as loop:
        activities = Activities(store, loop, 5.0, stages, 5.0)
        (page_id,) = activities.redact_document(
            task.ActivityContext(case_id, 2), {"case_id": case_id, "eval_run_id": None}
        )["page_ids"]
        answer = activities.classify_page(
            task.ActivityContext(case_id, 3),
            {"case_id": case_id, "page_id": page_id, "contender": "llm"},
        )

    # Small values (AD-6): no page type, no reason, no text, and no route.
    assert answer == {
        "outcome": "ok",
        "case_status": "running",
        "classification_id": stages.classifications[
            (case_id, page_id)
        ].classification_id,
        "is_medical": False,
        "confidence": 0.8,
    }
    # The activity itself routes nothing (AD-7).
    assert store.pages[page_id].page_status is PageStatus.CLASSIFIED
    assert CLASSIFY_PAGE == "classify_page"


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


def test_story_1_9_the_route_activity_records_the_route_once_however_often_it_runs(
    store: MemoryCaseStore, case_id: str
) -> None:
    stages, (first, _, _) = classified_case(store, case_id)
    eval_run_id = new_id()
    command = route_command(
        stages, case_id, first, "awaiting_customer", eval_run_id=eval_run_id
    )
    events_before = len(after_start(store))

    with service_loop() as loop:
        activities = Activities(store, loop, 5.0, stages, 5.0)
        answers = [
            activities.route_page(task.ActivityContext(case_id, 4), command)
            for _ in range(2)
        ]

    # Each answer names the route the trail holds for the page.
    assert answers == [routed("awaiting_customer")] * 2
    assert store.pages[first].page_status is PageStatus.AWAITING_CUSTOMER
    assert len(after_start(store)) == events_before + 1
    audit = after_start(store)[-1][1].audit
    assert (audit.action.value, audit.actor, audit.actor_kind.value) == (
        "page.routed",
        "workflow:gate",
        "ai",
    )
    assert audit.ref == command["classification_id"]
    assert audit.model_dump(mode="json")["detail"] == {
        "route": "awaiting_customer",
        "threshold": 0.9,
    }
    assert audit.eval_run_id == eval_run_id
    # The route changes the page only: the case is settled by its own activity.
    assert store.cases[case_id].case_status is CaseStatus.RUNNING


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


def test_story_1_9_the_route_activity_answers_what_no_retry_can_mend(
    store: MemoryCaseStore, case_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    stages, (first, second, _) = classified_case(store, case_id)
    command = route_command(
        stages, case_id, first, "extracting", classification_id=new_id()
    )
    refused_as_invalid = {"outcome": "refused", "reason": "validation_failed"}

    def without(key: str) -> dict[str, Any]:
        return {name: value for name, value in command.items() if name != key}

    with service_loop() as loop:
        activities = Activities(store, loop, 5.0, stages, 5.0)
        context = task.ActivityContext(case_id, 4)
        invalid = [
            activities.route_page(context, broken)
            for broken in (
                {**command, "route": "discarded"},
                # Anything missing ...
                without("route"),
                without("threshold"),
                without("case_id"),
                without("page_id"),
                without("classification_id"),
                # ... or of the wrong kind ...
                {**command, "page_id": None},
                {**command, "classification_id": 7},
                # ... or a threshold that is no number from 0 to 1.
                {**command, "threshold": "0.9"},
                {**command, "threshold": True},
                {**command, "threshold": 1.5},
                {**command, "threshold": -0.1},
                {**command, "threshold": float("nan")},
                {**command, "threshold": float("inf")},
            )
        ]
        unknown_page = activities.route_page(context, {**command, "page_id": new_id()})
        unknown_case = activities.route_page(context, {**command, "case_id": new_id()})
        store.pages[second].page_status = PageStatus.UPLOADED
        not_classified = activities.route_page(context, {**command, "page_id": second})
        store.cases[case_id] = replace(
            store.cases[case_id], case_status=CaseStatus.FAILED
        )
        case_failed = activities.route_page(context, command)

    # Answered, never raised: the engine would only run the activity again.
    assert invalid == [refused_as_invalid] * len(invalid)
    assert unknown_page == {"outcome": "refused", "reason": "not_found"}
    assert unknown_case == {"outcome": "refused", "reason": "not_found"}
    assert not_classified == {"outcome": "refused", "reason": "out_of_order"}
    assert case_failed == {"outcome": "ok", "case_status": "failed"}
    assert store.pages[first].page_status is PageStatus.CLASSIFIED
    assert "page.routed" not in {
        recording.audit.action.value for _, recording in after_start(store)
    }
    assert "retry=True" not in caplog.text


def test_story_1_9_a_route_that_cannot_be_written_fails_the_activity_for_a_retry(
    store: MemoryCaseStore, case_id: str
) -> None:
    stages, (first, _, _) = classified_case(store, case_id)
    command = route_command(stages, case_id, first, "awaiting_triage")
    store.fail_audit_insert = True

    with service_loop() as loop:
        activities = Activities(store, loop, 5.0, stages, 5.0)
        with pytest.raises(ActivityFailed):
            activities.route_page(task.ActivityContext(case_id, 4), command)
        # AD-8: the status change and its event are written together or not at all.
        assert store.pages[first].page_status is PageStatus.CLASSIFIED
        store.fail_audit_insert = False
        answer = activities.route_page(task.ActivityContext(case_id, 4), command)

    assert answer == routed("awaiting_triage")
    assert store.pages[first].page_status is PageStatus.AWAITING_TRIAGE


def test_story_1_9_the_settle_activity_gives_the_case_its_status_after_the_gate(
    store: MemoryCaseStore, case_id: str
) -> None:
    stages, page_ids = gated_case(store, case_id, [Route.CUSTOMER, Route.EXTRACTION])
    settled = {"case_id": case_id}

    with service_loop() as loop:
        activities = Activities(store, loop, 5.0, stages, 5.0)
        context = task.ActivityContext(case_id, 5)
        answers = [
            activities.settle_case_after_gate(context, settled) for _ in range(2)
        ]
        unknown = activities.settle_case_after_gate(context, {"case_id": new_id()})
        store.fail = True
        with pytest.raises(ActivityFailed):
            activities.settle_case_after_gate(context, settled)

    # The case's status, and each page's as the settle read it (story
    # 1.10): ids and statuses only (AD-6).
    assert (
        answers == [settled_waiting(page_ids, ["awaiting_customer", "extracting"])] * 2
    )
    assert unknown == {"outcome": "refused", "reason": "not_found"}
    assert store.cases[case_id].case_status is CaseStatus.AWAITING_HUMAN


@pytest.mark.parametrize(
    "settled",
    [{}, {"case_id": None}, {"case_id": 7}],
    ids=["missing", "null", "not-an-id"],
)
def test_story_1_9_the_settle_activity_refuses_a_command_that_names_no_case(
    store: MemoryCaseStore, case_id: str, settled: dict[str, Any]
) -> None:
    stages, _ = gated_case(store, case_id, [Route.CUSTOMER])

    with service_loop() as loop:
        activities = Activities(store, loop, 5.0, stages, 5.0)
        answer = activities.settle_case_after_gate(
            task.ActivityContext(case_id, 5), settled
        )

    assert answer == {"outcome": "refused", "reason": "validation_failed"}
    assert store.cases[case_id].case_status is CaseStatus.RUNNING


@pytest.mark.parametrize("handed_in", ["failed", "completed", "running", "paused"])
def test_story_1_9_the_settle_activity_takes_no_status_from_its_caller(
    store: MemoryCaseStore, case_id: str, handed_in: str
) -> None:
    # Never `failed`, which only a failed stage or the lifecycle's own
    # failure gives a case, with its event; and nothing else either: the
    # status comes from the stored pages alone (story 1.10).
    stages, page_ids = gated_case(store, case_id, [Route.TRIAGE, Route.EXTRACTION])

    with service_loop() as loop:
        activities = Activities(store, loop, 5.0, stages, 5.0)
        answer = activities.settle_case_after_gate(
            task.ActivityContext(case_id, 5),
            {"case_id": case_id, "case_status": handed_in},
        )

    assert answer == settled_waiting(page_ids, ["awaiting_triage", "extracting"])
    assert store.cases[case_id].case_status is CaseStatus.AWAITING_HUMAN


# --- Progress ----------------------------------------------------------------------------


def test_story_1_9_progress_shows_the_routed_statuses_and_no_failure_reason(
    client: TestClient, store: MemoryCaseStore, case_id: str
) -> None:
    stages, page_ids = classified_case(
        store, case_id, readings={2: ("invoice", False, 1.0), 3: ("other", False, 0.6)}
    )

    async def gate_the_case() -> None:
        for page_id, route in zip(
            page_ids, [Route.EXTRACTION, Route.CUSTOMER, Route.TRIAGE], strict=True
        ):
            await record_route(
                case_id,
                page_id,
                stages.classifications[(case_id, page_id)].classification_id,
                route,
                THRESHOLD,
                store=store,
            )
        await settle_case_after_gate(case_id, store=store, trace_id=None)

    asyncio.run(gate_the_case())

    response = client.get(f"/cases/{case_id}/progress")

    assert response.status_code == 200
    assert response.json() == {
        "case_id": case_id,
        "case_status": "awaiting_human",
        "redaction_status": "done",
        "pages": [
            {
                "page_id": page_id,
                "page_number": number,
                "page_status": status,
                "error_code": None,
            }
            for number, (page_id, status) in enumerate(
                zip(
                    page_ids,
                    ["extracting", "awaiting_customer", "awaiting_triage"],
                    strict=True,
                ),
                start=1,
            )
        ],
        "error_code": None,
    }


def test_story_1_9_progress_of_a_case_whose_page_stage_failed_names_the_code_on_both(
    client: TestClient, store: MemoryCaseStore, case_id: str
) -> None:
    stages = FakeStages(
        pages=2,
        failing_page_numbers=frozenset({2}),
        classification_error_code="invalid_model_output",
    )

    async def scenario() -> None:
        await store.start(*starting(new_case(case_id, PARAMETERS, NOW)))
        redacted_result = await stages.redact_document(
            case_id, eval_run_id=None, trace_context={}
        )
        await record_stage_result(redacted_result, store=store)
        for page_id in redacted_result.page_ids:
            await record_stage_result(
                await stages.classify_page(
                    case_id,
                    page_id,
                    ClassifierContender.LLM,
                    eval_run_id=None,
                    trace_context={},
                ),
                store=store,
            )

    asyncio.run(scenario())

    progress = CaseProgress.model_validate(
        client.get(f"/cases/{case_id}/progress").json()
    )

    assert progress.case_status is CaseStatus.FAILED
    assert progress.error_code is ErrorCode.INVALID_MODEL_OUTPUT
    assert [(page.page_status.value, page.error_code) for page in progress.pages] == [
        ("classified", None),
        ("failed", ErrorCode.INVALID_MODEL_OUTPUT),
    ]


def test_story_1_9_progress_of_a_case_whose_redaction_failed_names_the_code_on_the_case(
    client: TestClient, store: MemoryCaseStore, case_id: str
) -> None:
    async def scenario() -> None:
        await store.start(*starting(new_case(case_id, PARAMETERS, NOW)))
        await record_stage_result(
            redaction_failed(case_id, "stage_timeout"), store=store
        )

    asyncio.run(scenario())

    body = client.get(f"/cases/{case_id}/progress").json()

    assert body["case_status"] == "failed"
    assert body["error_code"] == "stage_timeout"
    assert body["pages"] == []
