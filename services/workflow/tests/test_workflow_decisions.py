"""Story 1.10: the one decision operation, the case status that follows the pages, and the waits.

Unit tests: the rule of who may decide what, the recording of a decision,
the route, the orchestrator's waits for decisions and the engine's event.
No scheduler, no database, no network.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from workflow_fakes import (
    TRACE_ID,
    FakeEngine,
    FakeStages,
    MemoryCaseStore,
    after_start,
    starting,
)

from contracts.audit import DECISION_ACTIONS
from contracts.decisions import DECISION_RULES
from contracts.enums import (
    CaseStatus,
    ClassifierContender,
    Decision,
    DemoRole,
    PageStatus,
    RetrieverConfig,
)
from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.workflow import DecisionRecorded, DecisionRequest
from workflow.domain import decisions
from workflow.domain.cases import (
    record_route,
    record_stage_result,
    settle_case_after_gate,
)
from workflow.domain.decisions import (
    authorise,
    record_decision,
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
# What is not a person: a service, the gate, and what only looks like a demo role.


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
        await store.start(*starting(new_case(case_id, parameters, NOW)))
        redacted = await stages.redact_document(
            case_id, eval_run_id=parameters.eval_run_id, trace_context={}
        )
        await record_stage_result(redacted, store=store)
        # As the lifecycle does it: every page is classified, then every
        # page is routed. A stage result lets the case status follow the
        # pages (story 2.4), so the order is the real one.
        classified = []
        for page_id in redacted.page_ids:
            result = await stages.classify_page(
                case_id,
                page_id,
                ClassifierContender.LLM,
                eval_run_id=parameters.eval_run_id,
                trace_context={},
            )
            await record_stage_result(result, store=store)
            classified.append(result)
        for page_id, route, result in zip(
            redacted.page_ids, routes, classified, strict=True
        ):
            await record_route(
                case_id,
                page_id,
                result.classification_id,
                route,
                THRESHOLD,
                store=store,
            )
        if settled:
            await settle_case_after_gate(case_id, store=store, trace_id=None)
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
        len(after_start(store)),
        list(store.decisions),
    )


# --- The rule: who may decide what ---------------------------------------------------


@pytest.mark.parametrize(
    ("actor", "decision"),
    [
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
    ],
)
def test_story_1_10_a_role_deciding_what_is_not_its_own_is_role_not_allowed(
    actor: str, decision: str
) -> None:
    with pytest.raises(DomainError) as raised:
        authorise(actor, Decision(decision))

    assert raised.value.code is ErrorCode.ROLE_NOT_ALLOWED
    assert raised.value.http_status == 403


# --- Recording a decision ---------------------------------------------------------


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
        if event.action in DECISION_ACTIONS
    ]
    assert decided == [
        ("page.discarded", "customer", "human", first),
        ("page.kept", "customer", "human", second),
    ]
    # The kept page waits for the underwriter: the case still waits for a human.
    assert store.cases[case_id].case_status is CaseStatus.AWAITING_HUMAN


def test_story_1_10_a_page_not_awaiting_the_decision_is_409_and_nothing_changes(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    # Not the status the decision needs.
    (page_id,) = gated_case(store, case_id, [Route.TRIAGE])
    before = snapshot(store)

    code = refused(store, engine, case_id, page_id, "keep", "customer")

    assert code is ErrorCode.NOT_AWAITING_DECISION
    assert DomainError(code, "x").http_status == 409
    assert snapshot(store) == before
    assert engine.told == []


def test_story_1_10_an_actor_that_is_not_human_is_403_and_nothing_changes(
    store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    actor = "workflow:gate"
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
