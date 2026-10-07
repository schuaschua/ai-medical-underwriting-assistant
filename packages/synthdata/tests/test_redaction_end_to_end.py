"""Story 1.7: the whole path of a started case, with every real part but Azure and Dapr.

`workflow` and `intake` as they really run, against a real PostgreSQL, the
Durable Task Scheduler emulator and the blob emulator
(`docker compose up --detach --wait`), with the Language stand-in where Azure
AI Language would be. Where the Dapr sidecars would be, a transport hands
`workflow`'s service invocation to the `intake` app.

These tests are here and not under `services/` because they name the
stand-in's package, which nothing there may do (spine AD-17).
"""

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.client import OrchestrationState, OrchestrationStatus
from fastapi.testclient import TestClient
from workflow_local import connect

from contracts.models.intake import PageList
from contracts.models.workflow import AuditTrail, CaseProgress
from intake.adapters.blob import build_blob_service
from intake.adapters.http.app import create_app as create_intake
from intake.settings import Settings as IntakeSettings
from synthdata.language_standin import LanguageStandIn, Mode
from workflow.adapters.http.app import create_app
from workflow.adapters.scheduler import build_client
from workflow.settings import Settings

pytestmark = pytest.mark.integration

CASES_DIR = Path(__file__).resolve().parents[3] / "data" / "cases"
PDF = {"Content-Type": "application/pdf"}
# So that a failing step is not waited out: the same attempts, closer together.
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


@pytest.fixture
def scheduler_client(
    workflow_service_settings: Settings,
) -> Iterator[DurableTaskSchedulerClient]:
    client = build_client(workflow_service_settings)
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
    """`workflow` as it really runs: its own role, its worker, the emulator."""
    with TestClient(
        create_app(settings.model_copy(update=FAST_RETRIES), sidecar=sidecar),
        raise_server_exceptions=False,
    ) as client:
        yield client


class IntakeBehindSidecar(httpx.AsyncBaseTransport):
    """Stands in for the Dapr sidecars: an invocation of `intake` reaches the real app."""

    PREFIX = "/v1.0/invoke/intake/method"

    def __init__(self, intake_app: Any) -> None:
        self._intake = httpx.ASGITransport(app=intake_app)
        self.paths: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if not path.startswith(self.PREFIX):
            return httpx.Response(500, json={"errorCode": "ERR_DIRECT_INVOKE"})
        self.paths.append(path.removeprefix(self.PREFIX))
        forwarded = httpx.Request(
            request.method,
            f"http://intake{path.removeprefix(self.PREFIX)}",
            headers=[
                (name, value)
                for name, value in request.headers.items()
                if name.lower() != "host"
            ],
            content=await request.aread(),
        )
        return await self._intake.handle_async_request(forwarded)


@dataclass
class LocalIntake:
    settings: IntakeSettings
    language: LanguageStandIn

    def app(self) -> Any:
        """A new instance of the service; each has its own database connections."""
        return create_intake(
            self.settings, language=httpx.ASGITransport(app=self.language.app())
        )

    def upload(self, name: str) -> tuple[str, str]:
        """What `web` does first: the upload creates the case in `intake`."""
        with TestClient(self.app()) as client:
            created = client.post(
                "/cases", content=(CASES_DIR / name).read_bytes(), headers=PDF
            ).json()
        return created["case_id"], created["document_id"]

    def pages(self, case_id: str) -> PageList:
        with TestClient(self.app()) as client:
            return PageList.model_validate(client.get(f"/cases/{case_id}/pages").json())

    def page_text(self, page_id: str) -> str:
        with TestClient(self.app()) as client:
            return str(client.get(f"/pages/{page_id}/text").json()["text"])


@pytest.fixture
def intake(migrated_database: IntakeSettings, stand_in: LanguageStandIn) -> LocalIntake:
    """`intake` on this test's database and blob containers, with the stand-in behind it."""
    return LocalIntake(migrated_database, stand_in)


