"""Stories 2.5 and 2.6, against a real PostgreSQL and the Durable Task Scheduler emulator.

Run `docker compose up --detach --wait` first. No test here calls Azure. The
service runs as it really runs, as its own database role and with its worker;
a transport stands where its Dapr sidecar would be, and behind it the stage
services answer in the contracts' shapes. The whole path with the real stage
services and the model stand-in is tested beside the stand-ins (`packages/`).
"""

import asyncio
import contextlib
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.client import OrchestrationQuery, OrchestrationStatus
from fastapi.testclient import TestClient
from workflow_fakes import (
    STARTED_BY,
    FakeEngine,
    FakeStages,
    SidecarStandIn,
    classification_done,
    facts_done,
    redaction_done,
    starting,
    verdict_done,
)
from workflow_local import connect, wait_for_case_status

from contracts.audit import VerdictDetail
from contracts.enums import (
    CaseStatus,
    ClassifierContender,
    RetrieverConfig,
)
from contracts.ids import new_id
from contracts.models.workflow import AuditTrail, CaseProgress, DecisionRequest
from workflow.adapters.db import SqlCaseStore, build_database
from workflow.adapters.http.app import create_app
from workflow.adapters.scheduler import build_client
from workflow.domain.cases import (
    record_route,
    record_stage_result,
    settle_case_after_gate,
)
from workflow.domain.decisions import record_decision
from workflow.domain.entities import StartParameters
from workflow.domain.gate import Route
from workflow.domain.lifecycle import new_case
from workflow.domain.recording import RecordOutcome
from workflow.domain.verdicts import complete_case, verdict_run_instance_id
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
BOTH_BUILT = frozenset({RetrieverConfig.R3, RetrieverConfig.R4})
# WORKFLOW_AVAILABLE_RETRIEVER_CONFIGS for a build that can run both rows.
BOTH = [RetrieverConfig.R3, RetrieverConfig.R4]
# What the classifier reads, by page number: a medical page it is sure of
# (extraction) and one it is not sure of (triage).
SURE_AND_UNSURE = {1: ("lab_report", True, 0.95), 2: ("lab_report", True, 0.6)}
UNDERWRITER = {"actor": "underwriter"}


def query(settings: Settings, statement: str, *parameters: object) -> list[Any]:
    with connect(settings) as connection:
        return connection.execute(statement, parameters).fetchall()


def stored_events(settings: Settings, case_id: str) -> list[tuple[Any, ...]]:
    """The case's events as the table holds them, in the order they were written."""
    return query(
        settings,
        "SELECT action, actor, page_id::text, ref::text, error_code, "
        "eval_run_id::text, trace_id "
        "FROM workflow.audit_event WHERE case_id = %s ORDER BY audit_event_seq",
        case_id,
    )


def stored_actions(settings: Settings, case_id: str) -> list[str]:
    return [str(row[0]) for row in stored_events(settings, case_id)]


def case_status(settings: Settings, case_id: str) -> str:
    (row,) = query(
        settings,
        "SELECT case_status FROM workflow.case_status WHERE case_id = %s",
        case_id,
    )
    return str(row[0])


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
    client: DurableTaskSchedulerClient, instance_id: str
) -> tuple[OrchestrationStatus, dict[str, Any]]:
    """Wait for an orchestration to end; how it ended, and its output."""
    state = client.wait_for_orchestration_completion(instance_id, timeout=60)
    assert state is not None
    return state.runtime_status, json.loads(state.serialized_output or "{}")


def instances_of(
    client: DurableTaskSchedulerClient, case_id: str
) -> dict[str, tuple[OrchestrationStatus, datetime]]:
    """Every orchestration whose instance id begins with the case's, with its state and when it was created."""
    return {
        state.instance_id: (state.runtime_status, state.created_at)
        for state in client.get_all_orchestration_states(
            OrchestrationQuery(instance_id_prefix=case_id)
        )
    }


