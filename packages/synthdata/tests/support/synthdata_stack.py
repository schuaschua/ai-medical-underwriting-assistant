"""What the cross-service tests share: the real services, wired together without Dapr.

These tests run `workflow`, `intake` and `classification` as they really run,
against the containers of compose.yaml, with the stand-ins of this package
where Azure AI Language and the Foundry chat deployment would be. Where the
Dapr sidecars would be, a transport hands a service invocation to the app of
the service it names.

The helpers live with the stand-ins' tests, on pytest's `pythonpath`, and not
under `services/`: nothing there may name the generator's package or the
answer key (spine AD-17).
"""

import asyncio
import contextlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import httpx2
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.client import OrchestrationState, OrchestrationStatus
from fastapi.testclient import TestClient
from workflow_local import connect, wait_for_case_status

from classification.adapters.http.app import create_app as create_classification
from classification.settings import Settings as ClassificationSettings
from contracts.models.classification import ClassificationList
from contracts.models.intake import PageList
from contracts.models.workflow import AuditTrail, CaseProgress
from intake.adapters.http.app import create_app as create_intake
from intake.settings import Settings as IntakeSettings
from synthdata.foundry_standin import FoundryStandIn
from synthdata.language_standin import LanguageStandIn
from web.adapters.http.app import create_app as create_web
from web.settings import Settings as WebSettings
from workflow.adapters.http.app import create_app as create_workflow
from workflow.settings import Settings as WorkflowSettings

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CASES_DIR = REPOSITORY_ROOT / "data" / "cases"
ANSWER_KEY_DIR = REPOSITORY_ROOT / "data" / "answer-key" / "cases"
PDF = {"Content-Type": "application/pdf"}
# So that a failing step is not waited out: the same attempts, closer together.
FAST_RETRIES = {
    "activity_first_retry_seconds": 0.2,
    "activity_backoff_coefficient": 1.0,
    "stage_max_attempts": 4,
}
# An orchestration in one of these may still run, or wait for a person.
_ALIVE = frozenset(
    {
        OrchestrationStatus.PENDING,
        OrchestrationStatus.RUNNING,
        OrchestrationStatus.SUSPENDED,
    }
)
_INVOKE = "/v1.0/invoke/"
_BODY_FRAMING = frozenset({"content-length", "content-encoding", "transfer-encoding"})
_METHOD = "/method"


def answer_key(case_name: str) -> dict[str, Any]:
    """The answer key of one synthetic case: its expected pages and planted identifiers."""
    key: dict[str, Any] = json.loads((ANSWER_KEY_DIR / f"{case_name}.json").read_text())
    return key


def query(settings: WorkflowSettings, statement: str, *parameters: object) -> list[Any]:
    with connect(settings) as connection:
        return connection.execute(statement, parameters).fetchall()


def audit_rows(settings: WorkflowSettings, case_id: str) -> list[tuple[Any, ...]]:
    return query(
        settings,
        "SELECT action, page_id::text, error_code, detail, actor "
        "FROM workflow.audit_event WHERE case_id = %s ORDER BY occurred_at, recorded_at",
        case_id,
    )


def completed(client: DurableTaskSchedulerClient, case_id: str) -> OrchestrationState:
    state = client.wait_for_orchestration_completion(case_id, timeout=90)
    assert state is not None
    assert state.runtime_status is OrchestrationStatus.COMPLETED
    return state


class ServicesBehindSidecar(httpx.AsyncBaseTransport):
    """Stands in for the Dapr sidecars: an invocation by app id reaches that service's app.

    `calls` notes every invocation as app id, method and path, in order.
    """

    def __init__(self, **apps: Any) -> None:
        # A service is given as its app, or as a transport that reaches it.
        self._apps = {
            app_id: app
            if isinstance(app, httpx.AsyncBaseTransport)
            else httpx.ASGITransport(app=app)
            for app_id, app in apps.items()
        }
        self.calls: list[tuple[str, str, str]] = []

    def paths(self, app_id: str) -> list[str]:
        """The paths invoked on one service, in order."""
        return [path for called, _, path in self.calls if called == app_id]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        app_id, separator, path = (
            request.url.path.removeprefix(_INVOKE).partition(_METHOD)
            if request.url.path.startswith(_INVOKE)
            else ("", "", "")
        )
        if not separator or app_id not in self._apps:
            # As the sidecar answers for an app it cannot reach.
            return httpx.Response(500, json={"errorCode": "ERR_DIRECT_INVOKE"})
        self.calls.append((app_id, request.method, path))
        # The whole target goes on: the path and its query string.
        target = request.url.raw_path.decode("ascii").removeprefix(
            f"{_INVOKE}{app_id}{_METHOD}"
        )
        forwarded = httpx.Request(
            request.method,
            f"http://{app_id}{target}",
            headers=[
                (name, value)
                for name, value in request.headers.items()
                if name.lower() != "host"
            ],
            content=await request.aread(),
        )
        return await self._apps[app_id].handle_async_request(forwarded)


