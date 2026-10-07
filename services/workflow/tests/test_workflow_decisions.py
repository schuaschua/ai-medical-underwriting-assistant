"""Story 1.10: the one decision operation, the case status that follows the pages, and the waits.

Unit tests: the rule of who may decide what, the recording of a decision,
the route, the orchestrator's waits for decisions and the engine's event.
No scheduler, no database, no network.
"""

import asyncio
import inspect
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import grpc
import pytest
from durabletask import task
from durabletask.client import OrchestrationStatus
from fastapi.testclient import TestClient
from workflow_fakes import (
    TRACE_ID,
    FakeEngine,
    FakeStages,
    MemoryCaseStore,
    StoreDown,
    classification_failed,
)

import workflow
from contracts.audit import HUMAN_ACTIONS
from contracts.decisions import DECISION_RULES
from contracts.enums import (
    CaseStatus,
    ClassifierContender,
    Decision,
    DemoRole,
    PageStatus,
    RetrieverConfig,
    Service,
    StopAfter,
)
from contracts.errors import DomainError, ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.workflow import DecisionRecorded, DecisionRequest
from workflow.adapters import orchestration, scheduler
from workflow.adapters.orchestration import (
    MARK_CASE_FAILED,
    SETTLE_CASE_AFTER_GATE,
    build_case_lifecycle,
    decision_event,
)
from workflow.adapters.scheduler import SchedulerEngine
from workflow.domain import case_status as case_status_rule
from workflow.domain import decisions
from workflow.domain.case_status import (
    FINAL_PAGE_STATUSES,
    case_status_after_gate,
    case_status_following,
)
from workflow.domain.cases import (
    record_route,
    record_stage_result,
    settle_case_after_gate,
)
from workflow.domain.decisions import (
    authorise,
    case_takes_decisions,
    record_decision,
    status_a_decision_leaves,
)
from workflow.domain.entities import StartParameters
from workflow.domain.gate import Route
from workflow.domain.lifecycle import new_case

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
THRESHOLD = 0.90
PARAMETERS = StartParameters(
    classifier_contender=ClassifierContender.LLM,
    retriever_configs=(RetrieverConfig.R3,),
    stop_after=None,
    eval_run_id=None,
)
RETRY = task.RetryPolicy(
    first_retry_interval=timedelta(seconds=1), max_number_of_attempts=3
)
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"
# What is not a person: every service, the gate, a model deployment, and
# anything that only looks like a demo role.
NOT_HUMAN = [
    *(service.value for service in Service),
    "workflow:gate",
    "classification:chat-main",
    "Customer",
    "admin",
    "customer ",
]


def gated_case(
    store: MemoryCaseStore,
    case_id: str,
    routes: list[Route],
    *,
    parameters: StartParameters = PARAMETERS,
    settled: bool = True,
) -> list[str]:
    """A started case whose pages the gate has routed; returns the page ids, in page order."""
    stages = FakeStages(pages=len(routes))

    async def scenario() -> list[str]:
        await store.start(new_case(case_id, parameters, NOW))
        redacted = await stages.redact_document(
            case_id, eval_run_id=parameters.eval_run_id, trace_context={}
        )
        await record_stage_result(redacted, store=store)
        for page_id, route in zip(redacted.page_ids, routes, strict=True):
            result = await stages.classify_page(
                case_id,
                page_id,
                ClassifierContender.LLM,
                eval_run_id=parameters.eval_run_id,
                trace_context={},
            )
            await record_stage_result(result, store=store)
            await record_route(
                case_id,
                page_id,
                result.classification_id,
                route,
                THRESHOLD,
                store=store,
            )
        if settled:
            await settle_case_after_gate(case_id, store=store)
        return list(redacted.page_ids)

    return asyncio.run(scenario())


def decide(
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    page_id: str,
    decision: str,
    actor: str,
) -> DecisionRecorded:
    return asyncio.run(
        record_decision(
            case_id,
            page_id,
            DecisionRequest.model_validate({"decision": decision, "actor": actor}),
            store=store,
            engine=engine,
            trace_id=TRACE_ID,
            now=lambda: NOW,
        )
    )


def refused(
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    page_id: str,
    decision: str,
    actor: str,
) -> ErrorCode:
    with pytest.raises(DomainError) as raised:
        decide(store, engine, case_id, page_id, decision, actor)
    return raised.value.code


def snapshot(store: MemoryCaseStore) -> tuple[Any, ...]:
    """Everything a decision could change, to show that a refusal changed nothing."""
    return (
        dict(store.cases),
        {page_id: page.page_status for page_id, page in store.pages.items()},
        len(store.events),
        list(store.decisions),
    )


# --- The rule: the case status follows the pages ----------------------------------


@pytest.mark.parametrize(
    ("page_statuses", "expected"),
    [
        # Any page awaiting a human: the case waits for a human.
        (["awaiting_customer", "extracting"], "awaiting_human"),
        (["awaiting_triage", "discarded"], "awaiting_human"),
        (["awaiting_customer", "awaiting_triage"], "awaiting_human"),
        (["awaiting_triage", "uploaded"], "awaiting_human"),
        # None waits, and a page is still in work: running.
        (["extracting", "discarded"], "running"),
        (["extracting", "extracting"], "running"),
        (["classified", "denied"], "running"),
        (["uploaded"], "running"),
        # Every page final: completed.
        (["discarded"], "completed"),
        (["discarded", "denied", "extracted"], "completed"),
        (["extracted", "extracted"], "completed"),
        # No page yet: its pages are still to come.
        ([], "running"),
    ],
)
def test_story_1_10_the_case_status_follows_the_pages(
    page_statuses: list[str], expected: str
) -> None:
    statuses = [PageStatus(status) for status in page_statuses]

    assert case_status_following(statuses) is CaseStatus(expected)
    # After the gate the same rule holds, unless the case was told to stop there.
    assert case_status_after_gate(statuses, None) is CaseStatus(expected)
    assert case_status_after_gate(statuses, StopAfter.GATE) is CaseStatus.COMPLETED


def test_story_1_10_the_rule_is_pure_and_names_no_status_table_of_its_own() -> None:
    source = inspect.getsource(case_status_rule)

    # The orchestration calls it: no clock, no random value, no I/O.
    for forbidden in ("datetime", "random", "await ", "open(", "import logging"):
        assert forbidden not in source
    # Which statuses are final comes from the transition table, and which
    # await a person from the contracts' mapping.
    assert FINAL_PAGE_STATUSES == {
        PageStatus.EXTRACTED,
        PageStatus.DISCARDED,
        PageStatus.DENIED,
        PageStatus.FAILED,
    }