def finished_case(
    client: TestClient,
    scheduler_client: DurableTaskSchedulerClient,
    **started: Any,
) -> str:
    """Start a case and let its lifecycle run to its end; the case's id."""
    case_id = new_id()
    response = client.post(f"/cases/{case_id}/start", json={**STARTED_BY, **started})
    assert response.status_code == 200
    runtime_status, output = ended(scheduler_client, case_id)
    assert runtime_status is OrchestrationStatus.COMPLETED
    assert output == {"case_id": case_id, "case_status": "completed"}
    return case_id


def ask(client: TestClient, case_id: str, config: str) -> Any:
    return client.post(
        f"/cases/{case_id}/verdict-runs", json={"retriever_config": config}
    )


# --- The lifecycle, on the emulator -------------------------------------------------------


def test_story_2_5_a_started_case_gets_one_run_once_every_page_is_final_and_then_completes(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=2))
    case_id, eval_run_id = new_id(), new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(
            f"/cases/{case_id}/start", json={**STARTED_BY, "eval_run_id": eval_run_id}
        )
        runtime_status, output = ended(scheduler_client, case_id)
        trail = AuditTrail.model_validate(client.get(f"/cases/{case_id}/audit").json())
        progress = CaseProgress.model_validate(
            client.get(f"/cases/{case_id}/progress").json()
        )

    assert runtime_status is OrchestrationStatus.COMPLETED
    assert output == {"case_id": case_id, "case_status": "completed"}
    assert (progress.case_status, progress.error_code) == (CaseStatus.COMPLETED, None)
    # Exactly one command, for the row the case was started with (the
    # default, `r3`), with ids only and the case's run id.
    assert sidecar.verdict_commands(case_id) == [
        {"case_id": case_id, "retriever_config": "r3", "eval_run_id": eval_run_id}
    ]
    run = sidecar.stages.verdict_results[(case_id, "r3")]
    actions = [event.action.value for event in trail.events]
    # After both pages' extractions the run, then the completion, last.
    assert actions[-4:] == [
        "facts.extracted",
        "facts.extracted",
        "verdict.suggested",
        "case.completed",
    ]
    assert (actions.count("verdict.suggested"), actions.count("case.completed")) == (
        1,
        1,
    )
    suggested = trail.events[-2]
    # Case-level: the actor names the service and its model deployment, the
    # reference is the run at `verdict`; nothing of what it suggests.
    assert (suggested.actor_kind.value, suggested.actor) == ("ai", "verdict:chat-main")
    # AD-15: the event says which retriever row the run was made with.
    assert (suggested.page_id, suggested.ref, suggested.detail) == (
        None,
        run.verdict_run_id,
        VerdictDetail(retriever_config=RetrieverConfig.R3),
    )
    assert (suggested.eval_run_id, suggested.error_code) == (eval_run_id, None)
    assert trail.events[-1].eval_run_id == eval_run_id
    # Only the case's own orchestration ran.
    assert list(instances_of(scheduler_client, case_id)) == [case_id]


def test_story_2_5_a_case_started_with_two_configurations_gets_a_run_for_each_before_it_completes(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=1, built_retriever_configs=BOTH_BUILT))

    with workflow_service(
        service_settings, sidecar.transport(), available_retriever_configs=BOTH
    ) as client:
        case_id = finished_case(
            client, scheduler_client, retriever_configs=["r3", "r4"]
        )

    # One command per configuration, and no more.
    assert sorted(
        command["retriever_config"] for command in sidecar.verdict_commands(case_id)
    ) == ["r3", "r4"]
    events = stored_events(service_settings, case_id)
    assert [row[0] for row in events][-4:] == [
        "facts.extracted",
        "verdict.suggested",
        "verdict.suggested",
        "case.completed",
    ]
    # Each run has its own event, by its own reference.
    assert {row[3] for row in events[-3:-1]} == {
        sidecar.stages.verdict_results[(case_id, config)].verdict_run_id
        for config in ("r3", "r4")
    }
    assert case_status(service_settings, case_id) == "completed"


