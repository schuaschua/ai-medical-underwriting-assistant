"""Story 1.12, against a real PostgreSQL and the Durable Task Scheduler emulator.

Run `docker compose up --detach --wait` first. No test here calls Azure.
`workflow` runs as it really runs, with a stand-in where its Dapr sidecar
would be: behind it `intake` answers the redaction command and
`classification` the classify command, in the contracts' shapes.
"""

import asyncio
import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from alembic import command
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from fastapi.testclient import TestClient
from workflow_fakes import (
    STARTED_BY,
    FakeStages,
    SidecarStandIn,
    classification_done,
    redaction_done,
    starting,
)
from workflow_local import as_service, connect, wait_for_case_status

from contracts.audit import AuditAction, AuditRecord, RouteDetail
from contracts.decisions import DECISION_RULES
from contracts.enums import (
    ActorKind,
    ClassifierContender,
    PageStatus,
    RetrieverConfig,
)
from contracts.errors import ErrorCode
from contracts.ids import new_id
from contracts.models.workflow import AuditTrail, CaseProgress
from workflow.adapters.db import SqlCaseStore, build_database
from workflow.adapters.http.app import create_app
from workflow.adapters.migrations import alembic_config
from workflow.adapters.scheduler import build_client
from workflow.domain.cases import read_audit_trail, record_stage_result
from workflow.domain.entities import StartParameters
from workflow.domain.gate import Route, route_recording
from workflow.domain.lifecycle import new_case
from workflow.domain.transitions import PAGE_TRANSITIONS
from workflow.settings import Settings

pytestmark = pytest.mark.integration

FAST_RETRIES = {
    "activity_first_retry_seconds": 0.2,
    "activity_backoff_coefficient": 1.0,
    "stage_max_attempts": 4,
}
NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
PARAMETERS = StartParameters(
    classifier_contender=ClassifierContender.LLM,
    retriever_configs=(RetrieverConfig.R3,),
    stop_after=None,
    eval_run_id=None,
)
# What the classifier reads, by page number: two non-medical pages it is sure
# of (the customer is asked), one page it is not sure of (triage), and a
# medical page it is sure of (extraction).
READINGS = {
    1: ("other", False, 0.96),
    2: ("invoice", False, 1.0),
    3: ("lab_report", True, 0.6),
    4: ("lab_report", True, 0.95),
}
# The page status each human action leaves its page in (AD-10).
_STATUS_AFTER_DECISION = {rule.action: rule.leaves for rule in DECISION_RULES.values()}


@pytest.fixture
def scheduler_client(local_scheduler: Settings) -> Iterator[DurableTaskSchedulerClient]:
    client = build_client(local_scheduler)
    try:
        yield client
    finally:
        client.close()


@contextlib.contextmanager
def workflow_service(
    settings: Settings, sidecar: httpx.AsyncBaseTransport
) -> Iterator[TestClient]:
    """The service as it really runs: its own role, its worker, the emulator."""
    with TestClient(
        create_app(settings.model_copy(update=FAST_RETRIES), sidecar=sidecar),
        raise_server_exceptions=False,
    ) as client:
        yield client


@contextlib.contextmanager
def a_store(settings: Settings) -> Iterator[tuple[SqlCaseStore, asyncio.Runner]]:
    """The real store, as the service's role, with a loop to run its calls on."""
    with asyncio.Runner() as runner:
        database = build_database(settings)
        try:
            yield SqlCaseStore(database), runner
        finally:
            runner.run(database.dispose())


def decide(
    client: TestClient, case_id: str, page_id: str, decision: str, actor: str
) -> None:
    response = client.post(
        f"/cases/{case_id}/pages/{page_id}/decisions",
        json={"decision": decision, "actor": actor},
    )
    assert response.status_code == 200


def read_trail(client: TestClient, case_id: str) -> AuditTrail:
    response = client.get(f"/cases/{case_id}/audit")
    assert response.status_code == 200
    return AuditTrail.model_validate(response.json())


def status_after(event: AuditRecord) -> PageStatus:
    """The page status an event about a page reports (AD-8)."""
    if event.action is AuditAction.PAGE_CLASSIFIED:
        return PageStatus.CLASSIFIED
    if event.action is AuditAction.PAGE_ROUTED:
        assert isinstance(event.detail, RouteDetail)
        return PageStatus(event.detail.route)
    if event.action is AuditAction.STAGE_FAILED:
        return PageStatus.FAILED
    if event.action is AuditAction.FACTS_EXTRACTED:
        return PageStatus.EXTRACTED
    return _STATUS_AFTER_DECISION[event.action]


