"""Story 1.8, against a real PostgreSQL and the Durable Task Scheduler emulator.

Run `docker compose up --detach --wait` first. No test here calls Azure.
`workflow` runs as it really runs, with a stand-in where its Dapr sidecar
would be: behind it `intake` answers the redaction command and
`classification` the classify command, over HTTP, in the contracts' shapes.
The whole path, with the real services behind the sidecar, is tested beside
the stand-ins (`packages/` tests, story 1.8).
"""

import contextlib
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.client import OrchestrationState, OrchestrationStatus
from fastapi.testclient import TestClient
from workflow_fakes import FakeStages, SidecarStandIn
from workflow_local import connect

from contracts.ids import new_id
from contracts.models.workflow import AuditTrail, CaseProgress
from workflow.adapters.http.app import create_app
from workflow.adapters.scheduler import build_client
from workflow.settings import Settings

pytestmark = pytest.mark.integration

# So that the tests do not wait out the real backoff: the same attempts,
# closer together.
FAST_RETRIES = {
    "activity_first_retry_seconds": 0.2,
    "activity_backoff_coefficient": 1.0,
    "stage_max_attempts": 4,
}


def query(settings: Settings, statement: str, *parameters: object) -> list[Any]:
    with connect(settings) as connection:
        return connection.execute(statement, parameters).fetchall()


def audit_rows(settings: Settings, case_id: str) -> list[tuple[Any, ...]]:
    return query(
        settings,
        "SELECT action, page_id::text, error_code, detail, actor "
        "FROM workflow.audit_event WHERE case_id = %s ORDER BY occurred_at, recorded_at",
        case_id,
    )


def page_rows(settings: Settings, case_id: str) -> list[tuple[Any, ...]]:
    return query(
        settings,
        "SELECT page_id::text, page_number, page_status FROM workflow.page_status "
        "WHERE case_id = %s ORDER BY page_number",
        case_id,
    )


def case_status(settings: Settings, case_id: str) -> str:
    ((status,),) = query(
        settings,
        "SELECT case_status FROM workflow.case_status WHERE case_id = %s",
        case_id,
    )
    return str(status)


@pytest.fixture
def scheduler_client(local_scheduler: Settings) -> Iterator[DurableTaskSchedulerClient]:
    client = build_client(local_scheduler)
    try:
        yield client
    finally:
        client.close()


def completed(client: DurableTaskSchedulerClient, case_id: str) -> OrchestrationState:
    state = client.wait_for_orchestration_completion(case_id, timeout=60)
    assert state is not None
    assert state.runtime_status is OrchestrationStatus.COMPLETED
    return state


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


def test_story_1_8_every_page_of_a_started_case_is_classified_with_one_event_each(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=4))
    case_id, eval_run_id = new_id(), new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start", json={"eval_run_id": eval_run_id})
        state = completed(scheduler_client, case_id)
        progress = CaseProgress.model_validate(
            client.get(f"/cases/{case_id}/progress").json()
        )
        trail = AuditTrail.model_validate(client.get(f"/cases/{case_id}/audit").json())

    page_ids = sidecar.stages.results[case_id].page_ids
    # Every page is `classified`; the case goes on `running`. Nothing routes a
    # page yet: no page waits for a customer or an underwriter.
    assert json.loads(state.serialized_output or "") == {
        "case_id": case_id,
        "case_status": "running",
    }
    assert progress.case_status.value == "running"
    assert [(page.page_id, page.page_status.value) for page in progress.pages] == [
        (page_id, "classified") for page_id in page_ids
    ]
    # One command per page, with ids only, the contender the case was started
    # with (the default, `llm`) and its run id.
    commands = sidecar.classify_commands(case_id)
    assert sorted(commands, key=lambda command: page_ids.index(command["page_id"])) == [
        {
            "case_id": case_id,
            "page_id": page_id,
            "contender": "llm",
            "eval_run_id": eval_run_id,
        }
        for page_id in page_ids
    ]
    # One `page.classified` event per page: the actor names the service and
    # the model deployment, the reference is the classification, no detail.
    classified = [
        event for event in trail.events if event.action.value == "page.classified"
    ]
    assert sorted(event.page_id or "" for event in classified) == sorted(page_ids)
    for event in classified:
        stored = sidecar.stages.classifications[(case_id, event.page_id or "")]
        assert event.actor == "classification:chat-main"
        assert event.actor_kind.value == "ai"
        assert event.ref == stored.classification_id
        assert event.detail is None
        assert event.eval_run_id == eval_run_id
    assert len(trail.events) == 5
    # Redaction came first: nothing was classified before it was done.
    assert sidecar.requests[0].url.path.endswith(f"/cases/{case_id}/redaction")