def test_story_2_5_no_run_is_commanded_while_a_page_waits_and_one_is_once_it_is_decided(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=2, readings=SURE_AND_UNSURE))
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start", json=STARTED_BY)
        waiting = wait_for_case_status(client, case_id, "awaiting_human")
        _, unsure = (page["page_id"] for page in waiting["pages"])
        while_waiting = list(sidecar.verdict_commands(case_id))
        # A run asked for now is refused, and gets no orchestration.
        too_early = ask(client, case_id, "r3")
        denied = client.post(
            f"/cases/{case_id}/pages/{unsure}/decisions",
            json={"decision": "deny", **UNDERWRITER},
        )
        runtime_status, output = ended(scheduler_client, case_id)

    assert while_waiting == []
    assert (too_early.status_code, too_early.json()["error"]["code"]) == (
        409,
        "pages_not_terminal",
    )
    assert denied.status_code == 200
    assert (runtime_status, output["case_status"]) == (
        OrchestrationStatus.COMPLETED,
        "completed",
    )
    assert len(sidecar.verdict_commands(case_id)) == 1
    # The run came after the decision that made the last page final.
    actions = stored_actions(service_settings, case_id)
    assert actions.index("page.denied") < actions.index("verdict.suggested")
    assert actions[-2:] == ["verdict.suggested", "case.completed"]
    assert list(instances_of(scheduler_client, case_id)) == [case_id]


