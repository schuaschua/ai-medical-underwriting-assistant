"""What the cross-service tests share: the real services, wired together without Dapr.

These tests run `workflow`, `intake`, `classification`, `extraction`, `retrieval` and `verdict` as they really run,
against the containers of compose.yaml, with the stand-ins of this package
where Azure AI Language and the Foundry chat deployment would be. Where the
Dapr sidecars would be, a transport hands a service invocation to the app of
the service it names. `retrieval`'s ingestion job runs the same way, with the
stand-ins where Document Intelligence and the Foundry deployments would be
(story 2.2).

The helpers live with the stand-ins' tests, on pytest's `pythonpath`, and not
under `services/`: nothing there may name the generator's package or the
answer key (spine AD-17).
"""

import asyncio
import contextlib
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import httpx2
import psycopg
from azure.storage.blob import BlobServiceClient
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.client import OrchestrationState, OrchestrationStatus
from fastapi.testclient import TestClient
from workflow_local import connect, wait_for_case_status

from classification import train as training_job
from classification.adapters.blob import upload_local_training_pages
from classification.adapters.http.app import create_app as create_classification
from classification.settings import Settings as ClassificationSettings
from contracts.models.classification import ClassificationList
from contracts.models.extraction import FactList
from contracts.models.intake import PageList
from contracts.models.verdict import AgentStepList, VerdictRunList
from contracts.models.workflow import AuditTrail, CaseProgress
from extraction.adapters.http.app import create_app as create_extraction
from extraction.settings import Settings as ExtractionSettings
from intake.adapters.http.app import create_app as create_intake
from intake.settings import Settings as IntakeSettings
from retrieval import ingest as ingest_job
from retrieval.adapters.blob import upload_local_manual
from retrieval.adapters.db import SqlChunkRepository
from retrieval.adapters.db import build_database as build_retrieval_database
from retrieval.adapters.http.app import create_app as create_retrieval
from retrieval.settings import Settings as RetrievalSettings
from synthdata.classifier_standin import ClassifierStandIn, blob_container_reader
from synthdata.foundry_standin import FoundryStandIn
from synthdata.generate import RULE_TABLE_FILE
from synthdata.language_standin import EMULATOR, LanguageStandIn
from synthdata.layout_standin import DEFAULT_PORT as DOCUMENT_INTELLIGENCE_PORT
from synthdata.layout_standin import LayoutStandIn
from synthdata.manual import MANUAL_FILE_NAME
from synthdata.search_standin import SearchStandIn
from verdict.adapters.http.app import create_app as create_verdict
from verdict.settings import Settings as VerdictSettings
from web.adapters.http.app import create_app as create_web
from web.settings import Settings as WebSettings
from workflow.adapters.http.app import create_app as create_workflow
from workflow.settings import Settings as WorkflowSettings

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CASES_DIR = REPOSITORY_ROOT / "data" / "cases"
ANSWER_KEY_DIR = REPOSITORY_ROOT / "data" / "answer-key" / "cases"
MANUAL_PDF = REPOSITORY_ROOT / "data" / "manual" / MANUAL_FILE_NAME
PDF = {"Content-Type": "application/pdf"}
# The id of the classifier the tests train and ask (story 4.2).
CLASSIFIER_ID = "page-types-test"
# Where the stand-in for Azure AI Search is said to be (`synthdata.search_standin`).
SEARCH_ENDPOINT = "http://127.0.0.1:5103"
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


def rule_table() -> dict[str, dict[str, Any]]:
    """The answer key's rule table, by `rule_id`. Only tests outside `services/` read it."""
    table = json.loads((REPOSITORY_ROOT / "data" / RULE_TABLE_FILE).read_text())
    return {rule["rule_id"]: rule for rule in table["rules"]}


def query(settings: WorkflowSettings, statement: str, *parameters: object) -> list[Any]:
    with connect(settings) as connection:
        return connection.execute(statement, parameters).fetchall()


def audit_rows(settings: WorkflowSettings, case_id: str) -> list[tuple[Any, ...]]:
    return query(
        settings,
        "SELECT action, page_id::text, error_code, detail, actor "
        # In the order `workflow` wrote them (story 1.12).
        "FROM workflow.audit_event WHERE case_id = %s ORDER BY audit_event_seq",
        case_id,
    )