class RunningService(httpx.AsyncBaseTransport):
    """Reaches a service that is already running in a test client.

    `workflow` runs with its worker, and its database connections belong to
    the loop it was started on. A call from another service's loop goes
    through its test client, which hands it over to that loop.
    """

    def __init__(self, client: TestClient) -> None:
        self._client = client

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        content = await request.aread()
        answer = await asyncio.to_thread(
            self._client.request,
            request.method,
            # The path and its query string.
            request.url.raw_path.decode("ascii"),
            content=content,
            headers={
                name: value
                for name, value in request.headers.items()
                if name.lower() not in {"host", "content-length"}
            },
        )
        return httpx.Response(
            answer.status_code,
            content=answer.content,
            # The answer's own headers, but for those that describe how its
            # body was sent, which is decoded by now.
            headers=[
                (name, value)
                for name, value in answer.headers.items()
                if name.lower() not in _BODY_FRAMING
            ],
        )


@dataclass
class LocalIntake:
    """`intake` on a test's database and blob containers, with the Language stand-in behind it."""

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

    def redact(self, case_id: str) -> list[str]:
        """Redact a case directly, as `workflow` would command it; return its page ids."""
        with TestClient(self.app()) as client:
            result = client.post(f"/cases/{case_id}/redaction", json={}).json()
        assert result["status"] == "done"
        return list(result["page_ids"])


@dataclass
class LocalClassification:
    """`classification` on a test's database, with the model stand-in behind its gateway."""

    settings: ClassificationSettings
    model: FoundryStandIn
    intake: LocalIntake
    # The sidecar of every instance made, so a test can see what was read.
    sidecars: list[ServicesBehindSidecar] = field(default_factory=list)

    def app(self) -> Any:
        """A new instance of the service, reading its pages from the real `intake`."""
        sidecar = ServicesBehindSidecar(intake=self.intake.app())
        self.sidecars.append(sidecar)
        return create_classification(
            self.settings,
            sidecar=sidecar,
            model=httpx2.ASGITransport(app=self.model.app()),
        )

    def reads_of_intake(self) -> list[tuple[str, str]]:
        """Every call the service made to `intake`, as method and path."""
        return [
            (method, path)
            for sidecar in self.sidecars
            for _, method, path in sidecar.calls
        ]

    def listed(self, case_id: str) -> ClassificationList:
        """`GET /cases/{case_id}/classifications` on the service."""
        with TestClient(self.app()) as client:
            return ClassificationList.model_validate(
                client.get(f"/cases/{case_id}/classifications").json()
            )


@contextlib.contextmanager
def workflow_service(
    settings: WorkflowSettings, sidecar: httpx.AsyncBaseTransport
) -> Iterator[TestClient]:
    """`workflow` as it really runs: its own role, its worker, the emulator."""
    with TestClient(
        create_workflow(settings.model_copy(update=FAST_RETRIES), sidecar=sidecar),
        raise_server_exceptions=False,
    ) as client:
        yield client


def start_and_wait(
    workflow_settings: WorkflowSettings,
    scheduler_client: DurableTaskSchedulerClient,
    sidecar: ServicesBehindSidecar,
    case_id: str,
    waits_for_a_human: bool = False,
    **options: Any,
) -> tuple[CaseProgress, AuditTrail, dict[str, Any]]:
    """Start the case, wait for its lifecycle to come to rest, and read what it left.

    A case whose page waits for a person keeps its lifecycle alive (story
    1.10): it is waited for until it says so, and then ended, since nobody
    decides its pages here. The last value is the lifecycle's own answer, of
    a lifecycle that ended by itself.
    """
    with workflow_service(workflow_settings, sidecar) as client:
        try:
            assert (
                client.post(f"/cases/{case_id}/start", json=options).status_code == 200
            )
            output: dict[str, Any] = {}
            if waits_for_a_human:
                wait_for_case_status(client, case_id, "awaiting_human", 90)
            else:
                state = completed(scheduler_client, case_id)
                output = json.loads(state.serialized_output or "")
            progress = CaseProgress.model_validate(
                client.get(f"/cases/{case_id}/progress").json()
            )
            trail = AuditTrail.model_validate(
                client.get(f"/cases/{case_id}/audit").json()
            )
        finally:
            # Also when the test fails on the way: no lifecycle is left waiting.
            end_lifecycle(scheduler_client, case_id)
    return progress, trail, output


def end_lifecycle(scheduler_client: DurableTaskSchedulerClient, case_id: str) -> None:
    """End the case's orchestration if it is still alive; do nothing if it is not."""
    state = scheduler_client.get_orchestration_state(case_id, fetch_payloads=False)
    if state is None or state.runtime_status not in _ALIVE:
        return
    scheduler_client.terminate_orchestration(case_id)
    scheduler_client.wait_for_orchestration_completion(case_id, timeout=90)


@contextlib.contextmanager
def web_service(
    spa_dir: Path, sidecar: httpx.AsyncBaseTransport
) -> Iterator[TestClient]:
    """`web` as it really runs, with the other services behind its sidecar."""
    settings = WebSettings(spa_dir=spa_dir, applicationinsights_connection_string=None)
    with TestClient(
        create_web(settings, sidecar=sidecar), raise_server_exceptions=False
    ) as client:
        yield client