def test_story_1_8_a_failed_classification_fails_its_page_and_the_case(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(
        FakeStages(
            pages=3,
            failing_page_numbers=frozenset({2}),
            classification_error_code="invalid_model_output",
        )
    )
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start")
        state = completed(scheduler_client, case_id)

    page_ids = sidecar.stages.results[case_id].page_ids
    assert json.loads(state.serialized_output or "")["case_status"] == "failed"
    assert case_status(service_settings, case_id) == "failed"
    # The rule recorded in story 1.6: the failed page stage fails that page
    # and the case, with one `stage.failed` event for the page.
    failures = [
        row for row in audit_rows(service_settings, case_id) if row[0] == "stage.failed"
    ]
    assert failures == [
        (
            "stage.failed",
            page_ids[1],
            "invalid_model_output",
            None,
            "classification:chat-main",
        )
    ]
    statuses = {
        page_id: status for page_id, _, status in page_rows(service_settings, case_id)
    }
    assert statuses[page_ids[1]] == "failed"
    # The other pages were classified at the same time: each is `classified`
    # if its result came before the failure, and stays `uploaded` if it came
    # after, because a failed case takes no further result.
    assert {statuses[page_ids[0]], statuses[page_ids[2]]} <= {"classified", "uploaded"}
    # Each page was commanded once: a failed result is an answer, not retried.
    assert len(sidecar.classify_commands(case_id)) == 3


def test_story_1_8_a_case_started_with_the_contender_that_is_not_built_fails(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=2))
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(
            f"/cases/{case_id}/start", json={"classifier_contender": "doc-intelligence"}
        )
        state = completed(scheduler_client, case_id)

    assert json.loads(state.serialized_output or "")["case_status"] == "failed"
    # The stage refused each command with 422; none was sent a second time.
    commands = sidecar.classify_commands(case_id)
    assert [command["contender"] for command in commands] == ["doc-intelligence"] * 2
    # The case is marked failed by the lifecycle, with its one case-level
    # event; no page was classified.
    assert audit_rows(service_settings, case_id)[1:] == [
        ("stage.failed", None, "stage_failed", None, "workflow:case-lifecycle")
    ]
    assert {row[2] for row in page_rows(service_settings, case_id)} == {"uploaded"}
    assert case_status(service_settings, case_id) == "failed"


def test_story_1_8_in_progress_is_retried_until_the_stage_answers(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    # The first command got lost on its way back; the stage is still at it
    # when the command is sent again, twice.
    stages = FakeStages(pages=1)
    stages.classify_script = ["down", "in_progress", "in_progress"]
    sidecar = SidecarStandIn(stages)
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start")
        completed(scheduler_client, case_id)

    assert len(sidecar.classify_commands(case_id)) == 4
    assert [row[2] for row in page_rows(service_settings, case_id)] == ["classified"]
    assert [row[0] for row in audit_rows(service_settings, case_id)] == [
        "document.redacted",
        "page.classified",
    ]


def test_story_1_8_when_classification_never_answers_the_case_is_marked_failed(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    stages = FakeStages(pages=1)
    stages.classify_script = ["down"] * 50
    sidecar = SidecarStandIn(stages)
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start")
        state = completed(scheduler_client, case_id)

    # As often as the stage policy says, then the case fails with its one event.
    assert len(sidecar.classify_commands(case_id)) == FAST_RETRIES["stage_max_attempts"]
    assert json.loads(state.serialized_output or "")["case_status"] == "failed"
    assert audit_rows(service_settings, case_id)[1:] == [
        ("stage.failed", None, "stage_failed", None, "workflow:case-lifecycle")
    ]
    assert [row[2] for row in page_rows(service_settings, case_id)] == ["uploaded"]


def test_story_1_8_a_page_the_stage_does_not_hold_fails_the_case_without_a_retry(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    stages = FakeStages(pages=2)
    stages.classify_script = ["not_found"] * 50
    sidecar = SidecarStandIn(stages)
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start")
        state = completed(scheduler_client, case_id)

    assert json.loads(state.serialized_output or "")["case_status"] == "failed"
    # Asked once for each page: `not_found` is answered, never retried.
    assert len(sidecar.classify_commands(case_id)) == 2
    assert case_status(service_settings, case_id) == "failed"