def completed(client: DurableTaskSchedulerClient, case_id: str) -> OrchestrationState:
    state = client.wait_for_orchestration_completion(case_id, timeout=90)
    assert state is not None
    assert state.runtime_status is OrchestrationStatus.COMPLETED
    return state


class ServicesBehindSidecar(httpx.AsyncBaseTransport):
    """Stands in for the Dapr sidecars: an invocation by app id reaches that service's app.

    `calls` notes every invocation as app id, method and path, in order;
    `targets` the same with the query string, where a test needs to see it.
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
        self.targets: list[tuple[str, str, str]] = []

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
        self.targets.append((app_id, request.method, target))
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
    """`classification` on a test's database, with the model stand-in behind its gateway.

    `classifier` is the stand-in for Document Intelligence's custom
    classifier (story 4.2). While it is None the service is told of no
    classifier, as before the second contender was built; a test that asks
    for one (`with_classifier`) gets the `doc-intelligence` contender and
    the training job on the test's own training container.
    """

    settings: ClassificationSettings
    model: FoundryStandIn
    intake: LocalIntake
    classifier: ClassifierStandIn | None = None
    # The sidecar of every instance made, so a test can see what was read.
    sidecars: list[ServicesBehindSidecar] = field(default_factory=list)

    def with_classifier(self) -> ClassifierStandIn:
        """Put the classifier stand-in behind the service and its job; return it.

        A build reads the training pages from the blob emulator, as the
        stand-in's own process does.
        """
        self.classifier = ClassifierStandIn(
            blob_container_reader(BlobServiceClient.from_connection_string(EMULATOR))
        )
        self.settings = self.settings.model_copy(
            update={
                # Where the stand-in listens when it runs as a process. The
                # tests hand the app a transport to it and never use the network.
                "doc_intelligence_endpoint": (
                    f"http://127.0.0.1:{DOCUMENT_INTELLIGENCE_PORT}"
                ),
                "doc_intelligence_classifier_id": CLASSIFIER_ID,
                # A running analysis or build is looked at again at once.
                "doc_intelligence_poll_seconds": 0.01,
                "training_poll_seconds": 0.01,
            }
        )
        return self.classifier

    def _classifier_transport(self) -> httpx2.AsyncBaseTransport | None:
        if self.classifier is None:
            return None
        return httpx2.ASGITransport(app=self.classifier.app())

    def app(self) -> Any:
        """A new instance of the service, reading its pages from the real `intake`."""
        sidecar = ServicesBehindSidecar(intake=self.intake.app())
        self.sidecars.append(sidecar)
        return create_classification(
            self.settings,
            sidecar=sidecar,
            model=httpx2.ASGITransport(app=self.model.app()),
            classifier=self._classifier_transport(),
        )

    def upload_training_pages(self, folder: Path) -> int:
        """Put a folder of prepared pages into this test's training container, as an operator does."""
        return upload_local_training_pages(self.settings, folder)

    def train(self) -> int:
        """Run the training job once (`python -m classification.train`); its exit status."""
        return training_job.main(self.settings, self._classifier_transport())

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