def test_story_2_5_a_failed_run_fails_the_case_with_its_code_and_the_lifecycle_ends_failed(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(
        FakeStages(pages=1, verdict="failed", verdict_error_code="model_unavailable")
    )
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start", json=STARTED_BY)
        runtime_status, output = ended(scheduler_client, case_id)
        progress = client.get(f"/cases/{case_id}/progress").json()

    # The orchestration ended by itself; it did not fail.
    assert runtime_status is OrchestrationStatus.COMPLETED
    assert output == {"case_id": case_id, "case_status": "failed"}
    assert (progress["case_status"], progress["error_code"]) == (
        "failed",
        "model_unavailable",
    )
    # The page keeps what it reached: only the case failed.
    assert [page["page_status"] for page in progress["pages"]] == ["extracted"]
    run = sidecar.stages.verdict_results[(case_id, "r3")]
    events = stored_events(service_settings, case_id)
    # One case-level `stage.failed` event, the run's own, with its code;
    # no second one from the lifecycle, and no `case.completed`.
    assert events[-1][:5] == (
        "stage.failed",
        "verdict:chat-main",
        None,
        run.verdict_run_id,
        "model_unavailable",
    )
    assert [row[0] for row in events].count("stage.failed") == 1
    assert "case.completed" not in [row[0] for row in events]
    # A failed run is a result: the command was not sent again.
    assert len(sidecar.verdict_commands(case_id)) == 1


def test_story_2_5_a_run_command_answered_in_progress_or_not_at_all_is_sent_again_until_the_stage_answers(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    # The first command got lost on its way back; the stage is still at it
    # when the command is sent again, twice.
    stages = FakeStages(pages=1)
    stages.verdict_script = ["down", "in_progress", "in_progress"]
    sidecar = SidecarStandIn(stages)

    with workflow_service(service_settings, sidecar.transport()) as client:
        case_id = finished_case(client, scheduler_client)

    assert len(sidecar.verdict_commands(case_id)) == 4
    # One run, recorded once, and the case completed after it.
    actions = stored_actions(service_settings, case_id)
    assert actions[-2:] == ["verdict.suggested", "case.completed"]
    assert actions.count("verdict.suggested") == 1
    assert "stage.failed" not in actions


# --- One more run on a finished case, on the emulator -------------------------------------


def test_story_2_6_a_run_asked_again_for_the_row_the_case_ran_with_is_the_stored_run(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=1))

    with workflow_service(service_settings, sidecar.transport()) as client:
        case_id = finished_case(client, scheduler_client)
        instance_id = verdict_run_instance_id(case_id, "r3")
        before = stored_events(service_settings, case_id)
        not_asked_yet = scheduler_client.get_orchestration_state(instance_id)

        first = ask(client, case_id, "r3")
        runtime_status, output = ended(scheduler_client, instance_id)
        instances = instances_of(scheduler_client, case_id)
        again = ask(client, case_id, "r3")
        once_more = ask(client, case_id, "r3")

    run = sidecar.stages.verdict_results[(case_id, "r3")]
    assert not_asked_yet is None
    assert instance_id == f"{case_id}:verdict:r3"
    assert first.status_code == 200
    # Asked while it ran, or just after: never a run of its own to name yet
    # unless it is done already.
    assert first.json()["status"] in {"running", "done"}
    assert (first.json()["case_id"], first.json()["retriever_config"]) == (
        case_id,
        "r3",
    )
    # An orchestration of its own, beside the case's, which is left as it ended.
    assert runtime_status is OrchestrationStatus.COMPLETED
    assert output == {"status": "done", "verdict_run_id": run.verdict_run_id}
    assert set(instances) == {case_id, instance_id}
    assert instances[case_id][0] is OrchestrationStatus.COMPLETED
    # The stage was commanded once more and answered with its stored run:
    # no second run, and no second event for it.
    assert (
        sidecar.verdict_commands(case_id)
        == [{"case_id": case_id, "retriever_config": "r3", "eval_run_id": None}] * 2
    )
    assert len(sidecar.stages.verdict_results) == 1
    assert stored_events(service_settings, case_id) == before
    assert [row[0] for row in before].count("verdict.suggested") == 1
    assert case_status(service_settings, case_id) == "completed"
    # The same request again starts nothing new and answers with that run.
    for response in (again, once_more):
        assert response.json() == {
            "case_id": case_id,
            "retriever_config": "r3",
            "status": "done",
            "verdict_run_id": run.verdict_run_id,
            "error_code": None,
        }
    assert instances_of(scheduler_client, case_id) == instances
    assert len(sidecar.verdict_commands(case_id)) == 2


def test_story_2_6_a_run_asked_for_another_row_is_recorded_and_the_case_stays_completed(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=1, built_retriever_configs=BOTH_BUILT))
    eval_run_id = new_id()

    with workflow_service(
        service_settings, sidecar.transport(), available_retriever_configs=BOTH
    ) as client:
        case_id = finished_case(client, scheduler_client, eval_run_id=eval_run_id)
        before = stored_events(service_settings, case_id)
        asked = ask(client, case_id, "r4")
        _, output = ended(scheduler_client, verdict_run_instance_id(case_id, "r4"))
        answered = ask(client, case_id, "r4")
        progress = client.get(f"/cases/{case_id}/progress").json()

    run = sidecar.stages.verdict_results[(case_id, "r4")]
    assert asked.status_code == 200
    assert output == {"status": "done", "verdict_run_id": run.verdict_run_id}
    assert answered.json() == {
        "case_id": case_id,
        "retriever_config": "r4",
        "status": "done",
        "verdict_run_id": run.verdict_run_id,
        "error_code": None,
    }
    # Ids only went to the stage, with the case's eval run.
    assert sidecar.verdict_commands(case_id)[-1] == {
        "case_id": case_id,
        "retriever_config": "r4",
        "eval_run_id": eval_run_id,
    }
    # One more event, after the completion, which stays the only one.
    events = stored_events(service_settings, case_id)
    assert events[:-1] == before
    assert events[-1][:5] == (
        "verdict.suggested",
        "verdict:chat-main",
        None,
        run.verdict_run_id,
        None,
    )
    assert [row[0] for row in events].count("case.completed") == 1
    assert (progress["case_status"], progress["error_code"]) == ("completed", None)