# --- The rule: who may decide what ---------------------------------------------------


@pytest.mark.parametrize(
    ("actor", "decision"),
    [
        ("customer", "keep"),
        ("customer", "discard"),
        ("underwriter", "accept"),
        ("underwriter", "deny"),
    ],
)
def test_story_1_10_each_role_may_make_its_own_two_decisions(
    actor: str, decision: str
) -> None:
    role, rule = authorise(actor, Decision(decision))

    assert role is DemoRole(actor)
    assert rule is DECISION_RULES[Decision(decision)]


@pytest.mark.parametrize(
    ("actor", "decision"),
    [
        ("customer", "accept"),
        ("customer", "deny"),
        ("underwriter", "keep"),
        ("underwriter", "discard"),
    ],
)
def test_story_1_10_a_role_deciding_what_is_not_its_own_is_role_not_allowed(
    actor: str, decision: str
) -> None:
    with pytest.raises(DomainError) as raised:
        authorise(actor, Decision(decision))

    assert raised.value.code is ErrorCode.ROLE_NOT_ALLOWED
    assert raised.value.http_status == 403


@pytest.mark.parametrize("actor", NOT_HUMAN)
@pytest.mark.parametrize("decision", list(Decision))
def test_story_1_10_an_actor_that_is_not_a_demo_role_is_actor_not_human(
    actor: str, decision: Decision
) -> None:
    with pytest.raises(DomainError) as raised:
        authorise(actor, decision)

    assert raised.value.code is ErrorCode.ACTOR_NOT_HUMAN
    assert raised.value.http_status == 403


@pytest.mark.parametrize(
    ("case_status", "stop_after", "takes"),
    [
        (CaseStatus.AWAITING_HUMAN, None, True),
        (CaseStatus.RUNNING, None, True),
        (CaseStatus.FAILED, None, False),
        (CaseStatus.COMPLETED, None, False),
        # AD-17: nobody decides the pages of a bake-off run.
        (CaseStatus.RUNNING, StopAfter.GATE, False),
        (CaseStatus.COMPLETED, StopAfter.GATE, False),
    ],
)
def test_story_1_10_a_failed_completed_or_stopped_case_takes_no_decision(
    case_status: CaseStatus, stop_after: StopAfter | None, takes: bool
) -> None:
    assert case_takes_decisions(case_status, stop_after) is takes


# --- Recording a decision ---------------------------------------------------------


@pytest.mark.parametrize(
    ("route", "decision", "actor", "page_status", "action"),
    [
        (Route.CUSTOMER, "discard", "customer", "discarded", "page.discarded"),
        (Route.CUSTOMER, "keep", "customer", "awaiting_triage", "page.kept"),
        (Route.TRIAGE, "accept", "underwriter", "extracting", "page.accepted"),
        (Route.TRIAGE, "deny", "underwriter", "denied", "page.denied"),
    ],
)
def test_story_1_10_a_decision_is_stored_with_its_status_change_and_its_audit_event(
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    route: Route,
    decision: str,
    actor: str,
    page_status: str,
    action: str,
) -> None:
    eval_run_id = new_id()
    page_id, other = gated_case(
        store,
        case_id,
        [route, Route.EXTRACTION],
        parameters=replace(PARAMETERS, eval_run_id=eval_run_id),
    )
    events_before = len(store.events)

    recorded = decide(store, engine, case_id, page_id, decision, actor)

    # The answer: the stored decision and the status it left the page in.
    stored = store.decisions[0]
    assert recorded.model_dump(mode="json") == {
        "decision_id": stored.decision_id,
        "case_id": case_id,
        "page_id": page_id,
        "decision": decision,
        "actor": actor,
        "page_status": page_status,
        "occurred_at": "2026-10-07T09:00:00Z",
    }
    # One row, one status change, one event: actor kind `human`, the actor
    # the demo role, the reference the decision's id (AD-8, AD-10).
    assert len(store.decisions) == 1
    assert store.pages[page_id].page_status is PageStatus(page_status)
    assert store.pages[other].page_status is PageStatus.EXTRACTING
    assert len(store.events) == events_before + 1
    audit = store.events[-1][1].audit
    assert (audit.actor_kind.value, audit.actor, audit.action.value) == (
        "human",
        actor,
        action,
    )
    assert audit.action in HUMAN_ACTIONS
    assert (audit.case_id, audit.page_id, audit.ref) == (
        case_id,
        page_id,
        stored.decision_id,
    )
    assert audit.occurred_at == NOW
    assert audit.detail is None
    assert audit.trace_id == TRACE_ID
    # The event belongs to the eval run its case belongs to.
    assert audit.eval_run_id == eval_run_id
    # Then the orchestration is told, with the status the page had.
    assert engine.told == [
        (case_id, page_id, route.page_status, Decision(decision)),
    ]