@dataclass
class LocalExtraction:
    """`extraction` on a test's database, with the model stand-in behind its gateway (story 2.4)."""

    settings: ExtractionSettings
    model: FoundryStandIn
    intake: LocalIntake
    # The sidecar of every instance made, so a test can see what was read.
    sidecars: list[ServicesBehindSidecar] = field(default_factory=list)

    def app(self) -> Any:
        """A new instance of the service, reading its pages from the real `intake`."""
        sidecar = ServicesBehindSidecar(intake=self.intake.app())
        self.sidecars.append(sidecar)
        return create_extraction(
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

    def facts(self, case_id: str) -> FactList:
        """`GET /cases/{case_id}/facts` on the service."""
        with TestClient(self.app()) as client:
            return FactList.model_validate(client.get(f"/cases/{case_id}/facts").json())


# Every stored chunk, whole: its columns and its row version, which changes
# whenever the row is written.
_ALL_CHUNKS = (
    "SELECT chunk_id, chunk_set, rule_ids, reference_rule_ids, section_id, "
    "impairment, manual_page, text, context_line, embedding::text, content_hash, "
    "xmin::text FROM retrieval.chunk ORDER BY chunk_id"
)
CHUNK_FIELDS = (
    "chunk_id",
    "chunk_set",
    "rule_ids",
    "reference_rule_ids",
    "section_id",
    "impairment",
    "manual_page",
    "text",
    "context_line",
    "embedding",
    "content_hash",
    "row_version",
)


@dataclass
class LocalRetrieval:
    """`retrieval`'s ingestion job on a test's database and manual container.

    The job runs as it really runs (`python -m retrieval.ingest`), with the
    layout stand-in where Document Intelligence would be and the model
    stand-in where the Foundry deployments would be.

    `search` is the stand-in for Azure AI Search (story 3.3). While it is
    None the job and the service are told of no search service, as before
    row `r5` was built; a test that sets it gets the index loaded by the
    job and rows `r5` and `r6` answered by the service.
    """

    settings: RetrievalSettings
    layout: LayoutStandIn
    model: FoundryStandIn
    search: SearchStandIn | None = None

    def _settings(self, changes: dict[str, Any]) -> RetrievalSettings:
        """The settings of a run: with the search service's endpoint when there is a stand-in for it."""
        told = dict(changes)
        if self.search is not None:
            # Where the stand-in listens when it runs as a process. The
            # tests hand the job a transport to it and never use the network.
            told.setdefault("search_service_endpoint", SEARCH_ENDPOINT)
            told.setdefault("search_service_check_wait_seconds", 0.0)
            told.setdefault("search_service_retry_seconds", 0.01)
        return self.settings.model_copy(update=told)

    def _search_transport(self) -> httpx2.AsyncBaseTransport | None:
        if self.search is None:
            return None
        return httpx2.ASGITransport(app=self.search.app())

    def upload(self, pdf: Path = MANUAL_PDF) -> None:
        """Put a manual PDF into this test's `manual` container."""
        upload_local_manual(self.settings, pdf)

    def ingest(self, **changes: Any) -> int:
        """Run the job once; its exit status."""
        return ingest_job.main(
            self._settings(changes),
            ingest_job.Transports(
                layout=httpx2.ASGITransport(app=self.layout.app()),
                model=httpx2.ASGITransport(app=self.model.app()),
                search=self._search_transport(),
            ),
        )

    def load_index(self) -> None:
        """Run the job's last steps alone: load the search stand-in's index from the stored chunks, then have it hold row `r6`'s knowledge base over that index."""
        settings = self._settings({})

        async def load() -> tuple[object, object]:
            database = build_retrieval_database(settings)
            try:
                loaded = await ingest_job.load_index(
                    settings, SqlChunkRepository(database), self._search_transport()
                )
            finally:
                await database.dispose()
            return loaded, await ingest_job.make_knowledge_base(
                settings, self._search_transport()
            )

        for outcome in asyncio.run(load()):
            assert not isinstance(outcome, Exception), outcome

    def chunks(self) -> dict[str, dict[str, Any]]:
        """Every stored chunk, by `chunk_id`."""
        with psycopg.connect(
            host=self.settings.database_host,
            port=self.settings.database_port,
            dbname=self.settings.database_name,
            user=self.settings.database_user,
        ) as connection:
            rows = connection.execute(_ALL_CHUNKS).fetchall()
        return {row[0]: dict(zip(CHUNK_FIELDS, row, strict=True)) for row in rows}

    @property
    def model_calls(self) -> int:
        """Chat and embedding calls the model stand-in has had."""
        return self.model.calls + self.model.embedding_calls

    @contextlib.contextmanager
    def service(self, **changes: Any) -> Iterator[TestClient]:
        """The `retrieval` service on this test's index (story 2.3).

        The app exactly as the server builds it from its settings, but for
        the transport that puts the model stand-in where the embedding
        deployment would be. Its gateway and its engine are closed with it.
        """
        with TestClient(self.app(**changes), raise_server_exceptions=False) as client:
            yield client

    def app(self, **changes: Any) -> Any:
        """A new instance of the service, with the model stand-in behind its gateway."""
        return create_retrieval(
            self._settings(changes),
            model_transport=httpx2.ASGITransport(app=self.model.app()),
            search_transport=self._search_transport(),
        )


@dataclass
class LocalVerdict:
    """`verdict` on a test's database, as it really runs (stories 2.5 and 2.6).

    The agent runs on Microsoft Agent Framework with the model stand-in
    behind the service's gateway. Its tools reach the real `extraction` for
    the case's facts and the real `retrieval`, over the ingested manual, for
    the rules. `model` is the stand-in the agent talks to: a stand-in of its
    own, so that what the agent and the search cost is not counted with the
    classifier's and the extraction's calls.
    """

    settings: VerdictSettings
    model: FoundryStandIn
    extraction: LocalExtraction
    retrieval: LocalRetrieval
    # The sidecar of every instance made, so a test can see what was called.
    sidecars: list[ServicesBehindSidecar] = field(default_factory=list)

    def app(self, **changes: Any) -> Any:
        """A new instance of the service, with the real `extraction` and `retrieval` behind its sidecar."""
        sidecar = ServicesBehindSidecar(
            extraction=self.extraction.app(),
            retrieval=self.retrieval.app(),
        )
        self.sidecars.append(sidecar)
        return create_verdict(
            self.settings.model_copy(update=changes),
            sidecar=sidecar,
            model=httpx2.ASGITransport(app=self.model.app()),
        )

    def calls(self, app_id: str) -> list[tuple[str, str]]:
        """Every call the service made to one other service, as method and path."""
        return [
            (method, path)
            for sidecar in self.sidecars
            for called, method, path in sidecar.calls
            if called == app_id
        ]

    def runs(self, case_id: str) -> VerdictRunList:
        """`GET /cases/{case_id}/verdict-runs` on the service."""
        with TestClient(self.app()) as client:
            return VerdictRunList.model_validate(
                client.get(f"/cases/{case_id}/verdict-runs").json()
            )

    def steps(self, verdict_run_id: str) -> AgentStepList:
        """`GET /verdict-runs/{verdict_run_id}/steps` on the service."""
        with TestClient(self.app()) as client:
            return AgentStepList.model_validate(
                client.get(f"/verdict-runs/{verdict_run_id}/steps").json()
            )

    def case_steps(self, case_id: str, **filters: str) -> AgentStepList:
        """`GET /cases/{case_id}/agent-steps` on the service, with its optional filters."""
        with TestClient(self.app()) as client:
            return AgentStepList.model_validate(
                client.get(f"/cases/{case_id}/agent-steps", params=filters).json()
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


def wait_for_extractions(
    client: TestClient, case_id: str, timeout_seconds: float = 90.0
) -> dict[str, Any]:
    """Read the case's progress on `workflow` until no page is `extracting`; return it.

    A page the gate or an accept sends on is extracted beside whatever else
    the case waits for (story 2.4). A test that looks at a waiting case
    waits for those extractions first, so that what it sees does not depend
    on how far they have got.
    """
    deadline = time.monotonic() + timeout_seconds
    progress: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/cases/{case_id}/progress")
        progress = response.json() if response.status_code == 200 else {}
        pages = progress.get("pages", [])
        if pages and all(page["page_status"] != "extracting" for page in pages):
            return progress
        # Not a wait for time to pass: the other threads get their turn.
        time.sleep(0.05)
    raise AssertionError(f"case {case_id} still has pages in extraction: {progress}")


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
    1.10): it is waited for until it says so and the pages the gate sent on
    are extracted (story 2.4), and then ended, since nobody decides its
    pages here. The last value is the lifecycle's own answer, of
    a lifecycle that ended by itself.
    """
    with workflow_service(workflow_settings, sidecar) as client:
        try:
            assert (
                client.post(
                    # Story 1.13: a start names the demo role that asks, as
                    # `web` passes it on.
                    f"/cases/{case_id}/start",
                    json={"actor": "customer", **options},
                ).status_code
                == 200
            )
            output: dict[str, Any] = {}
            if waits_for_a_human:
                wait_for_case_status(client, case_id, "awaiting_human", 90)
                wait_for_extractions(client, case_id)
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
