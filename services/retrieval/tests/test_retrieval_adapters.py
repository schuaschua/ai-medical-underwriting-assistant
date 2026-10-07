"""Story 2.2: the adapters of `retrieval`, without a database, a model or a network.

Unit tests: the settings, the Document Intelligence client and the model
gateway (each against a transport that stands in for the service), the
manual's blob, the Entra token, telemetry and the bundled migrations.
"""

import asyncio
import base64
import json
import logging
import re
import runpy
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
import pytest
from azure.core.exceptions import ResourceNotFoundError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from pgvector.sqlalchemy import VECTOR
from pydantic import SecretStr, ValidationError
from retrieval_fakes import (
    CHAT,
    EMBEDDING,
    PDF,
    analyze_result,
    completion,
    context_answer,
    embedding_answer,
    manual,
    vector_for,
)
from sqlalchemy import create_engine

from contracts.rules import RULE_ID_PATTERN
from retrieval import ingest as ingest_module
from retrieval.adapters import credential as credential_module
from retrieval.adapters.blob import (
    BlobManualStore,
    build_blob_service,
    upload_local_manual,
)
from retrieval.adapters.db import (
    POSTGRESQL_TOKEN_SCOPE,
    RULE_ID_AS_WORDS,
    TEXT_SEARCH_CONFIG,
    EntraToken,
    build_database,
    chunk_table,
    database_url,
    full_text_of,
    metadata,
    use_entra_token,
)
from retrieval.adapters.http.app import create_app
from retrieval.adapters.http.routes import Dependencies
from retrieval.adapters.layout import (
    DocumentLayout,
    build_layout_http,
    layout_of,
    layout_token_for,
)
from retrieval.adapters.migrations import (
    MIGRATIONS_DIR,
    alembic_config,
    bundled_head,
    include_name,
)
from retrieval.adapters.model import (
    COGNITIVE_SERVICES_SCOPE,
    CONTEXT_SCHEMA,
    NO_KEY,
    ModelGateway,
    build_model_client,
    chat_deployment,
    embedding_deployment,
    model_base_url,
    model_token_for,
)
from retrieval.adapters.telemetry import (
    EXCLUDED_URLS,
    QUIET_LOGGERS,
    adapter_span,
    configure_logging,
    configure_telemetry,
    current_trace_id,
    instrument_app,
    shutdown_telemetry,
)
from retrieval.domain.entities import EMBEDDING_DIMENSIONS
from retrieval.domain.ports import (
    LayoutFailed,
    ManualMissing,
    ModelAnswerInvalid,
    ModelCallFailed,
    ModelUnavailable,
)
from retrieval.prompts import CHUNK_CONTEXT, load_prompt, prompt_digest
from retrieval.settings import APP_ID, SCHEMA, Settings, get_settings

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SERVICE_DIR.parents[1]
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"
FOUNDRY = "https://aif-aiuw-demo-wus3.cognitiveservices.azure.com"
DOCUMENT_INTELLIGENCE = "https://di-aiuw-demo-wus3.cognitiveservices.azure.com"
RULE_PLACE = "Section: 2 SECRET-SECTION\nRule text:\nRule UW-AA-001: SECRET-RULE."


# --- Settings ----------------------------------------------------------------------------


def test_story_2_2_settings_read_the_retrieval_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RETRIEVAL_PORT", "9104")
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_DEPLOYMENT", "embedding-large")
    monkeypatch.setenv("RETRIEVAL_MANUAL_BLOB_NAME", "another.pdf")
    monkeypatch.setenv("CLASSIFICATION_CHAT_DEPLOYMENT", "not-ours")

    settings = Settings()

    assert (APP_ID, SCHEMA) == ("retrieval", "retrieval")
    assert settings.port == 9104
    assert settings.embedding_deployment == "embedding-large"
    assert settings.manual_blob_name == "another.pdf"
    assert settings.chat_deployment is None


def test_story_2_2_settings_default_to_the_local_containers_and_the_spines_names() -> (
    None
):
    settings = Settings()

    assert (settings.host, settings.port) == ("127.0.0.1", 8004)
    assert (settings.database_host, settings.database_name) == ("127.0.0.1", "aiuw")
    # AD-4: the container `retrieval` owns, and the manual's name in it.
    assert settings.manual_container == "manual"
    assert settings.manual_blob_name == "underwriting-manual.pdf"
    # AD-16: three retries; the layout model by its name.
    assert settings.model_max_retries == 3
    assert settings.layout_model == "prebuilt-layout"
    # Nothing is assumed about where Azure is: no endpoint, no deployment.
    assert settings.model_endpoint is None
    assert settings.layout_endpoint is None
    assert settings.embedding_deployment is None
    assert settings.model_entra_auth is settings.layout_entra_auth is False


def test_story_2_2_blank_variables_count_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "MODEL_ENDPOINT",
        "LAYOUT_ENDPOINT",
        "CHAT_DEPLOYMENT",
        "EMBEDDING_DEPLOYMENT",
        "BLOB_ACCOUNT_URL",
        "BLOB_CONNECTION_STRING",
        "AZURE_CLIENT_ID",
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
    ):
        monkeypatch.setenv(f"RETRIEVAL_{name}", "  ")

    settings = Settings()

    assert settings.model_endpoint is None
    assert settings.layout_endpoint is None
    assert settings.embedding_deployment is None
    assert settings.blob_connection_string is None
    assert settings.applicationinsights_connection_string is None