def test_story_2_6_a_requested_run_that_fails_leaves_the_case_completed_with_a_stage_failed_event(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(
        FakeStages(
            pages=1,
            built_retriever_configs=BOTH_BUILT,
            failing_verdict_configs=frozenset({RetrieverConfig.R4}),
            verdict_error_code="model_unavailable",
        )
    )

    with workflow_service(
        service_settings, sidecar.transport(), available_retriever_configs=BOTH
    ) as client:
        case_id = finished_case(client, scheduler_client)
        before = stored_events(service_settings, case_id)
        ask(client, case_id, "r4")
        runtime_status, output = ended(
            scheduler_client, verdict_run_instance_id(case_id, "r4")
        )
        answered = ask(client, case_id, "r4")
        progress = client.get(f"/cases/{case_id}/progress").json()

    run = sidecar.stages.verdict_results[(case_id, "r4")]
    # The run's orchestration ended, and says the run failed and which it was.
    assert runtime_status is OrchestrationStatus.COMPLETED
    assert output == {
        "status": "failed",
        "verdict_run_id": run.verdict_run_id,
        "error_code": "model_unavailable",
    }
    # Whoever asked is told why the run failed.
    assert answered.json() == {
        "case_id": case_id,
        "retriever_config": "r4",
        "status": "failed",
        "verdict_run_id": run.verdict_run_id,
        "error_code": "model_unavailable",
    }
    # The case is as it was: completed, and its progress names no failure.
    assert case_status(service_settings, case_id) == "completed"
    assert (progress["case_status"], progress["error_code"]) == ("completed", None)
    events = stored_events(service_settings, case_id)
    assert events[:-1] == before
    assert events[-1][:5] == (
        "stage.failed",
        "verdict:chat-main",
        None,
        run.verdict_run_id,
        "model_unavailable",
    )
    assert [row[0] for row in events].count("case.completed") == 1
    # A failed run is a result: commanded once.
    assert [
        command["retriever_config"] for command in sidecar.verdict_commands(case_id)
    ] == ["r3", "r4"]


def test_story_2_6_a_requested_run_whose_stage_never_answered_is_scheduled_again_when_asked_again(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=1))

    with workflow_service(service_settings, sidecar.transport()) as client:
        case_id = finished_case(client, scheduler_client)
        instance_id = verdict_run_instance_id(case_id, "r3")
        # `verdict` gives no answer through every attempt of the activity.
        sidecar.stages.verdict_script = ["down"] * int(FAST["stage_max_attempts"])
        ask(client, case_id, "r3")
        _, gave_up = ended(scheduler_client, instance_id)
        # The fault has passed. Asked again, the run is not held for ever by
        # the orchestration that ended without one: it is made again.
        again = ask(client, case_id, "r3")
        _, output = ended(scheduler_client, instance_id)
        answered = ask(client, case_id, "r3")

    run = sidecar.stages.verdict_results[(case_id, "r3")]
    assert gave_up == {
        "status": "failed",
        "verdict_run_id": None,
        "error_code": "upstream_unavailable",
    }
    assert (again.status_code, again.json()["status"]) == (200, "running")
    assert output == {"status": "done", "verdict_run_id": run.verdict_run_id}
    assert answered.json()["status"] == "done"
    assert answered.json()["verdict_run_id"] == run.verdict_run_id
    # Still one orchestration for the pair, and the case as it was.
    assert set(instances_of(scheduler_client, case_id)) == {case_id, instance_id}
    assert case_status(service_settings, case_id) == "completed"