def walk(trail: AuditTrail) -> dict[str, list[PageStatus]]:
    """Every status each page went through, as the trail's events report them in order.

    A page begins as `uploaded` with the redaction's event; every later event
    about it is one status change. A change that may not follow the status
    before it (`domain/transitions.py`) fails the walk.
    """
    statuses: dict[str, list[PageStatus]] = {}
    redacted = [
        event for event in trail.events if event.action is AuditAction.DOCUMENT_REDACTED
    ]
    assert len(redacted) <= 1
    for event in trail.events:
        if event.page_id is None:
            continue
        # No page has an event before the redaction that began its tracking.
        assert redacted and trail.events.index(redacted[0]) < trail.events.index(event)
        history = statuses.setdefault(event.page_id, [PageStatus.UPLOADED])
        after = status_after(event)
        assert after in PAGE_TRANSITIONS[history[-1]], (history, event.action)
        history.append(after)
    return statuses


def test_story_1_12_the_trail_of_a_decided_case_lists_every_step_in_causal_order(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=4, readings=READINGS))
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start", json=STARTED_BY)
        waiting = wait_for_case_status(client, case_id, "awaiting_human")
        first, second, third, fourth = (page["page_id"] for page in waiting["pages"])
        # One page discarded, one kept and then accepted, one denied.
        decide(client, case_id, first, "discard", "customer")
        decide(client, case_id, second, "keep", "customer")
        decide(client, case_id, second, "accept", "underwriter")
        decide(client, case_id, third, "deny", "underwriter")
        scheduler_client.wait_for_orchestration_completion(case_id, timeout=60)
        trail = read_trail(client, case_id)
        progress = CaseProgress.model_validate(
            client.get(f"/cases/{case_id}/progress").json()
        )

    assert trail.has_more is False
    actions = [(event.action.value, event.page_id) for event in trail.events]
    # Story 1.13: the start first, by the role that asked for it. Then the
    # redaction, once; then per page its classification, its route and its
    # decisions, each cause before its effect. The two pages that reached
    # `extracting`, one by the gate and one by an accept, are extracted
    # (story 2.4). With the last page final the case gets its verdict run,
    # a case-level event after every page's (stories 2.5 and 2.6), and only
    # then is it completed: that is the trail's last event.
    assert actions[:2] == [("case.started", None), ("document.redacted", None)]
    assert actions[-2:] == [("verdict.suggested", None), ("case.completed", None)]
    assert sorted(actions[2:-2]) == sorted(
        [("page.classified", page) for page in (first, second, third, fourth)]
        + [("page.routed", page) for page in (first, second, third, fourth)]
        + [
            ("page.discarded", first),
            ("page.kept", second),
            ("page.accepted", second),
            ("page.denied", third),
            ("facts.extracted", second),
            ("facts.extracted", fourth),
        ]
    )
    for page_id, chain in {
        first: ["page.classified", "page.routed", "page.discarded"],
        second: [
            "page.classified",
            "page.routed",
            "page.kept",
            "page.accepted",
            "facts.extracted",
        ],
        third: ["page.classified", "page.routed", "page.denied"],
        fourth: ["page.classified", "page.routed", "facts.extracted"],
    }.items():
        assert [action for action, page in actions if page == page_id] == chain
    # Each with its actor as recorded: the service and what did the work
    # for an AI step, the demo role for a person's.
    actors = {
        (event.action.value, event.actor_kind, event.actor) for event in trail.events
    }
    assert actors == {
        ("case.started", ActorKind.HUMAN, "customer"),
        ("document.redacted", ActorKind.AI, "intake:azure-ai-language"),
        ("page.classified", ActorKind.AI, "classification:chat-main"),
        ("page.routed", ActorKind.AI, "workflow:gate"),
        ("page.discarded", ActorKind.HUMAN, "customer"),
        ("page.kept", ActorKind.HUMAN, "customer"),
        ("page.accepted", ActorKind.HUMAN, "underwriter"),
        ("page.denied", ActorKind.HUMAN, "underwriter"),
        ("facts.extracted", ActorKind.AI, "extraction:chat-main"),
        ("verdict.suggested", ActorKind.AI, "verdict:chat-main"),
        ("case.completed", ActorKind.AI, "workflow:case-lifecycle"),
    }
    # The run's event refers to the run `verdict` stored for the case.
    suggested = trail.events[-2]
    assert (
        suggested.ref == sidecar.stages.verdict_results[(case_id, "r3")].verdict_run_id
    )
    assert all(event.error_code is None for event in trail.events)
    assert all(event.occurred_at.utcoffset() == timedelta(0) for event in trail.events)

    # Every page status change has its event: walked from `uploaded`, the
    # events lead each page to the status it has, by allowed changes only.
    walked = walk(trail)
    assert {page.page_id: page.page_status for page in progress.pages} == {
        page_id: history[-1] for page_id, history in walked.items()
    }
    assert [status.value for status in walked[second]] == [
        "uploaded",
        "classified",
        "awaiting_customer",
        "awaiting_triage",
        "extracting",
        "extracted",
    ]
    assert [status.value for status in walked[first]][-1] == "discarded"
    assert [status.value for status in walked[third]][-1] == "denied"
    assert [status.value for status in walked[fourth]][-2:] == [
        "extracting",
        "extracted",
    ]
    assert progress.case_status.value == "completed"