def test_story_1_10_discard_and_keep_leave_the_trail_the_acceptance_criterion_names(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    first, second, _ = gated_case(
        store, case_id, [Route.CUSTOMER, Route.CUSTOMER, Route.EXTRACTION]
    )

    decide(store, engine, case_id, first, "discard", "customer")
    decide(store, engine, case_id, second, "keep", "customer")

    assert store.pages[first].page_status is PageStatus.DISCARDED
    assert store.pages[second].page_status is PageStatus.AWAITING_TRIAGE
    trail = asyncio.run(store.audit_trail(case_id, 100))
    assert trail is not None
    decided = [
        (event.action.value, event.actor, event.actor_kind.value, event.page_id)
        for event in trail.events
        if event.action in HUMAN_ACTIONS
    ]
    assert decided == [
        ("page.discarded", "customer", "human", first),
        ("page.kept", "customer", "human", second),
    ]
    # The kept page waits for the underwriter: the case still waits for a human.
    assert store.cases[case_id].case_status is CaseStatus.AWAITING_HUMAN


@pytest.mark.parametrize(
    ("route", "decision", "actor"),
    [
        # Not the status the decision needs.
        (Route.TRIAGE, "keep", "customer"),
        (Route.TRIAGE, "discard", "customer"),
        (Route.CUSTOMER, "accept", "underwriter"),
        (Route.CUSTOMER, "deny", "underwriter"),
        (Route.EXTRACTION, "discard", "customer"),
        (Route.EXTRACTION, "accept", "underwriter"),
    ],
)
def test_story_1_10_a_page_not_awaiting_the_decision_is_409_and_nothing_changes(
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    route: Route,
    decision: str,
    actor: str,
) -> None:
    (page_id,) = gated_case(store, case_id, [route])
    before = snapshot(store)

    code = refused(store, engine, case_id, page_id, decision, actor)

    assert code is ErrorCode.NOT_AWAITING_DECISION
    assert DomainError(code, "x").http_status == 409
    assert snapshot(store) == before
    assert engine.told == []


@pytest.mark.parametrize("page_status", ["uploaded", "classified", "failed"])
def test_story_1_10_a_page_the_gate_has_not_routed_takes_no_decision(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str, page_status: str
) -> None:
    (page_id,) = gated_case(store, case_id, [Route.CUSTOMER])
    store.pages[page_id].page_status = PageStatus(page_status)
    before = snapshot(store)

    for decision, actor in [("keep", "customer"), ("accept", "underwriter")]:
        assert (
            refused(store, engine, case_id, page_id, decision, actor)
            is ErrorCode.NOT_AWAITING_DECISION
        )
    assert snapshot(store) == before


def test_story_1_10_a_page_of_a_failed_case_takes_no_decision(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    waiting, _, third = gated_case(
        store, case_id, [Route.CUSTOMER, Route.TRIAGE, Route.EXTRACTION]
    )
    # A later stage fails another page, and with it the case; the waiting
    # pages stay as they were (story 1.9's deferred item).
    asyncio.run(record_stage_result(classification_failed(case_id, third), store=store))
    assert store.cases[case_id].case_status is CaseStatus.FAILED
    assert store.pages[waiting].page_status is PageStatus.AWAITING_CUSTOMER
    before = snapshot(store)

    assert (
        refused(store, engine, case_id, waiting, "discard", "customer")
        is ErrorCode.NOT_AWAITING_DECISION
    )
    assert snapshot(store) == before
    assert engine.told == []


def test_story_1_10_a_case_started_with_stop_after_gate_ends_completed_and_takes_no_decision(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    parameters = replace(PARAMETERS, stop_after=StopAfter.GATE)
    customer, triage = gated_case(
        store,
        case_id,
        [Route.CUSTOMER, Route.TRIAGE],
        parameters=parameters,
        settled=False,
    )

    # Not even between the gate and the settle, while the case is `running`.
    assert store.cases[case_id].case_status is CaseStatus.RUNNING
    assert (
        refused(store, engine, case_id, customer, "keep", "customer")
        is ErrorCode.NOT_AWAITING_DECISION
    )
    settled = asyncio.run(settle_case_after_gate(case_id, store=store))
    assert settled.case_status is CaseStatus.COMPLETED
    before = snapshot(store)
    assert (
        refused(store, engine, case_id, triage, "deny", "underwriter")
        is ErrorCode.NOT_AWAITING_DECISION
    )
    assert snapshot(store) == before
    assert store.decisions == []


@pytest.mark.parametrize(
    ("actor", "decision"),
    [("customer", "accept"), ("underwriter", "keep")],
)
def test_story_1_10_the_wrong_role_is_403_and_nothing_changes(
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    actor: str,
    decision: str,
) -> None:
    customer, triage = gated_case(store, case_id, [Route.CUSTOMER, Route.TRIAGE])
    before = snapshot(store)

    for page_id in (customer, triage):
        assert (
            refused(store, engine, case_id, page_id, decision, actor)
            is ErrorCode.ROLE_NOT_ALLOWED
        )
    assert snapshot(store) == before
    assert engine.told == []


@pytest.mark.parametrize("actor", NOT_HUMAN)
def test_story_1_10_an_actor_that_is_not_human_is_403_and_nothing_changes(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str, actor: str
) -> None:
    customer, triage = gated_case(store, case_id, [Route.CUSTOMER, Route.TRIAGE])
    before = snapshot(store)

    for page_id, decision in [
        (customer, "keep"),
        (customer, "discard"),
        (triage, "accept"),
        (triage, "deny"),
    ]:
        assert (
            refused(store, engine, case_id, page_id, decision, actor)
            is ErrorCode.ACTOR_NOT_HUMAN
        )
    assert snapshot(store) == before
    assert engine.told == []


def test_story_1_10_the_same_decision_again_is_answered_with_the_stored_one(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    page_id, _ = gated_case(store, case_id, [Route.CUSTOMER, Route.EXTRACTION])
    first = decide(store, engine, case_id, page_id, "discard", "customer")
    after_first = snapshot(store)

    async def again() -> DecisionRecorded:
        # Later, and it would be given another id: neither is stored.
        return await record_decision(
            case_id,
            page_id,
            DecisionRequest.model_validate(
                {"decision": "discard", "actor": "customer"}
            ),
            store=store,
            engine=engine,
            now=lambda: NOW + timedelta(minutes=5),
        )

    second = asyncio.run(again())

    # The stored decision; no second row and no second event.
    assert second == first
    assert snapshot(store) == after_first
    assert len(store.decisions) == 1
    # And the event is raised again.
    assert (
        engine.told
        == [(case_id, page_id, PageStatus.AWAITING_CUSTOMER, Decision.DISCARD)] * 2
    )


def test_story_1_10_another_decision_after_one_was_made_is_409(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    discarded, kept, denied = gated_case(
        store, case_id, [Route.CUSTOMER, Route.CUSTOMER, Route.TRIAGE]
    )
    decide(store, engine, case_id, discarded, "discard", "customer")
    decide(store, engine, case_id, kept, "keep", "customer")
    decide(store, engine, case_id, denied, "deny", "underwriter")
    before = snapshot(store)
    told = list(engine.told)

    # Keep after discard, discard after keep, accept after deny, and an
    # underwriter's answer for a page the customer discarded.
    for page_id, decision, actor in [
        (discarded, "keep", "customer"),
        (kept, "discard", "customer"),
        (denied, "accept", "underwriter"),
        (discarded, "accept", "underwriter"),
    ]:
        assert (
            refused(store, engine, case_id, page_id, decision, actor)
            is ErrorCode.NOT_AWAITING_DECISION
        )
    assert snapshot(store) == before
    assert engine.told == told


def test_story_1_10_a_kept_page_is_the_underwriters_to_accept_or_deny(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    (page_id,) = gated_case(store, case_id, [Route.CUSTOMER])

    decide(store, engine, case_id, page_id, "keep", "customer")
    accepted = decide(store, engine, case_id, page_id, "accept", "underwriter")

    # No extraction yet (story 2.4): the accepted page stays `extracting`.
    assert accepted.page_status is PageStatus.EXTRACTING
    assert store.pages[page_id].page_status is PageStatus.EXTRACTING
    assert [stored.decision.value for stored in store.decisions] == ["keep", "accept"]
    assert [told[2:] for told in engine.told] == [
        (PageStatus.AWAITING_CUSTOMER, Decision.KEEP),
        (PageStatus.AWAITING_TRIAGE, Decision.ACCEPT),
    ]
    # The keep is still answered with the stored one, and names what it left.
    again = decide(store, engine, case_id, page_id, "keep", "customer")
    assert again.page_status is PageStatus.AWAITING_TRIAGE
    assert len(store.decisions) == 2


def test_story_1_10_an_unknown_case_or_page_is_404(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    (page_id,) = gated_case(store, case_id, [Route.CUSTOMER])
    other_case = new_id()
    (other_page,) = gated_case(store, other_case, [Route.CUSTOMER])
    before = snapshot(store)

    for case, page in [
        (new_id(), page_id),
        (case_id, new_id()),
        # A page of another case is not this case's page.
        (case_id, other_page),
    ]:
        code = refused(store, engine, case, page, "discard", "customer")
        assert code is ErrorCode.NOT_FOUND
        assert DomainError(code, "x").http_status == 404
    assert snapshot(store) == before
    assert engine.told == []


def test_story_1_10_a_decision_is_written_whole_or_not_at_all(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    (page_id,) = gated_case(store, case_id, [Route.CUSTOMER])
    before = snapshot(store)
    store.fail_audit_insert = True

    with pytest.raises(StoreDown):
        decide(store, engine, case_id, page_id, "discard", "customer")

    # No row, no status change, no case status change, and nobody was told.
    assert snapshot(store) == before
    assert engine.told == []


# --- The case status follows -----------------------------------------------------------


@pytest.mark.parametrize(
    ("routes", "decided", "statuses"),
    [
        # The last waiting page is discarded and a page is still in work.
        (
            [Route.CUSTOMER, Route.EXTRACTION],
            [(0, "discard", "customer")],
            ["running"],
        ),
        # Every page is final.
        (
            [Route.CUSTOMER, Route.TRIAGE],
            [(0, "discard", "customer"), (1, "deny", "underwriter")],
            ["awaiting_human", "completed"],
        ),
        # A kept page still waits; accepted, it is in work.
        (
            [Route.CUSTOMER],
            [(0, "keep", "customer"), (0, "accept", "underwriter")],
            ["awaiting_human", "running"],
        ),
        (
            [Route.CUSTOMER],
            [(0, "keep", "customer"), (0, "deny", "underwriter")],
            ["awaiting_human", "completed"],
        ),
        (
            [Route.CUSTOMER, Route.CUSTOMER],
            [(0, "discard", "customer"), (1, "discard", "customer")],
            ["awaiting_human", "completed"],
        ),
    ],
)
def test_story_1_10_the_case_status_follows_as_the_pages_are_decided(
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    routes: list[Route],
    decided: list[tuple[int, str, str]],
    statuses: list[str],
) -> None:
    page_ids = gated_case(store, case_id, routes)
    assert store.cases[case_id].case_status is CaseStatus.AWAITING_HUMAN

    seen = []
    for index, decision, actor in decided:
        decide(store, engine, case_id, page_ids[index], decision, actor)
        seen.append(store.cases[case_id].case_status.value)

    assert seen == statuses


def test_story_1_10_a_late_retry_of_the_settle_cannot_undo_a_decision(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    # Story 1.9's deferred item. The settle ran, a decision moved the case
    # on to `running`, and then the engine runs the settle activity again.
    page_id, _ = gated_case(store, case_id, [Route.TRIAGE, Route.EXTRACTION])
    decide(store, engine, case_id, page_id, "accept", "underwriter")
    assert store.cases[case_id].case_status is CaseStatus.RUNNING

    late = asyncio.run(settle_case_after_gate(case_id, store=store))

    assert late.case_status is CaseStatus.RUNNING
    # It reports the pages as the decision left them.
    assert late.page_statuses[page_id] is PageStatus.EXTRACTING
    assert store.cases[case_id].case_status is CaseStatus.RUNNING


def test_story_1_10_a_decision_made_before_the_settle_is_kept_by_it(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    # The customer answers as soon as the page shows, before the lifecycle
    # has settled the case.
    first, second = gated_case(
        store, case_id, [Route.CUSTOMER, Route.CUSTOMER], settled=False
    )
    assert store.cases[case_id].case_status is CaseStatus.RUNNING

    decide(store, engine, case_id, first, "discard", "customer")
    assert store.cases[case_id].case_status is CaseStatus.AWAITING_HUMAN
    decide(store, engine, case_id, second, "discard", "customer")
    assert store.cases[case_id].case_status is CaseStatus.COMPLETED

    settled = asyncio.run(settle_case_after_gate(case_id, store=store))
    assert settled.case_status is CaseStatus.COMPLETED
    assert set(settled.page_statuses.values()) == {PageStatus.DISCARDED}


# --- The event -----------------------------------------------------------------------


def test_story_1_10_a_decision_whose_event_could_not_be_raised_is_not_lost(
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (page_id,) = gated_case(store, case_id, [Route.CUSTOMER])
    engine.fail_events = True

    with caplog.at_level(logging.INFO):
        code = refused(store, engine, case_id, page_id, "keep", "customer")

    # The caller is told to try again; the decision itself is stored.
    assert code is ErrorCode.UPSTREAM_UNAVAILABLE
    assert store.pages[page_id].page_status is PageStatus.AWAITING_TRIAGE
    assert len(store.decisions) == 1
    assert engine.told == []
    # security rule 31: ids and the error's type, never its message.
    assert "decision event not raised" in caplog.text
    assert "type=StoreDown" in caplog.text
    assert "secret-store-detail" not in caplog.text

    # The same decision again: the stored one, and the event is raised now.
    engine.fail_events = False
    again = decide(store, engine, case_id, page_id, "keep", "customer")

    assert again.decision_id == store.decisions[0].decision_id
    assert len(store.decisions) == 1
    assert engine.told == [
        (case_id, page_id, PageStatus.AWAITING_CUSTOMER, Decision.KEEP)
    ]


@dataclass
class Instance:
    runtime_status: OrchestrationStatus


class FakeRpcError(grpc.RpcError):  # type: ignore[misc]  # grpc ships no type hints
    def __init__(self, status: grpc.StatusCode) -> None:
        super().__init__("secret-scheduler-detail")
        self._status = status

    def code(self) -> grpc.StatusCode:
        return self._status


@dataclass
class FakeSchedulerClient:
    existing: Instance | None = None
    raised: list[tuple[str, str, Any]] = field(default_factory=list)
    fail: bool = False
    # What the scheduler answers a raise with instead of taking it.
    refusal: grpc.StatusCode | None = None

    def get_orchestration_state(
        self, instance_id: str, **options: Any
    ) -> Instance | None:
        return self.existing

    def raise_orchestration_event(
        self, instance_id: str, event_name: str, *, data: Any = None
    ) -> None:
        if self.fail:
            raise ConnectionError("secret-scheduler-detail")
        if self.refusal is not None:
            raise FakeRpcError(self.refusal)
        self.raised.append((instance_id, event_name, data))


def tell(client: FakeSchedulerClient, case_id: str, page_id: str) -> None:
    engine = SchedulerEngine(client)  # type: ignore[arg-type]  # stands in for the library's client
    asyncio.run(
        engine.decision_made(
            case_id, page_id, PageStatus.AWAITING_CUSTOMER, Decision.KEEP
        )
    )


def test_story_1_10_the_engine_tells_the_orchestration_by_an_external_event(
    case_id: str,
) -> None:
    page_id = new_id()
    client = FakeSchedulerClient(existing=Instance(OrchestrationStatus.RUNNING))

    tell(client, case_id, page_id)

    # To the case's own instance, under the name its wait has, with the
    # decision as the payload.
    assert client.raised == [(case_id, f"decision.awaiting_customer.{page_id}", "keep")]
    assert decision_event(page_id, PageStatus.AWAITING_CUSTOMER) == client.raised[0][1]
    assert decision_event(page_id, PageStatus.AWAITING_TRIAGE) != client.raised[0][1]


@pytest.mark.parametrize(
    ("existing", "logged"),
    [
        (None, "orchestration=missing"),
        (Instance(OrchestrationStatus.FAILED), "orchestration=failed"),
        (Instance(OrchestrationStatus.TERMINATED), "orchestration=terminated"),
        # The lifecycle ended as it should: a repeat, and nothing to say.
        (Instance(OrchestrationStatus.COMPLETED), None),
    ],
    ids=["none", "failed", "terminated", "completed"],
)
def test_story_1_10_an_orchestration_that_has_ended_is_not_told(
    case_id: str,
    existing: Instance | None,
    logged: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeSchedulerClient(existing=existing)
    page_id = new_id()

    with caplog.at_level(logging.INFO, logger=scheduler.__name__):
        tell(client, case_id, page_id)

    assert client.raised == []
    # A missing or dead lifecycle is worth a warning, with ids only; a
    # completed one is not.
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    if logged is None:
        assert warnings == []
    else:
        assert warnings == [
            f"decision not told: case_id={case_id} page_id={page_id} {logged}"
        ]


@pytest.mark.parametrize(
    "refusal", [grpc.StatusCode.NOT_FOUND, grpc.StatusCode.FAILED_PRECONDITION]
)
def test_story_1_10_a_raise_that_loses_the_race_with_the_lifecycles_end_is_no_failure(
    case_id: str, refusal: grpc.StatusCode, caplog: pytest.LogCaptureFixture
) -> None:
    # It was running when looked at, and had ended when the event came.
    client = FakeSchedulerClient(
        existing=Instance(OrchestrationStatus.RUNNING), refusal=refusal
    )

    with caplog.at_level(logging.INFO, logger=scheduler.__name__):
        tell(client, case_id, new_id())

    assert "orchestration=ended" in caplog.text
    assert "secret-scheduler-detail" not in caplog.text


def test_story_1_10_an_event_the_scheduler_refuses_is_raised_to_the_caller(
    case_id: str,
) -> None:
    client = FakeSchedulerClient(
        existing=Instance(OrchestrationStatus.RUNNING), fail=True
    )

    with pytest.raises(ConnectionError):
        tell(client, case_id, new_id())
    # Any other answer of the scheduler is a failure too: the caller repeats.
    client = FakeSchedulerClient(
        existing=Instance(OrchestrationStatus.RUNNING),
        refusal=grpc.StatusCode.UNAVAILABLE,
    )
    with pytest.raises(grpc.RpcError):
        tell(client, case_id, new_id())


def test_story_1_10_every_refusal_is_logged_with_ids_and_a_code_and_never_the_actor(
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    customer, triage = gated_case(store, case_id, [Route.CUSTOMER, Route.TRIAGE])
    unknown_page = new_id()
    actor = "secret-actor-text"

    with caplog.at_level(logging.WARNING, logger=decisions.__name__):
        refused(store, engine, case_id, customer, "keep", actor)
        refused(store, engine, case_id, customer, "accept", "customer")
        refused(store, engine, case_id, unknown_page, "keep", "customer")
        refused(store, engine, new_id(), customer, "keep", "customer")
        refused(store, engine, case_id, triage, "keep", "customer")

    lines = [record.getMessage() for record in caplog.records]
    assert [line.rsplit("code=", 1)[1] for line in lines] == [
        "actor_not_human",
        "role_not_allowed",
        "not_found",
        "not_found",
        "not_awaiting_decision",
    ]
    assert lines[0] == (
        f"decision refused: case_id={case_id} page_id={customer} "
        "decision=keep code=actor_not_human"
    )
    assert f"page_id={unknown_page} decision=keep" in lines[2]
    # security rule 31: the actor is the caller's own text.
    assert actor not in caplog.text


# --- Only the one operation --------------------------------------------------------


def test_story_1_10_only_the_one_operation_records_a_decision() -> None:
    package = Path(workflow.__file__).parent
    sources = {
        str(path.relative_to(package)): path.read_text(encoding="utf-8")
        for path in package.rglob("*.py")
    }

    # The store's decision write is called from one place: the operation.
    callers = [name for name, source in sources.items() if ".decide(" in source]
    assert callers == ["domain/decisions.py"]
    # The operation is called from one place: its route. No activity of the
    # orchestration, and so no AI stage, has a way to it (AD-10).
    callers = [
        name
        for name, source in sources.items()
        if "record_decision(" in source and name != "domain/decisions.py"
    ]
    assert callers == ["adapters/http/routes.py"]
    assert "decide" not in inspect.getsource(scheduler.Activities)
    # The table is written by the store's one method, and never updated or
    # deleted from.
    database = sources["adapters/db.py"]
    assert database.count("insert(human_decision_table)") == 1
    assert "update(human_decision_table)" not in database
    assert "delete(" not in database
    # And the operation checks the actor before anything is stored.
    operation = inspect.getsource(decisions.record_decision)
    assert operation.index("authorise(") < operation.index("store.decide(")


# --- The route ------------------------------------------------------------------------


def post_decision(
    client: TestClient, case_id: str, page_id: str, body: dict[str, Any]
) -> Any:
    return client.post(
        f"/cases/{case_id}/pages/{page_id}/decisions",
        json=body,
        headers={"traceparent": TRACEPARENT},
    )


def test_story_1_10_the_route_records_a_decision_and_answers_with_it(
    client: TestClient, store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    page_id, _ = gated_case(store, case_id, [Route.CUSTOMER, Route.EXTRACTION])

    response = post_decision(
        client, case_id, page_id, {"decision": "discard", "actor": "customer"}
    )

    assert response.status_code == 200
    recorded = DecisionRecorded.model_validate(response.json())
    assert (recorded.case_id, recorded.page_id) == (case_id, page_id)
    assert (recorded.decision.value, recorded.actor.value) == ("discard", "customer")
    assert recorded.page_status is PageStatus.DISCARDED
    assert response.headers["cache-control"] == "no-store"
    # The event carries the trace of the request (AD-8).
    assert store.events[-1][1].audit.trace_id == TRACE_ID
    progress = client.get(f"/cases/{case_id}/progress").json()
    assert progress["case_status"] == "running"
    assert [page["page_status"] for page in progress["pages"]] == [
        "discarded",
        "extracting",
    ]
    assert len(engine.told) == 1


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"decision": "accept", "actor": "customer"}, 403, "role_not_allowed"),
        ({"decision": "keep", "actor": "underwriter"}, 403, "role_not_allowed"),
        ({"decision": "keep", "actor": "workflow"}, 403, "actor_not_human"),
        (
            {"decision": "keep", "actor": "classification:chat-main"},
            403,
            "actor_not_human",
        ),
        ({"decision": "accept", "actor": "underwriter"}, 409, "not_awaiting_decision"),
        # Not a request at all: refused before the rule is asked.
        ({"decision": "approve", "actor": "customer"}, 422, "validation_failed"),
        ({"decision": "keep"}, 422, "validation_failed"),
        ({"decision": "keep", "actor": ""}, 422, "validation_failed"),
        (
            {"decision": "keep", "actor": "customer", "page_status": "x"},
            422,
            "validation_failed",
        ),
    ],
)
def test_story_1_10_the_route_refuses_in_the_error_shape_and_changes_nothing(
    client: TestClient,
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    body: dict[str, Any],
    status: int,
    code: str,
) -> None:
    (page_id,) = gated_case(store, case_id, [Route.CUSTOMER])
    before = snapshot(store)

    response = post_decision(client, case_id, page_id, body)

    assert response.status_code == status
    detail = ErrorBody.model_validate(response.json()).error
    assert (detail.code.value, detail.trace_id) == (code, TRACE_ID)
    assert snapshot(store) == before
    assert engine.told == []


def test_story_1_10_the_route_answers_404_for_an_unknown_case_or_page(
    client: TestClient, store: MemoryCaseStore, case_id: str
) -> None:
    (page_id,) = gated_case(store, case_id, [Route.CUSTOMER])
    body = {"decision": "keep", "actor": "customer"}

    for case, page in [(new_id(), page_id), (case_id, new_id())]:
        response = post_decision(client, case, page, body)
        assert response.status_code == 404
        assert ErrorBody.model_validate(response.json()).error.code is (
            ErrorCode.NOT_FOUND
        )
    # An id that is not a UUIDv7 is refused before anything is looked up.
    assert post_decision(client, "1", page_id, body).status_code == 422
    assert post_decision(client, case_id, "1", body).status_code == 422


def test_story_1_10_the_route_answers_502_when_the_event_could_not_be_raised(
    client: TestClient, store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    (page_id,) = gated_case(store, case_id, [Route.CUSTOMER])
    body = {"decision": "discard", "actor": "customer"}
    engine.fail_events = True

    failed = post_decision(client, case_id, page_id, body)
    engine.fail_events = False
    again = post_decision(client, case_id, page_id, body)

    assert failed.status_code == 502
    assert ErrorBody.model_validate(failed.json()).error.code is (
        ErrorCode.UPSTREAM_UNAVAILABLE
    )
    assert "secret-store-detail" not in failed.text
    # The repeat is answered with the stored decision, and the event is raised.
    assert again.status_code == 200
    assert again.json()["decision_id"] == store.decisions[0].decision_id
    assert len(store.decisions) == 1
    assert len(engine.told) == 1


# --- The orchestrator --------------------------------------------------------------------


class WaitingContext:
    """Stands in for the engine's context: activities asked for, and events waited for."""

    def __init__(self) -> None:
        self.asked: list[dict[str, Any]] = []
        self.waits: dict[str, list[task.CompletableTask[Any]]] = {}
        self.timers = 0

    def call_activity(self, activity: str, **options: Any) -> object:
        self.asked.append({"activity": activity, **options})
        return task.CompletableTask[Any]()

    def wait_for_external_event(self, name: str, **options: Any) -> object:
        waiting = task.CompletableTask[Any]()
        self.waits.setdefault(name, []).append(waiting)
        return waiting

    def create_timer(self, *arguments: Any, **options: Any) -> object:
        self.timers += 1
        return task.CompletableTask[Any]()

    def open_waits(self) -> list[str]:
        """The events still waited for, in the order the waits were made."""
        return [
            name
            for name, waits in self.waits.items()
            if any(not waiting.is_complete for waiting in waits)
        ]


@dataclass(frozen=True)
class Raised:
    """An external event, as the decision operation raises it."""

    name: str
    payload: object


def keeps(page_id: str) -> Raised:
    return Raised(decision_event(page_id, PageStatus.AWAITING_CUSTOMER), "keep")


def discards(page_id: str) -> Raised:
    return Raised(decision_event(page_id, PageStatus.AWAITING_CUSTOMER), "discard")


def accepts(page_id: str) -> Raised:
    return Raised(decision_event(page_id, PageStatus.AWAITING_TRIAGE), "accept")


def denies(page_id: str) -> Raised:
    return Raised(decision_event(page_id, PageStatus.AWAITING_TRIAGE), "deny")


CONFIRMED = {"outcome": "ok", "case_status": "running", "gate_threshold": 0.9}
ROUTE_READINGS = {
    "extracting": (True, 0.95),
    "awaiting_customer": (False, 1.0),
    "awaiting_triage": (True, 0.6),
}


def run_to_the_wait(
    case_id: str,
    routes: list[str],
    settled: Any = None,
    found: list[str] | None = None,
    **started: Any,
) -> tuple[WaitingContext, Any, list[str], Any]:
    """Run the orchestrator through the gate and the settle; return it where it stands.

    `settled` is the settle activity's answer. Left out, the case waits for
    a human and the settle found each page in the status `found` names, or,
    without `found`, as its route left it. The last value returned is the
    orchestrator's result if it has ended, else None.
    """
    context = WaitingContext()
    page_ids = [new_id() for _ in routes]
    if settled is None:
        settled = {
            "outcome": "ok",
            "case_status": "awaiting_human",
            "page_statuses": dict(zip(page_ids, found or routes, strict=True)),
        }
    steps: Any = build_case_lifecycle(RETRY)(
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
    answers.append(settled)
    try:
        for answer in answers:
            steps.send(answer)
    except StopIteration as done:
        return context, steps, page_ids, done.value
    return context, steps, page_ids, None


def raise_events(context: WaitingContext, steps: Any, events: list[Raised]) -> Any:
    """Hand the orchestrator each event in turn, as the engine does; return its result if it ends."""
    for event in events:
        waiting = next(
            (
                wait
                for wait in context.waits.get(event.name, [])
                if not wait.is_complete
            ),
            None,
        )
        if waiting is None:
            # Nothing waits for it: the engine keeps it, and nothing runs.
            continue
        waiting.complete(event.payload)
        try:
            # The engine resumes the orchestrator with the wait that finished.
            steps.send(waiting)
        except StopIteration as done:
            return done.value
    return None


def test_story_1_10_the_lifecycle_waits_for_a_decision_about_each_waiting_page(
    case_id: str,
) -> None:
    context, _, page_ids, result = run_to_the_wait(
        case_id, ["extracting", "awaiting_customer", "awaiting_triage"]
    )

    # It has not ended, and waits for one event per waiting page: none for
    # the page that went on to extraction.
    assert result is None
    assert context.open_waits() == [
        decision_event(page_ids[1], PageStatus.AWAITING_CUSTOMER),
        decision_event(page_ids[2], PageStatus.AWAITING_TRIAGE),
    ]
    # External events only: never a timer, and no activity that looks.
    assert context.timers == 0
    assert context.asked[-1]["activity"] == SETTLE_CASE_AFTER_GATE
    source = inspect.getsource(orchestration)
    assert "create_timer" not in source
    assert "sleep" not in source


def test_story_1_10_the_lifecycle_goes_on_page_by_page_as_decisions_arrive(
    case_id: str,
) -> None:
    context, steps, (first, second, third), _ = run_to_the_wait(
        case_id, ["awaiting_customer", "awaiting_customer", "awaiting_triage"]
    )
    asked_at_the_wait = len(context.asked)

    # In another order than the pages': each page goes on when its own
    # decision comes, whatever the others still wait for.
    assert raise_events(context, steps, [denies(third)]) is None
    assert context.open_waits() == [
        decision_event(first, PageStatus.AWAITING_CUSTOMER),
        decision_event(second, PageStatus.AWAITING_CUSTOMER),
    ]
    # A kept page now waits for the underwriter, under another event.
    assert raise_events(context, steps, [keeps(second)]) is None
    assert context.open_waits() == [
        decision_event(first, PageStatus.AWAITING_CUSTOMER),
        decision_event(second, PageStatus.AWAITING_TRIAGE),
    ]
    assert raise_events(context, steps, [discards(first)]) is None
    result = raise_events(context, steps, [denies(second)])

    # No page waits any more: the lifecycle ends, every page final.
    assert result == {"case_id": case_id, "case_status": "completed"}
    assert context.open_waits() == []
    # The decisions were stored by the operation, not by an activity.
    assert len(context.asked) == asked_at_the_wait
    assert context.timers == 0


@pytest.mark.parametrize(
    ("routes", "events", "case_status"),
    [
        (["awaiting_customer"], ["discard"], "completed"),
        (["awaiting_customer"], ["keep", "deny"], "completed"),
        # An accepted page is in work: extraction comes with story 2.4.
        (["awaiting_customer"], ["keep", "accept"], "running"),
        (["awaiting_triage"], ["accept"], "running"),
        (["awaiting_triage", "extracting"], ["deny"], "running"),
    ],
)
def test_story_1_10_the_lifecycle_ends_when_no_page_waits(
    case_id: str, routes: list[str], events: list[str], case_status: str
) -> None:
    context, steps, page_ids, _ = run_to_the_wait(case_id, routes)
    make = {"keep": keeps, "discard": discards, "accept": accepts, "deny": denies}

    result = raise_events(
        context, steps, [make[event](page_ids[0]) for event in events]
    )

    assert result == {"case_id": case_id, "case_status": case_status}


def test_story_1_10_an_event_that_is_no_decision_for_the_page_changes_nothing(
    case_id: str,
) -> None:
    context, steps, (page_id,), _ = run_to_the_wait(case_id, ["awaiting_customer"])
    name = decision_event(page_id, PageStatus.AWAITING_CUSTOMER)

    # The underwriter's decision, something unknown, and nothing at all,
    # under the name of the customer's wait: the page is waited for again.
    for payload in ("accept", "approve", None, 7):
        assert raise_events(context, steps, [Raised(name, payload)]) is None
        assert context.open_waits() == [name]

    assert raise_events(context, steps, [discards(page_id)]) == {
        "case_id": case_id,
        "case_status": "completed",
    }


def test_story_1_10_a_decision_told_twice_wakes_nothing_a_second_time(
    case_id: str,
) -> None:
    context, steps, (page_id,), _ = run_to_the_wait(case_id, ["awaiting_customer"])

    # The keep is repeated (its first event was thought lost): the second
    # event has the name of a wait that is over, not of the wait for the
    # underwriter, so it cannot be taken for the underwriter's answer.
    assert raise_events(context, steps, [keeps(page_id), keeps(page_id)]) is None
    assert context.open_waits() == [decision_event(page_id, PageStatus.AWAITING_TRIAGE)]
    assert status_a_decision_leaves(PageStatus.AWAITING_TRIAGE, "keep") is None


def test_story_1_10_a_page_decided_before_the_settle_is_not_waited_for(
    case_id: str,
) -> None:
    # The customer was quick: one page discarded and one kept before the
    # lifecycle settled the case. The settle read the pages as they are
    # stored, and the waits are made for those statuses, not for the routes.
    context, steps, (_, kept, third), result = run_to_the_wait(
        case_id,
        ["awaiting_customer", "awaiting_customer", "awaiting_customer"],
        found=["discarded", "awaiting_triage", "awaiting_customer"],
    )

    assert result is None
    assert context.open_waits() == [
        decision_event(kept, PageStatus.AWAITING_TRIAGE),
        decision_event(third, PageStatus.AWAITING_CUSTOMER),
    ]
    # Their events, raised before the settle, come all the same: nothing
    # waits under those names, and nothing is thrown off by them.
    result = raise_events(context, steps, [keeps(kept), accepts(kept), discards(third)])
    assert result == {"case_id": case_id, "case_status": "running"}


@pytest.mark.parametrize(
    "page_statuses",
    [None, {}, "awaiting_customer", {"another-page": "awaiting_customer"}, "unknown"],
    ids=["missing", "empty", "not-a-mapping", "another-page", "unknown-status"],
)
def test_story_1_10_a_settle_that_does_not_say_what_the_pages_are_ends_the_case_as_failed(
    case_id: str, page_statuses: object
) -> None:
    context = WaitingContext()
    page_id = new_id()
    answer: dict[str, Any] = {"outcome": "ok", "case_status": "awaiting_human"}
    if page_statuses == "unknown":
        answer["page_statuses"] = {page_id: "archived"}
    elif page_statuses is not None:
        answer["page_statuses"] = page_statuses
    steps: Any = build_case_lifecycle(RETRY)(
        context,  # type: ignore[arg-type]  # the stand-in has the methods used
        {"case_id": case_id, "classifier_contender": "llm"},
    )
    steps = cast(Any, steps)
    next(steps)
    for sent in [
        CONFIRMED,
        {"outcome": "ok", "case_status": "running", "page_ids": [page_id]},
        [
            {
                "outcome": "ok",
                "case_status": "running",
                "classification_id": new_id(),
                "is_medical": False,
                "confidence": 1.0,
            }
        ],
        [{"outcome": "ok", "case_status": "running", "route": "awaiting_customer"}],
        answer,
    ]:
        steps.send(sent)

    # Nothing is waited for on a guess: the case cannot go on.
    assert context.waits == {}
    assert context.asked[-1]["activity"] == MARK_CASE_FAILED
    with pytest.raises(StopIteration) as done:
        steps.send({"outcome": "ok", "recorded": "recorded"})
    assert done.value.value == {"case_id": case_id, "case_status": "failed"}


@pytest.mark.parametrize("case_status", ["completed", "failed"])
def test_story_1_10_a_case_that_ended_before_the_wait_is_not_waited_for(
    case_id: str, case_status: str
) -> None:
    # Every waiting page was decided before the settle ran, or the case
    # failed: the settle says so, and the lifecycle ends.
    context, _, _, result = run_to_the_wait(
        case_id,
        ["awaiting_customer", "awaiting_customer"],
        settled={"outcome": "ok", "case_status": case_status},
    )

    assert result == {"case_id": case_id, "case_status": case_status}
    assert context.waits == {}


def test_story_1_10_a_case_started_with_stop_after_gate_waits_for_nobody(
    case_id: str,
) -> None:
    context, _, _, result = run_to_the_wait(
        case_id,
        ["awaiting_customer", "awaiting_triage"],
        settled={"outcome": "ok", "case_status": "completed"},
        stop_after="gate",
    )

    assert result == {"case_id": case_id, "case_status": "completed"}
    assert context.waits == {}
    assert context.asked[-1]["input"] == {"case_id": case_id}


@pytest.mark.parametrize(
    "answer",
    [
        {"outcome": "refused", "reason": "not_found"},
        # Ok, but it does not say what the case is; and no answer worth the name.
        {"outcome": "ok"},
        {"outcome": "ok", "case_status": None},
        {},
        "",
        [],
    ],
    ids=["refused", "no-status", "null-status", "empty", "text", "list"],
)
def test_story_1_10_a_case_that_cannot_be_settled_is_marked_failed_and_not_waited_for(
    case_id: str, answer: Any
) -> None:
    context, steps, _, result = run_to_the_wait(
        case_id, ["awaiting_customer"], settled=answer
    )

    assert result is None
    assert context.asked[-1]["activity"] == MARK_CASE_FAILED
    with pytest.raises(StopIteration) as done:
        steps.send({"outcome": "ok", "recorded": "recorded"})
    assert done.value.value == {"case_id": case_id, "case_status": "failed"}
    assert context.waits == {}


def test_story_1_10_the_waits_are_replayed_the_same_way(case_id: str) -> None:
    def run() -> tuple[list[str], Any]:
        context, steps, page_ids, _ = run_to_the_wait(
            case_id, ["awaiting_customer", "awaiting_triage", "awaiting_customer"]
        )
        result = raise_events(
            context,
            steps,
            [
                keeps(page_ids[2]),
                accepts(page_ids[1]),
                discards(page_ids[0]),
                denies(page_ids[2]),
            ],
        )
        # The page ids differ from run to run: the waits are compared by
        # the page's place and the status waited for.
        place = {page_id: str(index) for index, page_id in enumerate(page_ids)}
        waits = [
            f"{name.rsplit('.', 1)[0]}.{place[name.rsplit('.', 1)[1]]}"
            for name in context.waits
        ]
        return waits, result["case_status"]

    assert run() == run()
    assert run()[1] == "running"
    # Deterministic: no clock, no random value, no I/O in the orchestrator.
    source = inspect.getsource(orchestration.build_case_lifecycle)
    for forbidden in ("datetime", "time.", "random", "uuid", "new_id", "await "):
        assert forbidden not in source
