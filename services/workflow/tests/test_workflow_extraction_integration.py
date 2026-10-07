"""Story 2.4, against a real PostgreSQL and the Durable Task Scheduler emulator.

Run `docker compose up --detach --wait` first. No test here calls Azure. The
service runs as it really runs, as its own database role and with its worker;
a transport stands where its Dapr sidecar would be, and behind it the stage
services answer in the contracts' shapes. The whole path with the real stage
services and the model stand-in is tested beside the stand-ins (`packages/`).
"""

import asyncio
import contextlib
import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import psycopg
import pytest
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.client import OrchestrationStatus
from fastapi.testclient import TestClient
from workflow_fakes import (
    STARTED_BY,
    FakeEngine,
    FakeStages,
    SidecarStandIn,
    classification_done,
    facts_done,
    facts_failed,
    redaction_done,
    starting,
)
from workflow_local import connect, wait_for_case_status

from contracts.enums import ClassifierContender, RetrieverConfig
from contracts.ids import new_id
from contracts.models.workflow import AuditTrail, DecisionRequest
from workflow.adapters.db import SqlCaseStore, build_database
from workflow.adapters.http.app import create_app
from workflow.adapters.scheduler import SchedulerEngine, build_client
from workflow.domain.cases import (
    record_route,
    record_stage_result,
    settle_case_after_gate,
)
from workflow.domain.decisions import (
    record_decision,
)
from workflow.domain.entities import StartParameters
from workflow.domain.gate import Route
from workflow.domain.lifecycle import new_case
from workflow.domain.ports import Told
from workflow.domain.recording import RecordOutcome
from workflow.settings import Settings

pytestmark = pytest.mark.integration