def test_story_1_12_a_failed_stage_is_in_the_trail_with_its_error_code(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(
        FakeStages(
            pages=2,
            failing_page_numbers=frozenset({2}),
            classification_error_code="model_unavailable",
        )
    )
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start", json=STARTED_BY)
        progress = CaseProgress.model_validate(
            wait_for_case_status(client, case_id, "failed")
        )
        scheduler_client.wait_for_orchestration_completion(case_id, timeout=60)
        trail = read_trail(client, case_id)

    failed_page = progress.pages[1]
    failures = [
        event for event in trail.events if event.action is AuditAction.STAGE_FAILED
    ]
    assert [(event.page_id, event.error_code) for event in failures] == [
        (failed_page.page_id, ErrorCode.MODEL_UNAVAILABLE)
    ]
    assert all(
        event.error_code is None
        for event in trail.events
        if event.action is not AuditAction.STAGE_FAILED
    )
    # The failed page's status has its event too.
    walked = walk(trail)
    assert walked[failed_page.page_id] == [PageStatus.UPLOADED, PageStatus.FAILED]
    assert failed_page.page_status is PageStatus.FAILED
    for page in progress.pages:
        assert walked.get(page.page_id, [PageStatus.UPLOADED])[-1] is page.page_status


# --- The order of writing (migration 0004) ------------------------------------------


def stored_order(settings: Settings, case_id: str) -> list[tuple[Any, ...]]:
    with connect(settings) as connection:
        return connection.execute(
            "SELECT action, page_id::text, audit_event_seq, recorded_at "
            "FROM workflow.audit_event WHERE case_id = %s ORDER BY audit_event_seq",
            (case_id,),
        ).fetchall()


def test_story_1_12_an_event_written_later_with_an_earlier_record_time_is_listed_later(
    service_settings: Settings,
) -> None:
    case_id, page_id = new_id(), new_id()
    # The clock `workflow` reads steps back between the two recordings (or
    # the first recording read it and then waited for the case's row).
    clock = iter([NOW, NOW - timedelta(seconds=30), NOW - timedelta(seconds=60)])

    async def scenario(store: SqlCaseStore) -> AuditTrail:
        await store.start(*starting(new_case(case_id, PARAMETERS, NOW)))
        await record_stage_result(
            redaction_done(case_id, [page_id]), store=store, now=lambda: next(clock)
        )
        classified = classification_done(case_id, page_id)
        await record_stage_result(classified, store=store, now=lambda: next(clock))
        await store.record(
            route_recording(
                case_id,
                page_id,
                classified.classification_id,
                Route.TRIAGE,
                0.9,
                occurred_at=NOW,
            ),
            next(clock),
        )
        return await read_audit_trail(case_id, store=store)

    with a_store(service_settings) as (store, runner):
        trail = runner.run(scenario(store))

    assert [event.action.value for event in trail.events] == [
        "case.started",
        "document.redacted",
        "page.classified",
        "page.routed",
    ]
    # The start was recorded at NOW, as the redaction after it.
    record_times = [row[3] for row in stored_order(service_settings, case_id)][1:]
    assert record_times == sorted(record_times, reverse=True)
    assert len(set(record_times)) == 3


def test_story_1_12_the_migration_numbers_the_events_already_there_in_the_order_read_until_now(
    empty_database: Settings,
) -> None:
    config = alembic_config(empty_database)
    command.upgrade(config, "0003")
    case_id = new_id()
    # Written as the build before this migration wrote them; the second row
    # inserted has the earlier record time.
    first, second, third = sorted(new_id() for _ in range(3))
    stored = [
        (second, "2026-10-07T09:00:05Z"),
        (first, "2026-10-07T09:00:01Z"),
        # The same record time as the one before it: the id decides.
        (third, "2026-10-07T09:00:05Z"),
    ]
    with connect(empty_database, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO workflow.case_status (case_id, case_status, "
            "redaction_status, classifier_contender, retriever_configs, "
            "created_at, updated_at) "
            "VALUES (%s, 'running', 'running', 'llm', '{r3}', now(), now())",
            (case_id,),
        )
        for event_id, recorded_at in stored:
            connection.execute(
                "INSERT INTO workflow.audit_event (audit_event_id, actor_kind, "
                "actor, action, occurred_at, case_id, page_id, ref, trace_id, "
                "recorded_at) VALUES (%s, 'ai', 'classification:chat-main', "
                "'page.classified', now(), %s, %s, %s, %s, %s)",
                (event_id, case_id, new_id(), new_id(), "0" * 32, recorded_at),
            )

    command.upgrade(config, "head")

    def numbered() -> list[tuple[Any, ...]]:
        with connect(empty_database) as connection:
            return connection.execute(
                "SELECT audit_event_id::text, audit_event_seq "
                "FROM workflow.audit_event ORDER BY audit_event_seq"
            ).fetchall()

    assert numbered() == [(first, 1), (second, 2), (third, 3)]
    # The guard is back as it was: not even the owner changes an event.
    with (
        connect(empty_database) as connection,
        pytest.raises(psycopg.errors.RestrictViolation),
    ):
        connection.execute("UPDATE workflow.audit_event SET actor = 'underwriter'")
    # The service's own role adds an event, and the database numbers it on;
    # a number of the writer's own choosing is refused.
    later = new_id()
    with connect(as_service(empty_database), autocommit=True) as connection:
        connection.execute(
            "INSERT INTO workflow.audit_event (audit_event_id, actor_kind, "
            "actor, action, occurred_at, case_id, page_id, ref, trace_id, "
            "recorded_at) VALUES (%s, 'ai', 'classification:chat-main', "
            "'page.classified', now(), %s, %s, %s, %s, '2026-10-07T08:00:00Z')",
            (later, case_id, new_id(), new_id(), "0" * 32),
        )
        with pytest.raises(psycopg.errors.GeneratedAlways):
            connection.execute(
                "INSERT INTO workflow.audit_event (audit_event_id, actor_kind, "
                "actor, action, occurred_at, case_id, page_id, ref, trace_id, "
                "recorded_at, audit_event_seq) VALUES (%s, 'ai', "
                "'classification:chat-main', 'page.classified', now(), %s, %s, "
                "%s, %s, now(), 1)",
                (new_id(), case_id, new_id(), new_id(), "0" * 32),
            )
    assert numbered()[-1] == (later, 4)
    with connect(empty_database) as connection:
        indexes = connection.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'workflow' "
            "AND tablename = 'audit_event' AND indexname LIKE 'ix_%%'"
        ).fetchall()
    assert indexes == [("ix_workflow_audit_event_case_id_audit_event_seq",)]

    # Back to the revision before: the events and their guard stay.
    command.downgrade(config, "0003")
    with connect(empty_database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM workflow.audit_event"
        ).fetchone() == (4,)
        assert connection.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'workflow' "
            "AND tablename = 'audit_event' AND indexname LIKE 'ix_%%'"
        ).fetchall() == [("ix_workflow_audit_event_case_id_occurred_at",)]
    with (
        connect(empty_database) as connection,
        pytest.raises(psycopg.errors.RestrictViolation),
    ):
        connection.execute("DELETE FROM workflow.audit_event")
