"""Story 1.7, against a real PostgreSQL and the Durable Task Scheduler emulator.

Run `docker compose up --detach --wait` first. No test here calls Azure.
`workflow` runs as it really runs, with a stand-in where its Dapr sidecar
would be: behind it `intake` answers the redaction command over HTTP, in the
contracts' shapes. The whole path, with the real `intake` behind the sidecar,
is tested beside the Language stand-in (`packages/` tests, story 1.7).
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


def case_row(settings: Settings, case_id: str) -> tuple[Any, ...]:
    (row,) = query(
        settings,
        "SELECT case_status, redaction_status FROM workflow.case_status "
        "WHERE case_id = %s",
        case_id,
    )
    return tuple(row)


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


# --- workflow, with a stand-in for its sidecar ---------------------------------------


def test_story_1_7_a_started_case_is_redacted_and_its_pages_are_tracked_as_uploaded(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=3))
    case_id, eval_run_id = new_id(), new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start", json={"eval_run_id": eval_run_id})
        state = completed(scheduler_client, case_id)
        progress = CaseProgress.model_validate(
            client.get(f"/cases/{case_id}/progress").json()
        )
        trail = AuditTrail.model_validate(client.get(f"/cases/{case_id}/audit").json())

    assert json.loads(state.serialized_output or "") == {
        "case_id": case_id,
        "case_status": "running",
    }
    # Redaction is done, and each page of the result is tracked: as
    # `uploaded` first, and by now, the lifecycle having run on, as
    # `classified` (story 1.8, test_workflow_classification_integration.py).
    assert (progress.case_status.value, progress.redaction_status.value) == (
        "running",
        "done",
    )
    result = sidecar.stages.results[case_id]
    assert [(page.page_id, page.page_number) for page in progress.pages] == [
        (page_id, number) for number, page_id in enumerate(result.page_ids, 1)
    ]
    assert {page.page_status.value for page in progress.pages} == {"classified"}
    # One `document.redacted` event whose detail is a count per category.
    (event,) = (
        event for event in trail.events if event.action.value == "document.redacted"
    )
    assert event.actor == "intake:azure-ai-language"
    assert event.detail == {"Person": 2, "PhoneNumber": 1}
    assert event.eval_run_id == eval_run_id
    # Commanded once, through the sidecar, with the run id the case was
    # started with, and before anything else was commanded.
    assert sidecar.redactions(case_id) == 1
    request = sidecar.requests[0]
    assert request.url.path == f"/v1.0/invoke/intake/method/cases/{case_id}/redaction"
    assert json.loads(request.content) == {"eval_run_id": eval_run_id}


@pytest.mark.parametrize("error_code", ["redaction_failed", "stage_timeout"])
def test_story_1_7_a_failed_redaction_ends_the_case_as_failed_with_one_stage_failed_event(
    service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    error_code: str,
) -> None:
    sidecar = SidecarStandIn(FakeStages(redaction="failed", error_code=error_code))
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start")
        state = completed(scheduler_client, case_id)
        progress = CaseProgress.model_validate(
            client.get(f"/cases/{case_id}/progress").json()
        )

    assert json.loads(state.serialized_output or "")["case_status"] == "failed"
    assert (progress.case_status.value, progress.redaction_status.value) == (
        "failed",
        "failed",
    )
    # No pages, and exactly one case-level `stage.failed` event, with the code.
    assert progress.pages == []
    assert audit_rows(service_settings, case_id) == [
        ("stage.failed", None, error_code, None, "intake:azure-ai-language")
    ]
    assert sidecar.redactions(case_id) == 1


def test_story_1_7_a_case_intake_does_not_hold_fails_at_redaction_without_a_retry(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    # Story 1.6, finding 14: a start accepts any case id. `intake` answers 404
    # for one it does not hold, however often it is asked.
    sidecar = SidecarStandIn(FakeStages(script=["not_found"] * 50))
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start")
        state = completed(scheduler_client, case_id)

    assert json.loads(state.serialized_output or "")["case_status"] == "failed"
    # Asked once: `not_found` is answered, never retried.
    assert sidecar.redactions(case_id) == 1
    assert case_row(service_settings, case_id) == ("failed", "running")
    assert audit_rows(service_settings, case_id) == [
        ("stage.failed", None, "stage_failed", None, "workflow:case-lifecycle")
    ]


def test_story_1_7_in_progress_is_retried_until_the_stage_answers(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    # The first command got lost on its way back; the stage is still at it
    # when the command is sent again, twice.
    sidecar = SidecarStandIn(FakeStages(script=["down", "in_progress", "in_progress"]))
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start")
        completed(scheduler_client, case_id)

    assert sidecar.redactions(case_id) == 4
    assert case_row(service_settings, case_id) == ("running", "done")
    # One redaction event, however often the command was sent.
    assert [row[0] for row in audit_rows(service_settings, case_id)].count(
        "document.redacted"
    ) == 1


def test_story_1_7_when_intake_never_answers_the_case_is_marked_failed(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(script=["down"] * 50))
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start")
        state = completed(scheduler_client, case_id)

    # As often as the stage policy says, then the case fails with its one event.
    assert sidecar.redactions(case_id) == FAST_RETRIES["stage_max_attempts"]
    assert json.loads(state.serialized_output or "")["case_status"] == "failed"
    assert audit_rows(service_settings, case_id) == [
        ("stage.failed", None, "stage_failed", None, "workflow:case-lifecycle")
    ]