FAST = {
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
# What the classifier reads, by page number: a medical page it is sure of
# (extraction) and one it is not sure of (triage).
SURE_AND_UNSURE = {1: ("lab_report", True, 0.95), 2: ("lab_report", True, 0.6)}
UNDERWRITER = {"actor": "underwriter"}


def query(settings: Settings, statement: str, *parameters: object) -> list[Any]:
    with connect(settings) as connection:
        return connection.execute(statement, parameters).fetchall()


def trail_actions(settings: Settings, case_id: str) -> list[tuple[str, str | None]]:
    """The case's events in the order written: action and page."""
    return [
        (row[0], row[1])
        for row in query(
            settings,
            "SELECT action, page_id::text FROM workflow.audit_event "
            "WHERE case_id = %s ORDER BY audit_event_seq",
            case_id,
        )
    ]


def page_statuses(settings: Settings, case_id: str) -> list[str]:
    return [
        row[0]
        for row in query(
            settings,
            "SELECT page_status FROM workflow.page_status WHERE case_id = %s "
            "ORDER BY page_number",
            case_id,
        )
    ]


def case_status(settings: Settings, case_id: str) -> str:
    return str(
        query(
            settings,
            "SELECT case_status FROM workflow.case_status WHERE case_id = %s",
            case_id,
        )[0][0]
    )


def wait_for_page_statuses(
    settings: Settings, case_id: str, wanted: list[str], timeout_seconds: float = 30.0
) -> None:
    """Look at the stored pages until they have the wanted statuses.

    The lifecycle runs on the worker's own threads: what is stored is looked
    at until it has got there.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if page_statuses(settings, case_id) == wanted:
            return
        # Not a wait for time to pass: the other threads get their turn.
        time.sleep(0.05)
    raise AssertionError(
        f"the pages of case {case_id} did not reach {wanted}: "
        f"{page_statuses(settings, case_id)}"
    )


def told_rows(settings: Settings) -> list[tuple[Any, ...]]:
    return query(
        settings,
        "SELECT decision_id::text, told_at FROM workflow.decision_told "
        "ORDER BY told_at, decision_id",
    )


@pytest.fixture
def scheduler_client(local_scheduler: Settings) -> Iterator[DurableTaskSchedulerClient]:
    client = build_client(local_scheduler)
    try:
        yield client
    finally:
        client.close()


@contextlib.contextmanager
def workflow_service(
    settings: Settings, sidecar: httpx.AsyncBaseTransport, **changes: Any
) -> Iterator[TestClient]:
    """The service as it really runs: its own role, its worker, the emulator."""
    with TestClient(
        create_app(settings.model_copy(update={**FAST, **changes}), sidecar=sidecar),
        raise_server_exceptions=False,
    ) as client:
        yield client


def ended(
    client: DurableTaskSchedulerClient, case_id: str
) -> tuple[OrchestrationStatus, dict[str, Any]]:
    state = client.wait_for_orchestration_completion(case_id, timeout=60)
    assert state is not None
    return state.runtime_status, json.loads(state.serialized_output or "{}")


# --- The lifecycle, on the emulator -------------------------------------------------------


def test_story_2_4_a_started_case_is_extracted_page_by_page_and_completed(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=3))
    case_id, eval_run_id = new_id(), new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(
            f"/cases/{case_id}/start", json={**STARTED_BY, "eval_run_id": eval_run_id}
        )
        runtime_status, output = ended(scheduler_client, case_id)
        trail = AuditTrail.model_validate(client.get(f"/cases/{case_id}/audit").json())
        progress = client.get(f"/cases/{case_id}/progress").json()

    page_ids = sidecar.stages.results[case_id].page_ids
    # The orchestration ended when the case was final.
    assert runtime_status is OrchestrationStatus.COMPLETED
    assert output == {"case_id": case_id, "case_status": "completed"}
    assert progress["case_status"] == "completed"
    assert [page["page_status"] for page in progress["pages"]] == ["extracted"] * 3
    # One extraction command per page, with ids only and the case's run id.
    commands = sidecar.extract_commands(case_id)
    assert sorted(commands, key=lambda item: page_ids.index(item["page_id"])) == [
        {"case_id": case_id, "page_id": page_id, "eval_run_id": eval_run_id}
        for page_id in page_ids
    ]
    # One `facts.extracted` event per page: the actor names the service and
    # the model deployment, the reference is the page's fact set.
    extracted = [
        event for event in trail.events if event.action.value == "facts.extracted"
    ]
    assert sorted(event.page_id or "" for event in extracted) == sorted(page_ids)
    for event in extracted:
        stored = sidecar.stages.fact_sets[(case_id, event.page_id or "")]
        assert (event.actor_kind.value, event.actor) == ("ai", "extraction:chat-main")
        assert event.ref == stored.fact_set_id
        assert event.detail is None
        assert event.eval_run_id == eval_run_id
    # `case.completed` once, last in the trail, in the case's own run.
    assert [event.action.value for event in trail.events].count("case.completed") == 1
    last = trail.events[-1]
    assert (last.action.value, last.actor, last.page_id, last.ref) == (
        "case.completed",
        "workflow:case-lifecycle",
        None,
        case_id,
    )
    assert last.eval_run_id == eval_run_id
    # Each page went `extracting` and then `extracted`, each with its event.
    for page_id in page_ids:
        assert [
            action
            for action, page in trail_actions(service_settings, case_id)
            if page == page_id
        ] == ["page.classified", "page.routed", "facts.extracted"]


def test_story_2_4_a_page_accepted_later_is_extracted_then_and_the_case_completes(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=2, readings=SURE_AND_UNSURE))
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start", json=STARTED_BY)
        # The sure page is extracted while the other waits for the underwriter.
        waiting = wait_for_case_status(client, case_id, "awaiting_human")
        sure, unsure = (page["page_id"] for page in waiting["pages"])
        wait_for_page_statuses(
            service_settings, case_id, ["extracted", "awaiting_triage"]
        )
        before_accept = (
            page_statuses(service_settings, case_id),
            case_status(service_settings, case_id),
            scheduler_client.get_orchestration_state(case_id),
        )
        accepted = client.post(
            f"/cases/{case_id}/pages/{unsure}/decisions",
            json={"decision": "accept", **UNDERWRITER},
        )
        runtime_status, output = ended(scheduler_client, case_id)

    statuses, status, state = before_accept
    assert (statuses, status) == (["extracted", "awaiting_triage"], "awaiting_human")
    assert state is not None and state.runtime_status is OrchestrationStatus.RUNNING
    assert accepted.status_code == 200
    assert (runtime_status, output["case_status"]) == (
        OrchestrationStatus.COMPLETED,
        "completed",
    )
    assert page_statuses(service_settings, case_id) == ["extracted", "extracted"]
    assert [item["page_id"] for item in sidecar.extract_commands(case_id)] == [
        sure,
        unsure,
    ]
    # Stories 2.5 and 2.6: with the last page final the case gets its
    # verdict run, and only then is it completed.
    assert [item["retriever_config"] for item in sidecar.verdict_commands(case_id)] == [
        "r3"
    ]
    actions = trail_actions(service_settings, case_id)
    assert actions[-4:] == [
        ("page.accepted", unsure),
        ("facts.extracted", unsure),
        ("verdict.suggested", None),
        ("case.completed", None),
    ]
    # The decision was told by the request that stored it, and is marked so.
    assert len(told_rows(service_settings)) == 1


def test_story_2_4_a_decision_whose_event_was_lost_is_told_by_workflow_itself(
    service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=1, readings={1: SURE_AND_UNSURE[2]}))
    case_id = new_id()
    # The scheduler cannot be reached for the first raise of the event.
    raises: list[str] = []
    real_raise = SchedulerEngine._decision_made

    def lossy(self: SchedulerEngine, *arguments: Any) -> Told:
        raises.append(arguments[1])
        if len(raises) == 1:
            raise ConnectionError("scheduler away, secret address 10.0.0.9")
        return real_raise(self, *arguments)

    monkeypatch.setattr(SchedulerEngine, "_decision_made", lossy)

    with workflow_service(
        service_settings,
        sidecar.transport(),
        decision_tell_interval_seconds=0.2,
        decision_tell_grace_seconds=0.0,
    ) as client:
        client.post(f"/cases/{case_id}/start", json=STARTED_BY)
        waiting = wait_for_case_status(client, case_id, "awaiting_human")
        (page_id,) = (page["page_id"] for page in waiting["pages"])
        lost = client.post(
            f"/cases/{case_id}/pages/{page_id}/decisions",
            json={"decision": "accept", **UNDERWRITER},
        )
        # Nobody repeats the decision: the browser is closed.
        runtime_status, output = ended(scheduler_client, case_id)

    # The decision was stored, and the request said it could not tell the case.
    assert (lost.status_code, lost.json()["error"]["code"]) == (
        502,
        "upstream_unavailable",
    )
    assert "10.0.0.9" not in lost.text
    # `workflow` told the orchestration by itself; the page went on.
    assert raises == [page_id, page_id]
    assert (runtime_status, output["case_status"]) == (
        OrchestrationStatus.COMPLETED,
        "completed",
    )
    assert page_statuses(service_settings, case_id) == ["extracted"]
    assert [action for action, _ in trail_actions(service_settings, case_id)][-4:] == [
        "page.accepted",
        "facts.extracted",
        # Stories 2.5 and 2.6: the verdict run, before the case completes.
        "verdict.suggested",
        "case.completed",
    ]
    assert len(told_rows(service_settings)) == 1


# --- The real store -----------------------------------------------------------------------


@contextlib.contextmanager
def a_store(settings: Settings) -> Iterator[tuple[SqlCaseStore, asyncio.Runner]]:
    """The real store, as the service's role, with a loop to run its calls on."""
    with asyncio.Runner() as runner:
        database = build_database(settings)
        try:
            yield SqlCaseStore(database), runner
        finally:
            runner.run(database.dispose())


def gated_case(
    store: SqlCaseStore, runner: asyncio.Runner, routes: list[Route]
) -> tuple[str, list[str]]:
    """A started case whose pages the gate has routed and settled."""
    case_id = new_id()
    page_ids = [new_id() for _ in routes]

    async def scenario() -> None:
        await store.start(*starting(new_case(case_id, PARAMETERS, NOW)))
        await record_stage_result(redaction_done(case_id, page_ids), store=store)
        classified = [classification_done(case_id, page_id) for page_id in page_ids]
        for done in classified:
            await record_stage_result(done, store=store)
        for page_id, route, done in zip(page_ids, routes, classified, strict=True):
            await record_route(
                case_id, page_id, done.classification_id, route, 0.9, store=store
            )
        await settle_case_after_gate(case_id, store=store, trace_id=None)

    runner.run(scenario())
    return case_id, page_ids


def decide(
    store: SqlCaseStore,
    runner: asyncio.Runner,
    engine: FakeEngine,
    case_id: str,
    page_id: str,
    decision: str,
    actor: str,
    now: datetime = NOW,
) -> Any:
    return runner.run(
        record_decision(
            case_id,
            page_id,
            DecisionRequest.model_validate({"decision": decision, "actor": actor}),
            store=store,
            engine=engine,
            now=lambda: now,
        )
    )


def test_story_2_4_the_real_store_fails_page_and_case_on_a_failed_extraction(
    service_settings: Settings,
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id, (failing, other) = gated_case(
            store, runner, [Route.EXTRACTION, Route.EXTRACTION]
        )
        failed = runner.run(
            record_stage_result(
                facts_failed(case_id, failing, "model_unavailable"), store=store
            )
        )
        late = runner.run(record_stage_result(facts_done(case_id, other), store=store))

    assert (failed, late) == (RecordOutcome.RECORDED, RecordOutcome.CASE_FAILED)
    assert case_status(service_settings, case_id) == "failed"
    assert page_statuses(service_settings, case_id) == ["failed", "extracting"]
    assert query(
        service_settings,
        "SELECT action, page_id::text, error_code FROM workflow.audit_event "
        "WHERE case_id = %s ORDER BY audit_event_seq DESC LIMIT 1",
        case_id,
    ) == [("stage.failed", failing, "model_unavailable")]


def test_story_2_4_a_decision_of_a_case_whose_orchestration_is_dead_fails_the_case(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=1, readings={1: SURE_AND_UNSURE[2]}))
    case_id = new_id()

    with workflow_service(
        service_settings,
        sidecar.transport(),
        decision_tell_interval_seconds=0.2,
        decision_tell_grace_seconds=0.0,
    ) as client:
        client.post(f"/cases/{case_id}/start", json=STARTED_BY)
        waiting = wait_for_case_status(client, case_id, "awaiting_human")
        (page_id,) = (page["page_id"] for page in waiting["pages"])
        # The orchestration is terminated under the waiting case.
        scheduler_client.terminate_orchestration(case_id)
        scheduler_client.wait_for_orchestration_completion(case_id, timeout=60)
        accepted = client.post(
            f"/cases/{case_id}/pages/{page_id}/decisions",
            json={"decision": "accept", **UNDERWRITER},
        )
        failed = wait_for_case_status(client, case_id, "failed")

    # The decision is stored and answered; nothing will extract the page,
    # so the case is failed, with one case-level event, and not left running.
    assert accepted.status_code == 200
    assert failed["error_code"] == "stage_failed"
    assert told_rows(service_settings) == []
    actions = trail_actions(service_settings, case_id)
    assert actions[-2:] == [("page.accepted", page_id), ("stage.failed", None)]
    assert [action for action, _ in actions].count("stage.failed") == 1
    assert sidecar.extract_commands(case_id) == []


def test_story_2_4_the_mark_is_only_ever_added_and_the_decision_is_never_changed(
    migrated_database: Settings, service_settings: Settings
) -> None:
    engine = FakeEngine()
    with a_store(service_settings) as (store, runner):
        case_id, (page_id,) = gated_case(store, runner, [Route.TRIAGE])
        decided = decide(store, runner, engine, case_id, page_id, "deny", "underwriter")

    # The service's role may add a mark and read it, and nothing else.
    for statement in (
        "UPDATE workflow.decision_told SET told_at = now()",
        "DELETE FROM workflow.decision_told",
        "TRUNCATE workflow.decision_told",
        "UPDATE workflow.human_decision SET actor = 'customer'",
    ):
        with (
            connect(service_settings) as connection,
            pytest.raises(psycopg.errors.InsufficientPrivilege),
        ):
            connection.execute(statement)
    # A mark is about a stored decision.
    with (
        connect(migrated_database) as connection,
        pytest.raises(psycopg.errors.ForeignKeyViolation),
    ):
        connection.execute(
            "INSERT INTO workflow.decision_told (decision_id, told_at) "
            "VALUES (%s, now())",
            (new_id(),),
        )
    assert told_rows(service_settings) == [(decided.decision_id, NOW)]