def test_story_2_6_a_run_is_refused_for_an_unknown_case_a_failed_case_and_a_request_that_is_not_valid(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=1, verdict="failed"))
    unknown, refused_start = new_id(), new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        case_id = new_id()
        client.post(f"/cases/{case_id}/start", json=STARTED_BY)
        _, output = ended(scheduler_client, case_id)
        answers = [
            ask(client, unknown, "r3"),
            # Every page is final, and the case's own run failed.
            ask(client, case_id, "r3"),
            ask(client, case_id, "r9"),
            client.post(f"/cases/{case_id}/verdict-runs", json={}),
            # AD-11: a real row that this build cannot run, for a run and
            # for a start; the start stores nothing.
            ask(client, case_id, "r5"),
            client.post(
                f"/cases/{refused_start}/start",
                json={**STARTED_BY, "retriever_configs": ["r3", "r5"]},
            ),
        ]
        never_started = client.get(f"/cases/{refused_start}/progress")

    assert output["case_status"] == "failed"
    assert [
        (response.status_code, response.json()["error"]["code"]) for response in answers
    ] == [
        (404, "not_found"),
        (409, "pages_not_terminal"),
        (422, "validation_failed"),
        (422, "validation_failed"),
        (409, "retriever_not_available"),
        (409, "retriever_not_available"),
    ]
    assert never_started.status_code == 404
    assert instances_of(scheduler_client, refused_start) == {}
    # No orchestration but the case's own was made.
    assert list(instances_of(scheduler_client, case_id)) == [case_id]
    assert instances_of(scheduler_client, unknown) == {}


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
    store: SqlCaseStore,
    runner: asyncio.Runner,
    routes: list[Route],
    parameters: StartParameters = PARAMETERS,
) -> tuple[str, list[str]]:
    """A started case whose pages the gate has routed and settled."""
    case_id = new_id()
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

    runner.run(scenario())
    return case_id, page_ids


def final_case(
    store: SqlCaseStore,
    runner: asyncio.Runner,
    parameters: StartParameters = PARAMETERS,
) -> str:
    """A case whose two pages are final: one extracted, one discarded by the customer."""
    case_id, (extracting, waiting) = gated_case(
        store, runner, [Route.EXTRACTION, Route.CUSTOMER], parameters
    )

    async def scenario() -> None:
        await record_stage_result(facts_done(case_id, extracting), store=store)
        await record_decision(
            case_id,
            waiting,
            DecisionRequest.model_validate(
                {"decision": "discard", "actor": "customer"}
            ),
            store=store,
            engine=FakeEngine(),
        )

    runner.run(scenario())
    return case_id


def record(
    store: SqlCaseStore,
    runner: asyncio.Runner,
    result: Any,
    asked_afterwards: bool = False,
) -> RecordOutcome:
    return runner.run(
        record_stage_result(result, store=store, asked_afterwards=asked_afterwards)
    )


def complete(
    store: SqlCaseStore,
    runner: asyncio.Runner,
    case_id: str,
    trace_id: str | None = None,
) -> str:
    settled = runner.run(complete_case(case_id, store=store, trace_id=trace_id))
    return settled.case_status.value


@pytest.mark.parametrize(
    ("routes", "status"),
    [
        ([Route.EXTRACTION, Route.TRIAGE], "awaiting_human"),
    ],
    ids=["awaiting-triage"],
)
def test_story_2_5_the_real_store_completes_no_case_while_a_page_is_not_final(
    service_settings: Settings, routes: list[Route], status: str
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id, _ = gated_case(store, runner, routes)
        # Should a run's event be in the trail all the same: the pages decide.
        record(store, runner, verdict_done(case_id))
        before = stored_events(service_settings, case_id)
        answered = complete(store, runner, case_id)

    assert answered == status
    assert case_status(service_settings, case_id) == status
    assert stored_events(service_settings, case_id) == before
    assert "case.completed" not in [row[0] for row in before]


def test_story_2_5_completions_at_once_complete_a_case_with_one_event(
    service_settings: Settings,
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id = final_case(store, runner)
        record(store, runner, verdict_done(case_id))

    def complete_once(_: int) -> str:
        # Each on a connection and a loop of its own, as separate activities are.
        with a_store(service_settings) as (own_store, own_runner):
            return complete(own_store, own_runner, case_id)

    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(complete_once, range(6)))

    assert set(statuses) == {"completed"}
    actions = stored_actions(service_settings, case_id)
    assert actions.count("case.completed") == 1
    assert actions[-1] == "case.completed"