@pytest.mark.parametrize(
    "values",
    [
        # A stand-in that is not on this machine, or that would be sent a token.
        {"model_endpoint": "http://models.example.com"},
        {"layout_endpoint": "http://10.0.0.5:5102"},
        {"model_endpoint": "http://127.0.0.1:5101", "model_entra_auth": True},
        {"layout_endpoint": "http://localhost:5102", "layout_entra_auth": True},
        # The real service without the identity.
        {"model_endpoint": FOUNDRY},
        {"layout_endpoint": DOCUMENT_INTELLIGENCE},
        # Not an endpoint alone.
        {"model_endpoint": f"{FOUNDRY}/openai/v1", "model_entra_auth": True},
        {"layout_endpoint": f"{DOCUMENT_INTELLIGENCE}?x=1", "layout_entra_auth": True},
        {"model_endpoint": "ftp://127.0.0.1"},
        # Two ways to Blob Storage at once.
        {
            "blob_account_url": "https://staiuwdemowus3.blob.core.windows.net",
            "blob_connection_string": "UseDevelopmentStorage=true",
        },
        {"chat_deployment": " padded "},
        {"embedding_deployment": "padded "},
        {"manual_blob_name": " "},
        {"model_retry_seconds": 60.0, "model_max_retry_seconds": 30.0},
    ],
)
def test_story_2_2_settings_that_would_reach_a_service_unsafely_are_refused(
    values: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        Settings(**values)


def test_story_2_2_the_two_safe_ways_to_each_service_are_accepted() -> None:
    local = Settings(
        model_endpoint="http://127.0.0.1:5101", layout_endpoint="http://localhost:5102/"
    )
    azure = Settings(
        model_endpoint=FOUNDRY,
        model_entra_auth=True,
        layout_endpoint=f"{DOCUMENT_INTELLIGENCE}/",
        layout_entra_auth=True,
    )

    assert model_base_url(local) == "http://127.0.0.1:5101/openai/v1/"
    assert model_base_url(azure) == f"{FOUNDRY}/openai/v1/"
    assert model_token_for(local) is None and layout_token_for(local) is None


def test_story_2_2_a_refused_endpoint_names_its_variable() -> None:
    with pytest.raises(ValidationError) as model:
        Settings(model_endpoint="http://models.example.com")
    with pytest.raises(ValidationError) as layout:
        Settings(layout_endpoint=DOCUMENT_INTELLIGENCE)

    assert "RETRIEVAL_MODEL_ENDPOINT" in str(model.value)
    assert "RETRIEVAL_LAYOUT_ENTRA_AUTH=true" in str(layout.value)


def test_story_2_2_what_the_job_needs_must_be_configured() -> None:
    bare = Settings()

    # The service answers its probes without any of them; the job does not start.
    for build in (
        model_base_url,
        chat_deployment,
        embedding_deployment,
        build_layout_http,
        build_blob_service,
    ):
        with pytest.raises(ValueError, match="Set RETRIEVAL_"):
            build(bare)


def test_story_2_2_a_job_that_is_not_configured_says_what_is_missing(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        status = ingest_module.main(Settings())

    # Non-zero, before anything is built or called.
    assert status == 1
    assert (
        "ingestion failed: code=internal_error reason=not_configured "
        "missing=RETRIEVAL_LAYOUT_ENDPOINT,RETRIEVAL_MODEL_ENDPOINT,"
        "RETRIEVAL_CHAT_DEPLOYMENT,RETRIEVAL_EMBEDDING_DEPLOYMENT,"
        "RETRIEVAL_BLOB_ACCOUNT_URL"
    ) in caplog.text
    # With the stand-ins and the emulator named, nothing is missing.
    local = settings.model_copy(
        update={"blob_connection_string": SecretStr("UseDevelopmentStorage=true")}
    )
    assert ingest_module.missing_settings(local) == []


def test_story_2_2_a_job_that_cannot_be_set_up_says_so_in_its_one_line(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    settings: Settings,
) -> None:
    # Settings the job refuses: the error's message repeats the refused value.
    monkeypatch.setenv("RETRIEVAL_MODEL_ENDPOINT", "http://SECRET-HOST.example.com")
    get_settings.cache_clear()
    local = settings.model_copy(
        update={"blob_connection_string": SecretStr("UseDevelopmentStorage=true")}
    )

    def no_telemetry(given: Settings) -> bool:
        raise RuntimeError("SECRET-CONNECTION-STRING is not one")

    try:
        with caplog.at_level(logging.ERROR):
            refused = ingest_module.main()
            monkeypatch.setattr(ingest_module, "configure_telemetry", no_telemetry)
            no_exporter = ingest_module.main(local)
    finally:
        get_settings.cache_clear()

    # Non-zero, with the one line and the error's type: no traceback.
    assert (refused, no_exporter) == (1, 1)
    assert "ingestion failed: code=internal_error reason=setup_ValidationError" in (
        caplog.text
    )
    assert "ingestion failed: code=internal_error reason=setup_RuntimeError" in (
        caplog.text
    )
    assert "SECRET" not in caplog.text
    assert "Traceback" not in capsys.readouterr().err


def test_story_2_2_the_jobs_telemetry_is_sent_before_the_process_ends(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    local = settings.model_copy(
        update={"blob_connection_string": SecretStr("UseDevelopmentStorage=true")}
    )
    order: list[str] = []

    async def failing_run(given: Settings, transports: Any) -> Any:
        order.append("run")
        raise RuntimeError("the run broke")

    monkeypatch.setattr(ingest_module, "configure_telemetry", lambda given: True)
    monkeypatch.setattr(ingest_module, "run", failing_run)
    monkeypatch.setattr(
        ingest_module, "shutdown_telemetry", lambda: order.append("shutdown")
    )

    # Also when the run fails: the failure is what most needs to arrive.
    assert ingest_module.main(local) == 1
    assert order == ["run", "shutdown"]
    # Without telemetry there is nothing to send, and nothing is shut down.
    monkeypatch.setattr(ingest_module, "configure_telemetry", lambda given: False)
    assert ingest_module.main(local) == 1
    assert order == ["run", "shutdown", "run"]


def test_story_2_2_shutting_telemetry_down_flushes_every_provider_and_never_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    done: list[str] = []

    class Provider:
        def __init__(self, name: str, broken: bool = False) -> None:
            self.name, self.broken = name, broken

        def force_flush(self) -> None:
            if self.broken:
                raise RuntimeError("SECRET-ENDPOINT refused")
            done.append(f"{self.name}:flush")

        def shutdown(self) -> None:
            done.append(f"{self.name}:shutdown")

    import opentelemetry._logs
    import opentelemetry.metrics

    monkeypatch.setattr(trace, "get_tracer_provider", lambda: Provider("traces"))
    monkeypatch.setattr(
        opentelemetry.metrics, "get_meter_provider", lambda: Provider("metrics", True)
    )
    # A provider that was never set up has nothing to flush.
    monkeypatch.setattr(opentelemetry._logs, "get_logger_provider", object)

    with caplog.at_level(logging.WARNING):
        shutdown_telemetry()

    assert done == ["traces:flush", "traces:shutdown", "metrics:shutdown"]
    assert "telemetry force_flush failed: type=RuntimeError" in caplog.text
    assert "SECRET" not in caplog.text


def test_story_2_2_the_deployment_names_reach_code_only_as_settings(
    settings: Settings,
) -> None:
    options = ingest_module.ingest_options(settings)

    assert (options.chat_deployment, options.embedding_deployment) == (CHAT, EMBEDDING)
    assert options.prompt_digest == prompt_digest(CHUNK_CONTEXT)
    assert options.context_line_max_chars == settings.context_line_max_chars
    assert options.embedding_batch_size == settings.embedding_batch_size
    # The guard on removals and the job's own deadline are settings as well.
    assert (options.max_removed_share, options.allow_large_removal) == (0.1, False)
    assert options.deadline_seconds == settings.ingest_deadline_seconds == 1800.0
    # AD-16: no model is named in the code.
    for source in (SERVICE_DIR / "src").rglob("*.py"):
        text = source.read_text()
        assert not re.search(r"""["']text-embedding-3""", text), source.name
        assert "gpt-" not in text, source.name


def test_story_2_2_connection_strings_are_not_shown_when_settings_are_printed() -> None:
    settings = Settings(
        applicationinsights_connection_string=SecretStr("InstrumentationKey=secret"),
        blob_connection_string=SecretStr("AccountKey=secret"),
    )

    assert "secret" not in repr(settings)


def test_story_2_2_the_app_stack_sets_only_variables_the_settings_read() -> None:
    stack = REPOSITORY_ROOT / "infra" / "demo" / "app"
    text = (stack / "main.tf").read_text() + (stack / "locals.tf").read_text()
    names = set(re.findall(r'name\s*=\s*"RETRIEVAL_([A-Z_]+)"', text))

    assert names, "the app stack does not configure retrieval"
    assert names <= {name.upper() for name in Settings.model_fields}
    # What only the environment can say: where the manual, Document
    # Intelligence and the models are, what the deployments are called
    # (AD-16), and that the identity is used for all of them.
    assert {
        "DATABASE_ENTRA_AUTH",
        "AZURE_CLIENT_ID",
        "BLOB_ACCOUNT_URL",
        "MANUAL_CONTAINER",
        "MANUAL_BLOB_NAME",
        "LAYOUT_ENDPOINT",
        "LAYOUT_ENTRA_AUTH",
        "MODEL_ENDPOINT",
        "MODEL_ENTRA_AUTH",
        "CHAT_DEPLOYMENT",
        "EMBEDDING_DEPLOYMENT",
    } <= names
    # No key, no password and no connection string is handed to the service.
    assert not any(
        "KEY" in name or "PASSWORD" in name or "BLOB_CONNECTION" in name
        for name in names
    )
    # The job is the service's image and identity with another command, and
    # the same settings: it is given no setting the service is not.
    main = (stack / "main.tf").read_text()
    job = main[main.index('module "retrieval_ingest"') :]
    assert 'command = ["python", "-m", "retrieval.ingest"]' in job
    assert "env     = local.retrieval_env" in job
    assert "local.retrieval_identity.id" in job
    assert "${local.image_repositories.retrieval}:${var.image_tag}" in job
    # It calls no service of ours: no sidecar and no ingress.
    assert "dapr" not in job and "ingress" not in job


def test_story_2_2_the_local_start_runs_the_service_and_the_job_against_the_stand_ins() -> (
    None
):
    run_file = (REPOSITORY_ROOT / "dapr.yaml").read_text()
    tools = REPOSITORY_ROOT / "tools"
    ingest = (tools / "ingest-local.sh").read_text()
    migrate = (tools / "migrate-local.sh").read_text()
    start = (tools / "dev.sh").read_text()

    assert "appID: retrieval" in run_file
    assert 'RETRIEVAL_PORT: "8004"' in run_file and "daprHTTPPort: 3504" in run_file
    # The migration and the manual's upload, then the job, then the services.
    assert "alembic -c services/retrieval/alembic.ini upgrade head" in migrate
    assert "python -m retrieval.local_setup data/manual/underwriting-manual.pdf" in (
        migrate
    )
    assert "uv run python -m retrieval.ingest" in ingest
    assert (
        start.index("./tools/migrate-local.sh")
        < start.index("./tools/ingest-local.sh")
        < start.index("dapr run --run-file dapr.yaml")
    )
    # Every variable the script sets is one the settings read, and every
    # endpoint it names is on this machine.
    exported = set(re.findall(r"export RETRIEVAL_([A-Z_]+)=", ingest))
    assert exported <= {name.upper() for name in Settings.model_fields}
    assert {"LAYOUT_ENDPOINT", "MODEL_ENDPOINT", "EMBEDDING_DEPLOYMENT"} <= exported
    assert re.findall(r"_ENDPOINT=\"(.+?)\"", ingest) == [
        "http://127.0.0.1:${layout_port}",
        "http://127.0.0.1:${model_port}",
    ]


# --- The prompt --------------------------------------------------------------------------


def test_story_2_2_the_prompt_is_a_file_in_the_package_under_version_control() -> None:
    prompt_file = SERVICE_DIR / "src" / "retrieval" / "prompts" / CHUNK_CONTEXT

    prompt = load_prompt(CHUNK_CONTEXT)

    assert prompt == prompt_file.read_text().strip()
    # It asks for the one field the answer schema has, and for one line.
    assert CONTEXT_SCHEMA["properties"] == {"context_line": {"type": "string"}}
    assert CONTEXT_SCHEMA["required"] == ["context_line"]
    assert CONTEXT_SCHEMA["additionalProperties"] is False
    assert "context_line" in prompt and "one line" in prompt
    # A changed prompt has another digest, so the next run writes every line again.
    assert len(prompt_digest(CHUNK_CONTEXT)) == 64
    # azure.md rule 27: where its evaluation set is said beside the prompt.
    note = (prompt_file.parent / "__init__.py").read_text()
    assert "Epic 3" in note and "rule 27" in note


# --- The model gateway -------------------------------------------------------------------


@dataclass
class Deployments:
    """Stands in for the two deployments: an `httpx2` transport handler.

    Answers each call from a script of statuses (200 once it runs out).
    """

    statuses: list[int] = field(default_factory=list)
    content: Any = field(default_factory=context_answer)
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = None
    dimensions: int | None = None
    requests: list[httpx2.Request] = field(default_factory=list)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        status = self.statuses.pop(0) if self.statuses else 200
        if status != 200:
            return httpx2.Response(
                status,
                headers=self.headers,
                json={"error": {"code": "x", "message": "SECRET-ENDPOINT-MESSAGE"}},
            )
        if self.body is not None:
            return httpx2.Response(200, json=self.body)
        if request.url.path.endswith("/embeddings"):
            texts = json.loads(request.content)["input"]
            return httpx2.Response(200, json=embedding_answer(texts, self.dimensions))
        return httpx2.Response(200, json=completion(self.content))


def gateway_for(
    deployments: Deployments | Any, settings: Settings, **options: Any
) -> tuple[ModelGateway, list[float]]:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    handler = (
        deployments.handle if isinstance(deployments, Deployments) else deployments
    )
    gateway = ModelGateway(
        build_model_client(
            settings, httpx2.MockTransport(handler), options.pop("token", None)
        ),
        chat_deployment=chat_deployment(settings),
        embedding_deployment=embedding_deployment(settings),
        sleep=sleep,
        # No random part in a wait, unless a test asks for one.
        **{"jitter": lambda: 1.0, **options},
    )
    return gateway, waits


def on(gateway: ModelGateway, call: Any) -> Any:
    async def scenario() -> Any:
        try:
            return await call(gateway)
        finally:
            await gateway.aclose()

    return asyncio.run(scenario())


def context_line(gateway: ModelGateway) -> str:
    return str(on(gateway, lambda g: g.context_line(RULE_PLACE)))


def test_story_2_2_a_context_line_is_one_chat_completion_on_the_shared_deployment(
    settings: Settings,
) -> None:
    deployments = Deployments()
    gateway, waits = gateway_for(deployments, settings)

    answer = context_line(gateway)

    # The model's answer as it gave it: the domain parses it.
    assert answer == context_answer()
    (request,) = deployments.requests
    assert str(request.url) == "http://127.0.0.1:5101/openai/v1/chat/completions"
    body = json.loads(request.content)
    assert body["model"] == CHAT
    system, user = body["messages"]
    # The prompt is the instruction; the rule is data, in the user turn.
    assert (system["role"], system["content"]) == ("system", load_prompt(CHUNK_CONTEXT))
    assert (user["role"], user["content"]) == ("user", RULE_PLACE)
    assert body["response_format"]["json_schema"] == {
        "name": "chunk_context",
        "strict": True,
        "schema": CONTEXT_SCHEMA,
    }
    assert body["max_completion_tokens"] == 2000
    assert waits == []


def test_story_2_2_vectors_come_from_the_one_embedding_deployment_in_the_order_asked(
    settings: Settings,
) -> None:
    deployments = Deployments()
    gateway, _ = gateway_for(deployments, settings)
    texts = ["first SECRET text", "the second one", "third"]

    vectors = on(gateway, lambda g: g.embed(texts))

    (request,) = deployments.requests
    assert str(request.url) == "http://127.0.0.1:5101/openai/v1/embeddings"
    body = json.loads(request.content)
    assert (body["model"], body["input"]) == (EMBEDDING, texts)
    assert body["encoding_format"] == "float"
    # The answer listed them in another order; each text has its own vector.
    assert vectors == [vector_for(text) for text in texts]
    assert all(len(vector) == EMBEDDING_DIMENSIONS for vector in vectors)


def test_story_2_2_the_gateway_does_not_judge_what_comes_back(
    settings: Settings,
) -> None:
    prose, _ = gateway_for(Deployments(content="not what was asked for"), settings)
    nothing, _ = gateway_for(Deployments(content=None), settings)
    short, _ = gateway_for(Deployments(dimensions=8), settings)

    assert context_line(prose) == "not what was asked for"
    # A refusal or a filtered answer has no text: empty, for the domain to refuse.
    assert context_line(nothing) == ""
    # A vector of the wrong size is passed on as it is, and refused there.
    assert [len(vector) for vector in on(short, lambda g: g.embed(["a"]))] == [8]


@pytest.mark.parametrize(
    ("indexes", "reason"),
    [
        # One index twice, one missing, one out of range, one that is no number.
        ([0, 0, 1], "embedding_index_invalid"),
        ([0, 1], "embedding_index_invalid"),
        ([0, 1, 3], "embedding_index_invalid"),
        ([0, 1, 2, 3], "embedding_index_invalid"),
        ([0, 1, "2"], "embedding_index_invalid"),
        ([0, 1, None], "embedding_index_invalid"),
        (None, "embedding_answer_malformed"),
    ],
)
def test_story_2_2_an_embedding_answer_that_cannot_be_matched_to_its_texts_is_refused(
    settings: Settings, indexes: list[Any] | None, reason: str
) -> None:
    texts = ["first", "second", "third"]
    body: dict[str, Any] = {"object": "list", "model": EMBEDDING}
    if indexes is not None:
        body["data"] = [
            {"object": "embedding", "index": index, "embedding": vector_for("x")}
            for index in indexes
        ]
    gateway, _ = gateway_for(Deployments(body=body), settings)

    # A vector paired with the wrong chunk would never be noticed afterwards.
    with pytest.raises(ModelAnswerInvalid) as raised:
        on(gateway, lambda g: g.embed(texts))

    assert raised.value.reason == reason


@pytest.mark.parametrize("status", [429, 500, 503, 408, 409])
def test_story_2_2_a_call_answered_not_now_is_retried_honouring_retry_after(
    settings: Settings, status: int
) -> None:
    deployments = Deployments(statuses=[status, status], headers={"retry-after": "2"})
    gateway, waits = gateway_for(deployments, settings)

    assert on(gateway, lambda g: g.embed(["a"])) == [vector_for("a")]

    assert len(deployments.requests) == 3
    assert waits == [2.0, 2.0]


def test_story_2_2_after_three_retries_the_model_is_unavailable(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    deployments = Deployments(statuses=[429] * 10)
    gateway, waits = gateway_for(deployments, settings, retry_seconds=1.0)

    with caplog.at_level(logging.WARNING), pytest.raises(ModelUnavailable):
        context_line(gateway)

    # AD-16: the first call and three more, with waits that grow.
    assert len(deployments.requests) == 4
    assert waits == [1.0, 2.0, 4.0]
    assert (
        f"model unavailable: operation=context_line deployment={CHAT} attempts=4"
        in (caplog.text)
    )
    assert "SECRET" not in caplog.text


def test_story_2_2_the_waits_keep_to_a_limit_and_have_a_random_part(
    settings: Settings,
) -> None:
    asked, _ = gateway_for(Deployments(), settings, max_retry_seconds=5.0)
    random_part, _ = gateway_for(
        Deployments(), settings, retry_seconds=2.0, jitter=lambda: 0.0
    )

    assert asked._wait_before(1, 60.0) == 5.0
    assert asked._wait_before(9, None) == 5.0
    # Up to half of the wait is taken off at random.
    assert random_part._wait_before(1, None) == 1.0
    assert random_part._wait_before(2, None) == 2.0
    asyncio.run(asked.aclose())
    asyncio.run(random_part.aclose())


def test_story_2_2_a_call_that_gets_no_answer_is_retried_too(
    settings: Settings,
) -> None:
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        if len(calls) < 3:
            raise httpx2.ConnectError("SECRET-ADDRESS refused")
        return httpx2.Response(200, json=completion(context_answer()))

    gateway, waits = gateway_for(handler, settings)

    assert context_line(gateway) == context_answer()
    assert len(calls) == 3 and len(waits) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_story_2_2_a_call_the_model_refuses_is_not_retried(
    settings: Settings, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    deployments = Deployments(statuses=[status])
    gateway, waits = gateway_for(deployments, settings)

    with caplog.at_level(logging.ERROR), pytest.raises(ModelCallFailed) as raised:
        on(gateway, lambda g: g.embed(["a"]))

    assert raised.value.reason == f"model_status_{status}"
    assert len(deployments.requests) == 1 and waits == []
    assert f"model call refused: deployment={EMBEDDING} status={status}" in caplog.text
    assert "SECRET" not in caplog.text


def test_story_2_2_no_more_calls_than_the_setting_are_under_way_in_the_whole_process(
    settings: Settings,
) -> None:
    under_way = 0
    most = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal under_way, most
        under_way += 1
        most = max(most, under_way)
        await asyncio.sleep(0.01)
        under_way -= 1
        if request.url.path.endswith("/embeddings"):
            return httpx2.Response(200, json=embedding_answer(["a"]))
        return httpx2.Response(200, json=completion(context_answer()))

    gateway, _ = gateway_for(handler, settings, max_concurrent_calls=2)

    async def many(g: ModelGateway) -> None:
        # Chat calls and embedding calls share the one limit.
        await asyncio.gather(
            *(g.context_line(RULE_PLACE) for _ in range(5)),
            *(g.embed(["a"]) for _ in range(5)),
        )

    on(gateway, many)

    assert most == 2


class FakeCredential:
    """Stands in for the Azure identity library's credential."""

    def __init__(self, lifetime_seconds: float = 3600.0) -> None:
        self.scopes: list[str] = []
        self.lifetime_seconds = lifetime_seconds

    def get_token(self, *scopes: str, **options: Any) -> Any:
        self.scopes.extend(scopes)

        @dataclass
        class Token:
            token: str
            expires_on: float

        return Token(
            f"entra-token-{len(self.scopes)}", time.time() + self.lifetime_seconds
        )


class NoTokenCredential:
    def get_token(self, *scopes: str, **options: Any) -> Any:
        raise RuntimeError("SECRET-TENANT could not be reached")


def azure_settings() -> Settings:
    return Settings(
        model_endpoint=FOUNDRY,
        model_entra_auth=True,
        layout_endpoint=DOCUMENT_INTELLIGENCE,
        layout_entra_auth=True,
        chat_deployment=CHAT,
        embedding_deployment=EMBEDDING,
        azure_client_id="client-id",
    )


def test_story_2_2_in_azure_the_models_are_signed_in_to_with_an_entra_token_never_a_key(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    credential = FakeCredential()
    monkeypatch.setattr(
        "retrieval.adapters.model.azure_credential", lambda settings: credential
    )
    azure = azure_settings()
    deployments = Deployments()
    token = model_token_for(azure)
    assert token is not None
    gateway, _ = gateway_for(deployments, azure, token=token)

    async def both(g: ModelGateway) -> None:
        await g.context_line(RULE_PLACE)
        await g.embed(["a"])

    on(gateway, both)

    first, second = deployments.requests
    assert str(first.url) == f"{FOUNDRY}/openai/v1/chat/completions"
    assert str(second.url) == f"{FOUNDRY}/openai/v1/embeddings"
    # The token of the service identity, fetched once and kept.
    assert first.headers["authorization"] == "Bearer entra-token-1"
    assert second.headers["authorization"] == "Bearer entra-token-1"
    assert credential.scopes == [COGNITIVE_SERVICES_SCOPE]
    assert "api-key" not in first.headers
    # The local stand-in is sent a fixed word in the place of a key.
    local = Deployments()
    stand_in, _ = gateway_for(local, settings)
    context_line(stand_in)
    assert local.requests[0].headers["authorization"] == f"Bearer {NO_KEY}"


def test_story_2_2_a_token_that_cannot_be_fetched_is_retried_then_model_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    deployments = Deployments()
    token = EntraToken(NoTokenCredential(), scope=COGNITIVE_SERVICES_SCOPE)
    gateway, waits = gateway_for(deployments, azure_settings(), token=token)

    with caplog.at_level(logging.WARNING), pytest.raises(ModelUnavailable):
        context_line(gateway)

    # No token, so no call was made; the type of the failure only.
    assert deployments.requests == []
    assert len(waits) == 3
    assert "code=token_RuntimeError" in caplog.text
    assert "SECRET" not in caplog.text


def test_story_2_2_the_client_never_follows_a_redirect_or_retries_by_itself(
    settings: Settings,
) -> None:
    client = build_model_client(settings)

    assert client.max_retries == 0
    assert client._client.follow_redirects is False
    assert client._client.trust_env is False
    asyncio.run(client.close())


def test_story_2_2_every_model_call_has_a_span_and_a_log_line_of_counts(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("retrieval.adapters.model.tracer", provider.get_tracer("test"))
    deployments = Deployments(statuses=[429], headers={"retry-after": "7"})
    gateway, _ = gateway_for(deployments, settings)

    async def both(g: ModelGateway) -> None:
        await g.context_line(RULE_PLACE)
        await g.embed(["a SECRET text"])

    with caplog.at_level(logging.DEBUG):
        on(gateway, both)

    spans = exporter.get_finished_spans()
    attempts = [span for span in spans if span.name == "retrieval.model.attempt"]
    (line,) = [span for span in spans if span.name == "retrieval.model.context_line"]
    (embed,) = [span for span in spans if span.name == "retrieval.model.embed"]
    # One span per HTTP call, each below the span of its call.
    assert [span.parent.span_id for span in attempts if span.parent] == [
        line.context.span_id,
        line.context.span_id,
        embed.context.span_id,
    ]
    assert dict(attempts[0].attributes or {}) == {
        "retrieval.model.attempt": 1,
        "http.response.status_code": 429,
        "error.type": "status_429",
        "retrieval.model.wait_seconds": 7.0,
    }
    # The call's span carries the deployment, the attempts and the token counts.
    assert dict(line.attributes or {}) == {
        "gen_ai.request.model": CHAT,
        "retrieval.model.attempts": 2,
        "gen_ai.usage.input_tokens": 1,
        "gen_ai.usage.output_tokens": 1,
    }
    assert dict(embed.attributes or {})["gen_ai.usage.input_tokens"] == 3
    assert (
        f"model call: operation=embed deployment={EMBEDDING} attempts=1 "
        "input_tokens=3 output_tokens=0"
    ) in caplog.text
    # Nothing of the rule, the answer or the endpoint's message, anywhere.
    assert "SECRET" not in caplog.text
    for span in spans:
        assert "SECRET" not in repr(dict(span.attributes or {}))
        for event in span.events:
            assert "SECRET" not in repr(dict(event.attributes or {}))


def test_story_2_2_an_error_inside_an_adapters_span_leaves_its_type_and_no_message() -> (
    None
):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # As a database error would: its message holds the statement and its values.
    with (
        pytest.raises(RuntimeError),
        adapter_span(provider.get_tracer("test"), "retrieval.db.apply_chunks"),
    ):
        raise RuntimeError("INSERT INTO chunk VALUES ('SECRET-VALUE')")

    (span,) = exporter.get_finished_spans()
    (event,) = span.events
    assert span.status.status_code is trace.StatusCode.ERROR
    assert span.status.description is None
    assert set(event.attributes or {}) == {"exception.type", "exception.stacktrace"}
    assert "SECRET" not in repr(dict(event.attributes or {}))
    # The engine's own errors never carry the values of a statement either.
    assert build_database(Settings()).engine.sync_engine.hide_parameters is True
    # No adapter opens a span any other way, and neither does the job.
    for source in (SERVICE_DIR / "src" / "retrieval").rglob("*.py"):
        if source.name != "telemetry.py":
            assert "start_as_current_span" not in source.read_text(), source.name


# --- Document Intelligence ---------------------------------------------------------------


@dataclass
class LayoutService:
    """Stands in for Document Intelligence: an `httpx2` transport handler."""

    # What each submit and each look is answered with, in order; the last again.
    submits: list[Any] = field(default_factory=lambda: [202])
    looks: list[Any] = field(default_factory=lambda: ["succeeded"])
    result: Any = field(default_factory=analyze_result)
    location: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    # Sent with every look that is answered 200 or refused for now.
    look_headers: dict[str, str] = field(default_factory=dict)
    requests: list[httpx2.Request] = field(default_factory=list)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.method == "POST":
            given = self._next(self.submits)
            if isinstance(given, Exception):
                raise given
            if given != 202:
                return httpx2.Response(given, headers=self.headers, json={})
            location = self.location
            if location is None:
                # Another host than the endpoint: only the id may be taken from it.
                location = (
                    "https://elsewhere.example.com/documentintelligence/documentModels/"
                    "prebuilt-layout/analyzeResults/result-1?api-version=2024-11-30"
                )
            return httpx2.Response(202, headers={"operation-location": location})
        given = self._next(self.looks)
        if isinstance(given, Exception):
            raise given
        if isinstance(given, int):
            return httpx2.Response(given, headers=self.look_headers, json={})
        if given == "not-json":
            return httpx2.Response(200, content=b"SECRET not json")
        if given == "not-an-object":
            return httpx2.Response(200, json=["SECRET"])
        body: dict[str, Any] = {"status": given}
        if given == "succeeded":
            body["analyzeResult"] = self.result
        if given == "failed":
            body["error"] = {
                "code": "Invalid-Content Length!",
                "message": "SECRET-SERVICE-MESSAGE",
            }
        return httpx2.Response(200, headers=self.look_headers, json=body)

    @staticmethod
    def _next(script: list[Any]) -> Any:
        return script.pop(0) if len(script) > 1 else script[0]


def layout_for(
    service: LayoutService, settings: Settings, **options: Any
) -> tuple[DocumentLayout, list[float]]:
    waits: list[float] = []
    now = [0.0]

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        # The clock moves only when the adapter waits: no test sleeps.
        now[0] += seconds

    return (
        DocumentLayout(
            build_layout_http(settings, httpx2.MockTransport(service.handle)),
            api_version=settings.layout_api_version,
            model=settings.layout_model,
            poll_seconds=options.pop("poll_seconds", 2.0),
            deadline_seconds=options.pop("deadline_seconds", 600.0),
            max_retries=settings.layout_max_retries,
            sleep=sleep,
            clock=lambda: now[0],
            **options,
        ),
        waits,
    )


def parse(layout: DocumentLayout) -> Any:
    async def scenario() -> Any:
        try:
            return await layout.parse(PDF)
        finally:
            await layout.aclose()

    return asyncio.run(scenario())


def test_story_2_2_the_manual_is_parsed_by_the_layout_model_over_rest(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    service = LayoutService(looks=["notStarted", "running", "succeeded"])
    layout, waits = layout_for(service, settings)

    with caplog.at_level(logging.DEBUG):
        parsed = parse(layout)

    assert parsed == manual()
    submit, *looks = service.requests
    assert (submit.method, submit.url.path) == (
        "POST",
        "/documentintelligence/documentModels/prebuilt-layout:analyze",
    )
    assert submit.url.params["api-version"] == "2024-11-30"
    # The manual's bytes are sent; the service is given no address to read.
    assert json.loads(submit.content) == {
        "base64Source": base64.b64encode(PDF).decode()
    }
    # Every look goes to the configured endpoint: only the id is taken from
    # the address the service named.
    result_address = (
        "http://127.0.0.1:5102/documentintelligence/documentModels/prebuilt-layout"
        "/analyzeResults/result-1?api-version=2024-11-30"
    )
    assert {str(look.url) for look in looks} == {result_address}
    assert len(looks) == 3
    assert waits == [2.0, 2.0]
    assert "authorization" not in submit.headers
    assert "layout parsed: result_id=result-1 pages=4" in caplog.text
    assert "SECRET" not in caplog.text


def test_story_2_2_a_submit_that_is_not_answered_is_sent_again(
    settings: Settings,
) -> None:
    service = LayoutService(
        submits=[429, httpx2.ConnectError("SECRET-ADDRESS"), 503, 202],
        headers={"retry-after": "9"},
    )
    layout, waits = layout_for(service, settings)

    assert parse(layout) == manual()
    # What `Retry-After` asks for, or else the poll's wait.
    assert waits == [9.0, 2.0, 9.0]


@pytest.mark.parametrize(
    ("service", "reason"),
    [
        # Document Intelligence answers an error.
        (LayoutService(submits=[400]), "layout_submit_status_400"),
        (LayoutService(submits=[401]), "layout_submit_status_401"),
        (LayoutService(submits=[500]), "layout_submit_status_500"),
        (
            LayoutService(submits=[httpx2.ReadTimeout("SECRET")]),
            "layout_submit_ReadTimeout",
        ),
        (LayoutService(submits=[200]), "layout_submit_status_200"),
        (LayoutService(location=""), "layout_submit_no_result_id"),
        (
            LayoutService(location="https://x.example.com/analyzeResults/a b"),
            "layout_submit_no_result_id",
        ),
        # A failed analysis, with the service's own code as an identifier.
        (
            LayoutService(looks=["running", "failed"]),
            "layout_failed_InvalidContentLength",
        ),
        # Looks that are never answered do not go on until the deadline.
        (LayoutService(looks=[503]), "layout_poll_status_503"),
        (
            LayoutService(looks=[httpx2.ConnectError("SECRET-ADDRESS")]),
            "layout_poll_ConnectError",
        ),
        (LayoutService(looks=["canceled"]), "layout_canceled"),
        (LayoutService(looks=[404]), "layout_result_status_404"),
        (LayoutService(looks=["not-json"]), "layout_result_not_json"),
        (LayoutService(looks=["not-an-object"]), "layout_result_not_an_object"),
        (LayoutService(result=None), "layout_result_missing"),
        (LayoutService(result={"paragraphs": []}), "layout_result_malformed"),
        (
            LayoutService(
                result={"pages": [{"pageNumber": 1}], "paragraphs": [{"content": "x"}]}
            ),
            "layout_result_malformed",
        ),
        (
            LayoutService(
                result={
                    "pages": [{"pageNumber": 1}],
                    "paragraphs": [
                        {"content": 7, "boundingRegions": [{"pageNumber": 1}]}
                    ],
                }
            ),
            "layout_result_malformed",
        ),
    ],
)
def test_story_2_2_a_layout_error_ends_the_parse_with_a_code(
    settings: Settings,
    service: LayoutService,
    reason: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    layout, _ = layout_for(service, settings)

    with caplog.at_level(logging.DEBUG), pytest.raises(LayoutFailed) as raised:
        parse(layout)

    assert raised.value.reason == reason
    # A code, never the service's message or the document.
    assert "SECRET" not in caplog.text
    assert "SECRET" not in str(raised.value)


def test_story_2_2_looks_that_get_no_answer_are_logged_and_bounded(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    # Two unanswered looks, an answer, then nothing but 503s.
    service = LayoutService(
        looks=[
            503,
            httpx2.ReadTimeout("SECRET"),
            "running",
            503,
            503,
            503,
            503,
            503,
            503,
        ]
    )
    layout, waits = layout_for(service, settings)

    with caplog.at_level(logging.WARNING), pytest.raises(LayoutFailed) as raised:
        parse(layout)

    # Five in a row end the parse, long before the deadline of ten minutes;
    # an answer in between starts the count again.
    assert raised.value.reason == "layout_poll_status_503"
    assert len(service.requests) == 1 + 3 + 5
    assert sum(waits) < 600
    # Each one is in the log, with the status or the error's type.
    assert (
        "layout poll not answered: result_id=result-1 attempt=1 code=status_503 "
        "in_a_row=1"
    ) in caplog.text
    assert "attempt=2 code=ReadTimeout in_a_row=2" in caplog.text
    assert "attempt=8 code=status_503 in_a_row=5" in caplog.text
    assert caplog.text.count("layout poll not answered") == 7
    assert "SECRET" not in caplog.text


def test_story_2_2_retry_after_is_honoured_on_a_running_analysis_within_a_limit(
    settings: Settings,
) -> None:
    service = LayoutService(
        looks=["running", "running", "succeeded"], look_headers={"retry-after": "7"}
    )
    layout, waits = layout_for(service, settings)
    eager = LayoutService(
        submits=[429, 202], headers={"retry-after": "86400"}, looks=["succeeded"]
    )
    capped, capped_waits = layout_for(eager, settings)

    assert parse(layout) == manual()
    assert parse(capped) == manual()

    # What the service asks for, in place of the setting's two seconds.
    assert waits == [7.0, 7.0]
    # A wait of a day is cut to the longest wait there is.
    assert capped_waits == [30.0]


def test_story_2_2_the_submit_and_its_waits_stay_inside_the_parse_deadline(
    settings: Settings,
) -> None:
    service = LayoutService(submits=[429], headers={"retry-after": "9"})
    layout, waits = layout_for(service, settings, deadline_seconds=20.0)

    with pytest.raises(LayoutFailed) as raised:
        parse(layout)

    # Two waits of nine seconds fit in twenty; a third would end after it.
    assert raised.value.reason == "layout_timeout"
    assert waits == [9.0, 9.0]
    assert len(service.requests) == 3


def test_story_2_2_an_analysis_that_does_not_end_times_out(
    settings: Settings,
) -> None:
    # Never done, and now and then not answered at all.
    service = LayoutService(looks=["running", 503, httpx2.ConnectError("x"), "running"])
    layout, waits = layout_for(service, settings, deadline_seconds=20.0)

    with pytest.raises(LayoutFailed) as raised:
        parse(layout)

    assert raised.value.reason == "layout_timeout"
    assert sum(waits) == 20.0


def test_story_2_2_in_azure_document_intelligence_is_signed_in_to_with_the_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = FakeCredential()
    monkeypatch.setattr(
        "retrieval.adapters.layout.azure_credential", lambda settings: credential
    )
    azure = azure_settings()
    service = LayoutService()
    layout, _ = layout_for(service, azure, token=layout_token_for(azure))

    parse(layout)

    assert [
        str(request.url).split("/documentintelligence")[0]
        for request in (service.requests)
    ] == [DOCUMENT_INTELLIGENCE] * 2
    assert [request.headers["authorization"] for request in service.requests] == [
        "Bearer entra-token-1"
    ] * 2
    assert credential.scopes == [COGNITIVE_SERVICES_SCOPE]
    # A token that cannot be had is a layout failure, by its type only.
    broken, _ = layout_for(
        LayoutService(),
        azure,
        token=EntraToken(NoTokenCredential(), scope=COGNITIVE_SERVICES_SCOPE),
    )
    with pytest.raises(LayoutFailed) as raised:
        parse(broken)
    assert raised.value.reason == "layout_token_RuntimeError"
    # Never a redirect: the token must not leave the endpoint.
    http = build_layout_http(azure)
    assert http.follow_redirects is False and http.trust_env is False
    asyncio.run(http.aclose())


def test_story_2_2_a_layout_result_is_read_into_pages_and_paragraphs() -> None:
    parsed = layout_of(
        {
            "pages": [
                {"pageNumber": 1, "words": [{"content": "a"}]},
                {"pageNumber": 2, "lines": [{"content": "b"}]},
                {"pageNumber": 3, "words": [], "lines": []},
            ],
            "paragraphs": [
                {
                    "content": "A header",
                    "role": "pageHeader",
                    "boundingRegions": [{"pageNumber": 1}],
                },
                {
                    # A paragraph over a page break is where it starts.
                    "content": "Body",
                    "role": "",
                    "boundingRegions": [{"pageNumber": 2}, {"pageNumber": 3}],
                },
            ],
        }
    )

    assert [(page.page_number, page.has_text) for page in parsed.pages] == [
        (1, True),
        (2, True),
        (3, False),
    ]
    assert [(p.page_number, p.text, p.role) for p in parsed.paragraphs] == [
        (1, "A header", "pageHeader"),
        (2, "Body", None),
    ]


# --- The manual's blob ---------------------------------------------------------------------


@dataclass
class FakeBlobService:
    content: bytes | None = PDF
    asked: list[tuple[str, str]] = field(default_factory=list)

    def get_blob_client(self, container: str, blob: str) -> Any:
        self.asked.append((container, blob))
        return self

    def download_blob(self) -> Any:
        if self.content is None:
            raise ResourceNotFoundError("SECRET-ACCOUNT has no such blob")
        return self

    def readall(self) -> bytes:
        assert self.content is not None
        return self.content


def test_story_2_2_the_manual_is_read_from_its_container() -> None:
    service = FakeBlobService()
    store = BlobManualStore(service, "manual", "underwriting-manual.pdf")  # type: ignore[arg-type]  # a stand-in for the storage client

    assert asyncio.run(store.read()) == PDF
    assert service.asked == [("manual", "underwriting-manual.pdf")]


def test_story_2_2_no_blob_of_the_configured_name_is_a_missing_manual() -> None:
    store = BlobManualStore(FakeBlobService(content=None), "manual", "gone.pdf")  # type: ignore[arg-type]  # a stand-in for the storage client

    with pytest.raises(ManualMissing) as raised:
        asyncio.run(store.read())

    assert "SECRET" not in str(raised.value)


def test_story_2_2_the_storage_client_is_the_identitys_in_azure_and_the_emulators_locally(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    credential = FakeCredential()
    monkeypatch.setattr(
        "retrieval.adapters.blob.azure_credential", lambda settings: credential
    )
    azure = build_blob_service(
        Settings(blob_account_url="https://staiuwdemowus3.blob.core.windows.net")
    )
    local = build_blob_service(
        Settings(blob_connection_string=SecretStr("UseDevelopmentStorage=true"))
    )

    assert azure.credential is credential
    assert azure.account_name == "staiuwdemowus3"
    assert local.account_name == "devstoreaccount1"
    # The manual is uploaded by this code only into the emulator.
    with pytest.raises(ValueError, match="only to the local emulator"):
        upload_local_manual(
            Settings(blob_account_url="https://staiuwdemowus3.blob.core.windows.net"),
            tmp_path / "manual.pdf",
        )


# --- The Entra token and the database's address ----------------------------------------------


def test_story_2_2_the_database_url_holds_no_password_and_requires_tls_in_azure() -> (
    None
):
    local = database_url(Settings())
    azure = database_url(
        Settings(
            database_host="pgsql-aiuw-demo-wus3.postgres.database.azure.com",
            database_user="id-aiuw-demo-wus3-retrieval",
            database_entra_auth=True,
        )
    )

    assert local.password is None and azure.password is None
    assert "sslmode" not in local.query
    assert azure.query["sslmode"] == "require"
    assert azure.username == "id-aiuw-demo-wus3-retrieval"


def test_story_2_2_database_connections_sign_in_with_an_entra_token() -> None:
    credential = FakeCredential()
    token = EntraToken(credential)
    engine = create_engine("postgresql+psycopg://user@127.0.0.1:1/none")
    use_entra_token(engine, token)
    parameters: dict[str, Any] = {}

    engine.dialect.dispatch.do_connect(engine.dialect, None, (), parameters)

    # The token is the password; none is ever stored.
    assert list(parameters.values()) == ["entra-token-1"]
    assert credential.scopes == [POSTGRESQL_TOKEN_SCOPE]


def test_story_2_2_the_token_is_fetched_off_the_event_loop_kept_and_replaced_in_time() -> (
    None
):
    credential = FakeCredential()
    now = [1000.0]
    token = EntraToken(credential, clock=lambda: now[0])

    async def scenario() -> list[str]:
        values = []
        await token.refresh()
        values.append(token.value())
        # Still fresh: nothing is fetched.
        await token.refresh()
        values.append(token.value())
        # Within five minutes of its end: replaced before it is used.
        now[0] = time.time() + credential.lifetime_seconds - 200
        await token.refresh()
        values.append(token.value())
        return values

    assert asyncio.run(scenario()) == [
        "entra-token-1",
        "entra-token-1",
        "entra-token-2",
    ]
    # A caller without an event loop (migrations) fetches on the spot.
    assert EntraToken(FakeCredential()).value() == "entra-token-1"


def test_story_2_2_the_service_refreshes_the_token_before_it_connects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = FakeCredential()
    monkeypatch.setattr(
        "retrieval.adapters.db.azure_credential", lambda settings: credential
    )
    # A port nothing listens on: the connection fails, after the token was fetched.
    settings = Settings(
        database_entra_auth=True,
        database_port=1,
        database_connect_timeout_seconds=1,
    )
    database = build_database(settings)

    async def scenario() -> None:
        try:
            with pytest.raises(Exception):  # noqa: B017 - whatever the driver raises for a closed port
                async with database.connect():
                    pass
            with pytest.raises(Exception):  # noqa: B017 - as above
                async with database.begin():
                    pass
        finally:
            await database.dispose()

    asyncio.run(scenario())

    assert credential.scopes == [POSTGRESQL_TOKEN_SCOPE]
    # Without Entra sign-in no token source is built at all.
    assert build_database(Settings())._token is None


def test_story_2_2_azure_calls_sign_in_with_the_service_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[tuple[str, Any]] = []

    class Managed:
        def __init__(self, client_id: str) -> None:
            built.append(("managed", client_id))

    class Default:
        def __init__(self) -> None:
            built.append(("default", None))

    import azure.identity

    monkeypatch.setattr(azure.identity, "ManagedIdentityCredential", Managed)
    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", Default)
    credential_module._credential.cache_clear()
    try:
        in_azure = credential_module.azure_credential(
            Settings(azure_client_id="client-id")
        )
        again = credential_module.azure_credential(
            Settings(azure_client_id="client-id")
        )
        credential_module.azure_credential(Settings())
    finally:
        credential_module._credential.cache_clear()

    # One credential per process, so its token cache is shared.
    assert in_azure is again
    assert built == [("managed", "client-id"), ("default", None)]


# --- The chunk table and the migrations ------------------------------------------------------


def test_story_2_2_the_bundled_head_is_the_first_migration_in_the_package() -> None:
    versions = sorted(path.name for path in (MIGRATIONS_DIR / "versions").glob("v*.py"))

    assert versions == ["v0001_chunk.py"]
    assert bundled_head() == "0001"
    # Inside the package, so the image carries it.
    assert MIGRATIONS_DIR == SERVICE_DIR / "src" / "retrieval" / "migrations"
    ini = (SERVICE_DIR / "alembic.ini").read_text()
    assert "script_location = %(here)s/src/retrieval/migrations" in ini


def test_story_2_2_migrations_describe_only_the_services_own_schema(
    settings: Settings,
) -> None:
    assert include_name("retrieval", "schema", {}) is True
    assert include_name("classification", "schema", {}) is False
    assert include_name("retrieval", "table", {}) is True
    assert alembic_config(settings).attributes["settings"] is settings
    # Two tables, in the service's schema, with no foreign key into another's:
    # the chunks, and the note of what each chunk set was last built from.
    assert sorted(metadata.tables) == ["retrieval.chunk", "retrieval.ingest_run"]
    assert metadata.tables["retrieval.chunk"] is chunk_table
    for table in metadata.tables.values():
        assert table.schema == "retrieval"
        assert table.foreign_keys == set()
    assert {"manual_sha256", "prompt_digest", "chat_deployment"} <= set(
        metadata.tables["retrieval.ingest_run"].columns.keys()
    )


def test_story_2_2_the_chunk_record_can_fill_every_field_of_a_search_item() -> None:
    columns = set(chunk_table.columns.keys())

    # The contracts' search item and rule text (story 2.3), and what Epic 3
    # loads into another store: one record is the source for all of them.
    assert {
        "chunk_id",
        "chunk_set",
        "rule_ids",
        "reference_rule_ids",
        "text",
        "context_line",
        "section_id",
        "impairment",
        "manual_page",
        "embedding",
        "content_hash",
    } <= columns
    vector = chunk_table.c.embedding.type
    assert isinstance(vector, VECTOR)
    assert vector.dim == EMBEDDING_DIMENSIONS == 3072
    # AD-12: search is exact. The vector column has no index at all.
    for index in chunk_table.indexes:
        assert "embedding" not in [column.name for column in index.columns]


def test_story_2_2_the_full_text_column_keeps_a_rule_id_whole() -> None:
    expression = full_text_of("text")

    assert TEXT_SEARCH_CONFIG == "english"
    assert expression == (
        "to_tsvector('english'::regconfig, regexp_replace(text, "
        r"'\mUW-([A-Z]{2,4})-([0-9]{3})\M', 'UW\1\2', 'g'))"
    )
    # The same pattern as the contracts', with its parts as groups.
    assert RULE_ID_AS_WORDS.replace("(", "").replace(")", "") == (
        rf"\m{RULE_ID_PATTERN}\M"
    )
    # The migration writes the same expression out: a migration is not
    # changed when the code is.
    migration = (MIGRATIONS_DIR / "versions" / "v0001_chunk.py").read_text()
    assert r"regexp_replace(text, '\mUW-([A-Z]{2,4})-([0-9]{3})\M', 'UW\1\2', 'g')" in (
        migration
    )
    assert "to_tsvector('english'::regconfig, " in migration


# --- Start-up and telemetry ------------------------------------------------------------------


def test_story_2_2_the_server_starts_on_the_configured_host_and_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: dict[str, Any] = {}
    monkeypatch.setenv("RETRIEVAL_HOST", "0.0.0.0")  # noqa: S104 - what the container image sets
    monkeypatch.setenv("RETRIEVAL_PORT", "9104")
    monkeypatch.setattr(
        "uvicorn.run", lambda app, **options: started.update(app=app, **options)
    )
    get_settings.cache_clear()
    try:
        runpy.run_module("retrieval", run_name="__main__")
    finally:
        get_settings.cache_clear()

    assert started == {
        "app": "retrieval.adapters.http.app:create_app",
        "factory": True,
        "host": "0.0.0.0",  # noqa: S104 - as above
        "port": 9104,
        "server_header": False,
    }


def test_story_2_2_the_service_starts_without_the_models_or_the_manual() -> None:
    # Only the job needs them: the service has its probes and its database.
    app = create_app(Settings())

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert isinstance(app, FastAPI)


def test_story_2_2_logging_is_configured_at_start_up_and_libraries_are_kept_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured: dict[str, Any] = {}
    monkeypatch.setattr(
        logging, "basicConfig", lambda **options: configured.update(options)
    )

    configure_logging()

    assert configured["level"] == logging.INFO
    # The clients and their HTTP library never log a call themselves.
    assert {"openai", "httpx2", "azure"} <= set(QUIET_LOGGERS)
    for name in QUIET_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING


class FakeExporterSetup:
    def __init__(self) -> None:
        self.options: dict[str, Any] | None = None

    def __call__(self, **options: Any) -> None:
        self.options = options


def test_story_2_2_telemetry_is_reported_as_retrieval_with_the_service_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = FakeExporterSetup()
    assert configure_telemetry(Settings(), exporter_setup=exporter) is False
    assert exporter.options is None
    credential = FakeCredential()
    monkeypatch.setattr(
        "retrieval.adapters.telemetry.azure_credential", lambda settings: credential
    )
    settings = Settings(
        applicationinsights_connection_string=SecretStr("InstrumentationKey=k"),
        otel_sampling_ratio=0.25,
    )

    assert configure_telemetry(settings, exporter_setup=exporter) is True

    assert exporter.options is not None
    assert exporter.options["credential"] is credential
    assert exporter.options["sampling_ratio"] == 0.25
    assert exporter.options["resource"].attributes["service.name"] == "retrieval"
    assert exporter.options["instrumentation_options"] == {
        "fastapi": {"enabled": False}
    }


def test_story_2_2_requests_are_traced_and_the_probes_are_not(
    settings: Settings, dependencies: Dependencies
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_app(settings, dependencies=dependencies)
    instrument_app(app, tracer_provider=provider)

    with TestClient(app) as client:
        client.get("/health")
        client.get("/ready")
        client.get("/nowhere")

    assert EXCLUDED_URLS == "/health$,/ready$"
    servers = [
        span.name
        for span in exporter.get_finished_spans()
        if span.kind is trace.SpanKind.SERVER
    ]
    assert len(servers) == 1 and servers[0].startswith("GET")
    assert current_trace_id(TRACEPARENT) == TRACE_ID
    assert current_trace_id("not-a-traceparent") is None
    assert current_trace_id(None) is None


# --- What the service is made of -------------------------------------------------------------


def test_story_2_2_the_service_shares_no_code_with_another_service() -> None:
    pyproject = (SERVICE_DIR / "pyproject.toml").read_text()

    # Contracts is the only shared code (AD-3); the spine's pins.
    assert '"openai==3.24.0"' in pyproject
    assert '"pgvector==0.5.0"' in pyproject
    assert re.findall(r"^(\w+) = \{ workspace = true \}", pyproject, re.MULTILINE) == [
        "contracts"
    ]
    for source in (SERVICE_DIR / "src").rglob("*.py"):
        text = source.read_text()
        assert not re.search(
            r"^\s*(from|import) (intake|workflow|web|classification)\b",
            text,
            re.MULTILINE,
        ), source.name


def test_story_2_2_the_job_calls_no_other_service_of_ours() -> None:
    # No Dapr sidecar, no app id, no service invocation anywhere in the package.
    for source in (SERVICE_DIR / "src").rglob("*.py"):
        text = source.read_text().lower()
        assert "/v1.0/invoke" not in text, source.name
        assert "dapr" not in text.replace("the dapr app id", ""), source.name