def test_story_1_7_an_uploaded_and_started_case_shows_redaction_done_and_its_pages_uploaded(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
) -> None:
    case_id, _ = intake.upload("case-002.pdf")
    sidecar = IntakeBehindSidecar(intake.app())

    with workflow_service(workflow_service_settings, sidecar) as client:
        assert client.post(f"/cases/{case_id}/start").status_code == 200
        completed(scheduler_client, case_id)
        progress = CaseProgress.model_validate(
            client.get(f"/cases/{case_id}/progress").json()
        )
        trail = AuditTrail.model_validate(client.get(f"/cases/{case_id}/audit").json())

    # Progress: redaction done, and the six pages of the case as `uploaded`.
    assert (progress.case_status.value, progress.redaction_status.value) == (
        "running",
        "done",
    )
    assert [page.page_number for page in progress.pages] == [1, 2, 3, 4, 5, 6]
    assert {page.page_status.value for page in progress.pages} == {"uploaded"}
    # They are the pages `intake` holds, in the same order.
    assert [page.page_id for page in progress.pages] == [
        page.page_id for page in intake.pages(case_id).pages
    ]
    # The audit trail: one `document.redacted` event, a count per category.
    (event,) = trail.events
    assert (event.action.value, event.actor) == (
        "document.redacted",
        "intake:azure-ai-language",
    )
    assert event.detail is not None
    assert event.detail["Person"] >= 2
    assert all(isinstance(count, int) for count in event.detail.values())
    assert set(event.detail) <= {
        "Person",
        "Address",
        "PhoneNumber",
        "Email",
        "USSocialSecurityNumber",
        "PolicyNumber",
    }
    # What a later stage will read holds tokens, not the planted name.
    text = intake.page_text(progress.pages[0].page_id)
    assert "[Person]" in text
    assert "Jordan Samplewick" not in text
    assert sidecar.paths == [f"/cases/{case_id}/redaction"]


@pytest.mark.parametrize(
    ("mode", "error_code"),
    [(Mode.FAIL, "redaction_failed"), (Mode.HANG, "stage_timeout")],
)
def test_story_1_7_with_a_stand_in_told_to_fail_or_hang_the_case_fails_and_no_page_exists(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    workflow_admin: Settings,
    mode: Mode,
    error_code: str,
) -> None:
    intake.language.mode = mode
    # The stage's own deadline, made short for the stand-in that never ends.
    intake.settings = intake.settings.model_copy(
        update={"redaction_deadline_seconds": 1.0}
    )
    case_id, document_id = intake.upload("case-001.pdf")

    with workflow_service(
        workflow_service_settings, IntakeBehindSidecar(intake.app())
    ) as client:
        client.post(f"/cases/{case_id}/start")
        completed(scheduler_client, case_id)
        progress = CaseProgress.model_validate(
            client.get(f"/cases/{case_id}/progress").json()
        )

    # The case is failed, with one `stage.failed` event, and no page exists
    # in `workflow` or in `intake`.
    assert (progress.case_status.value, progress.pages) == ("failed", [])
    assert audit_rows(workflow_service_settings, case_id) == [
        ("stage.failed", None, error_code, None, "intake:azure-ai-language")
    ]
    assert intake.pages(case_id).pages == []
    # Read as the database's owner: `workflow`'s own role has no right to
    # `intake`'s schema (AD-4).
    assert query(workflow_admin, "SELECT count(*) FROM intake.page") == [(0,)]
    # Nothing is left in `cases`, and the original was read by nobody but
    # the redaction call: one job was given its address, and the original's
    # container holds that one blob still.
    blobs = build_blob_service(intake.settings)
    cases = blobs.get_container_client(intake.settings.cases_container)
    originals = blobs.get_container_client(intake.settings.originals_container)
    assert list(cases.list_blob_names()) == []
    (original_name,) = originals.list_blob_names()
    assert original_name == f"{case_id}/{document_id}.pdf"
    (job,) = intake.language.submitted
    assert job["analysisInput"]["documents"][0]["source"]["location"].endswith(
        original_name
    )
    if mode is Mode.HANG:
        # The Language job was cancelled at the deadline.
        assert intake.language.cancelled == list(intake.language.jobs)
