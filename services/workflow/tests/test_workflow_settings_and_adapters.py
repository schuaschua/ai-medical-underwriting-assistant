"""Story 1.6: settings, sign-in, the database connection, start-up and telemetry of `workflow`."""

import asyncio
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

import psycopg
import pytest
import uvicorn
from alembic import command
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from fastapi.testclient import TestClient
from opentelemetry.sdk.resources import SERVICE_NAME
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind, StatusCode, format_trace_id
from pydantic import SecretStr
from sqlalchemy import create_engine, event

import workflow.__main__ as entry
from contracts.enums import ClassifierContender, RetrieverConfig
from contracts.errors import ErrorBody
from workflow import local_setup
from workflow.adapters import db, telemetry
from workflow.adapters.credential import azure_credential
from workflow.adapters.db import (
    POSTGRESQL_TOKEN_SCOPE,
    TOKEN_REFRESH_MARGIN_SECONDS,
    EntraToken,
    build_database,
    database_url,
    use_entra_token,
)
from workflow.adapters.http.app import create_app, default_parameters
from workflow.adapters.http.routes import Dependencies
from workflow.adapters.migrations import MIGRATIONS_DIR, alembic_config, bundled_head
from workflow.adapters.telemetry import (
    configure_logging,
    configure_telemetry,
    current_trace_id,
    instrument_app,
)
from workflow.settings import Settings, get_settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
APP_STACK_MAIN = REPOSITORY_ROOT / "infra" / "demo" / "app" / "main.tf"
CLIENT_ID = "00000000-0000-0000-0000-000000000001"

# A made-up address in the connection string's format; it is never contacted.
CONNECTION_STRING = (
    "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
    "IngestionEndpoint=https://example.invalid/"
)


# --- Settings -----------------------------------------------------------------


def test_story_1_6_settings_read_the_workflow_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKFLOW_PORT", "9003")
    monkeypatch.setenv("WORKFLOW_DATABASE_HOST", "db.example.invalid")
    monkeypatch.setenv("WORKFLOW_DATABASE_ENTRA_AUTH", "true")
    monkeypatch.setenv("WORKFLOW_SCHEDULER_TASK_HUB", "aiuw-demo")
    monkeypatch.setenv("WORKFLOW_DEFAULT_RETRIEVER_CONFIGS", '["r4", "r5"]')
    monkeypatch.setenv("PORT", "1")
    monkeypatch.setenv("INTAKE_PORT", "2")

    settings = Settings()

    assert settings.port == 9003
    assert settings.database_host == "db.example.invalid"
    assert settings.database_entra_auth is True
    assert settings.scheduler_task_hub == "aiuw-demo"
    assert settings.default_retriever_configs == [
        RetrieverConfig.R4,
        RetrieverConfig.R5,
    ]


def test_story_1_6_settings_default_to_the_local_containers_and_the_demo_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in list(Settings.model_fields):
        monkeypatch.delenv(f"WORKFLOW_{name.upper()}", raising=False)

    settings = Settings()

    assert (settings.host, settings.port) == ("127.0.0.1", 8002)
    assert (settings.database_host, settings.database_port) == ("127.0.0.1", 5432)
    assert settings.database_entra_auth is False
    assert settings.database_service_role is None
    # The emulator of compose.yaml: plain HTTP on loopback, no credential.
    assert settings.scheduler_endpoint == "http://127.0.0.1:8080"
    assert settings.scheduler_task_hub == "default"
    assert (settings.scheduler_entra_auth, settings.scheduler_secure) == (False, False)
    assert settings.applicationinsights_connection_string is None
    # Spine, Build order: the demo path is the `llm` classifier with `r3`.
    parameters = default_parameters(settings)
    assert parameters.classifier_contender is ClassifierContender.LLM
    assert parameters.retriever_configs == (RetrieverConfig.R3,)
    assert (parameters.stop_after, parameters.eval_run_id) == (None, None)


@pytest.mark.parametrize("blank", ["", "   "])
def test_story_1_6_blank_variables_count_as_unset(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    for name in (
        "WORKFLOW_APPLICATIONINSIGHTS_CONNECTION_STRING",
        "WORKFLOW_AZURE_CLIENT_ID",
        "WORKFLOW_DATABASE_SERVICE_ROLE",
    ):
        monkeypatch.setenv(name, blank)

    settings = Settings()

    assert settings.applicationinsights_connection_string is None
    assert settings.azure_client_id is None
    assert settings.database_service_role is None


def test_story_1_6_the_connection_string_is_not_shown_when_settings_are_printed() -> (
    None
):
    settings = Settings(
        applicationinsights_connection_string=SecretStr(CONNECTION_STRING)
    )

    assert "InstrumentationKey" not in repr(settings)


def test_story_1_6_app_stack_sets_only_variables_the_settings_read() -> None:
    names = set(
        re.findall(r'name\s*=\s*"(WORKFLOW_[A-Z0-9_]+)"', APP_STACK_MAIN.read_text())
    )

    # What the service needs in Azure: its identity, the database and the scheduler.
    assert {
        "WORKFLOW_AZURE_CLIENT_ID",
        "WORKFLOW_DATABASE_ENTRA_AUTH",
        "WORKFLOW_DATABASE_HOST",
        "WORKFLOW_DATABASE_NAME",
        "WORKFLOW_DATABASE_USER",
        "WORKFLOW_SCHEDULER_ENDPOINT",
        "WORKFLOW_SCHEDULER_ENTRA_AUTH",
        "WORKFLOW_SCHEDULER_TASK_HUB",
    } <= names
    assert {name.removeprefix("WORKFLOW_").lower() for name in names} <= set(
        Settings.model_fields
    )
    # The running service is never told the role migrations grant to: it only
    # ever signs in as that role.
    assert "WORKFLOW_DATABASE_SERVICE_ROLE" not in names


# --- Azure sign-in ------------------------------------------------------------


def test_story_1_6_azure_calls_sign_in_with_the_service_identity() -> None:
    in_azure = azure_credential(Settings(azure_client_id=CLIENT_ID))
    on_a_laptop = azure_credential(Settings(azure_client_id=None))

    assert isinstance(in_azure, ManagedIdentityCredential)
    assert isinstance(on_a_laptop, DefaultAzureCredential)
    # One credential per process, so its token cache is shared.
    assert azure_credential(Settings(azure_client_id=CLIENT_ID)) is in_azure


# The first token the fake credential hands out.
FIRST_TOKEN = "entra-token-1"  # noqa: S105 - a made-up value


class FakeCredential:
    """Hands out tokens without a network; records where each was asked for."""

    def __init__(self, lifetime: float = 3600.0) -> None:
        self.scopes: list[str] = []
        self.threads: list[int] = []
        self.lifetime = lifetime

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        self.scopes.extend(scopes)
        self.threads.append(threading.get_ident())
        number = len(self.threads)
        expires_on = int(time.time() + self.lifetime)

        class Token:
            token = f"entra-token-{number}"

        Token.expires_on = expires_on  # type: ignore[attr-defined]  # shaped like azure-core's AccessToken
        return Token()


class Captured(Exception):
    """Raised instead of opening a connection, once the parameters are seen."""


def test_story_1_6_database_url_holds_no_password_and_requires_tls_in_azure() -> None:
    local = database_url(Settings())
    azure = database_url(
        Settings(
            database_host="pgsql-aiuw-demo-wus3.postgres.database.azure.com",
            database_user="id-aiuw-demo-wus3-workflow",
            database_entra_auth=True,
            database_connect_timeout_seconds=7,
        )
    )

    assert local.render_as_string(hide_password=False) == (
        "postgresql+psycopg://aiuw@127.0.0.1:5432/aiuw?connect_timeout=10"
    )
    assert azure.password is None
    assert azure.query == {"connect_timeout": "7", "sslmode": "require"}
    assert azure.username == "id-aiuw-demo-wus3-workflow"


def test_story_1_6_database_connections_sign_in_with_an_entra_token() -> None:
    credential = FakeCredential()
    engine = create_engine(database_url(Settings(database_entra_auth=True)))
    use_entra_token(engine, EntraToken(credential))
    seen: dict[str, Any] = {}

    @event.listens_for(engine, "do_connect")
    def capture(dialect: Any, record: Any, arguments: Any, parameters: Any) -> None:
        seen.update(parameters)
        raise Captured

    with pytest.raises(Captured):
        engine.connect()

    assert seen["password"] == FIRST_TOKEN
    assert seen["connect_timeout"] == "10"
    assert credential.scopes == [POSTGRESQL_TOKEN_SCOPE]


def test_story_1_6_the_token_is_fetched_off_the_event_loop_and_then_kept() -> None:
    credential = FakeCredential()
    token = EntraToken(credential)

    async def scenario() -> tuple[int, str]:
        await token.refresh()
        await token.refresh()
        # What the connection hook does, on the loop's thread: no fetch now.
        return threading.get_ident(), token.value()

    loop_thread, value = asyncio.run(scenario())

    assert value == FIRST_TOKEN
    assert len(credential.threads) == 1
    assert credential.threads[0] != loop_thread


def test_story_1_6_a_token_near_its_end_is_replaced_before_use() -> None:
    credential = FakeCredential(lifetime=TOKEN_REFRESH_MARGIN_SECONDS - 1)
    token = EntraToken(credential)

    asyncio.run(token.refresh())
    asyncio.run(token.refresh())

    assert len(credential.threads) == 2
    # Without a loop (migrations), the token is fetched on the spot.
    assert EntraToken(FakeCredential()).value() == FIRST_TOKEN


class SlowCredential(FakeCredential):
    """A credential whose fetch takes a moment, as a network call does."""

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        time.sleep(0.05)
        return super().get_token(*scopes, **kwargs)


def test_story_1_6_callers_that_arrive_together_share_one_token_fetch() -> None:
    credential = SlowCredential()
    token = EntraToken(credential)

    async def scenario() -> None:
        await asyncio.gather(*(token.refresh() for _ in range(8)))

    asyncio.run(scenario())

    # One fetch, off the event loop; the others waited for it and used it.
    assert len(credential.threads) == 1
    assert token.value() == FIRST_TOKEN


def test_story_1_6_the_connection_hook_never_fetches_a_token_that_is_still_valid() -> (
    None
):
    now = [1_000_000.0]
    credential = FakeCredential()
    token = EntraToken(credential, clock=lambda: now[0])

    async def refreshed_value() -> tuple[str, int]:
        await token.refresh()
        return token.value(), threading.get_ident()

    first, loop_thread = asyncio.run(refreshed_value())
    # Inside the refresh margin but not expired: what a reconnect just after
    # a refresh can meet. The hook answers from memory, on the loop's thread.
    now[0] = time.time() + 3600 - TOKEN_REFRESH_MARGIN_SECONDS + 5
    assert token.value() == first
    assert len(credential.threads) == 1

    # The next refresh replaces it ahead of its end, off the loop.
    second, loop_thread = asyncio.run(refreshed_value())
    assert second != first
    assert len(credential.threads) == 2
    assert loop_thread not in credential.threads


def test_story_1_6_the_service_refreshes_the_token_before_it_connects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = FakeCredential()
    monkeypatch.setattr(db, "azure_credential", lambda _settings: credential)
    database = build_database(Settings(database_entra_auth=True))
    seen: dict[str, Any] = {}

    @event.listens_for(database.engine.sync_engine, "do_connect")
    def capture(dialect: Any, record: Any, arguments: Any, parameters: Any) -> None:
        seen.update(parameters, connect_thread=threading.get_ident())
        raise Captured

    async def scenario() -> None:
        with pytest.raises(Captured):
            async with database.connect():
                pass
        with pytest.raises(Captured):
            async with database.begin():
                pass

    asyncio.run(scenario())

    assert seen["password"] == FIRST_TOKEN
    # One fetch for both connections, and not on the thread that connects.
    assert len(credential.threads) == 1
    assert credential.threads[0] != seen["connect_thread"]


def test_story_1_6_the_engine_has_connect_statement_and_pool_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[Settings] = []

    def credential_for(settings: Settings) -> FakeCredential:
        asked.append(settings)
        return FakeCredential()

    monkeypatch.setattr(db, "azure_credential", credential_for)
    settings = Settings(
        database_connect_timeout_seconds=3,
        database_statement_timeout_seconds=4,
        database_pool_timeout_seconds=5.0,
    )
    database = build_database(settings)
    seen: dict[str, Any] = {}

    @event.listens_for(database.engine.sync_engine, "do_connect")
    def capture(dialect: Any, record: Any, arguments: Any, parameters: Any) -> None:
        seen.update(parameters)
        raise Captured

    async def scenario() -> None:
        with pytest.raises(Captured):
            async with database.connect():
                pass

    asyncio.run(scenario())

    assert seen["connect_timeout"] == "3"
    assert seen["options"] == "-c statement_timeout=4000"
    assert database.engine.pool.timeout() == 5.0  # type: ignore[attr-defined]  # QueuePool's own accessor
    # No token without Entra auth: the local container has no password.
    assert "password" not in seen
    assert asked == []
    defaults = Settings()
    assert (
        defaults.database_connect_timeout_seconds,
        defaults.database_statement_timeout_seconds,
        defaults.database_pool_timeout_seconds,
    ) == (10, 30, 10.0)


def test_story_1_6_migrations_sign_in_with_an_entra_token_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # migrations/env.py with Entra auth on: what the pipeline's run does.
    credential = FakeCredential()
    monkeypatch.setattr(db, "azure_credential", lambda _settings: credential)
    seen: dict[str, Any] = {}

    def fake_connect(*arguments: Any, **parameters: Any) -> None:
        seen.update(parameters)
        raise Captured

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    settings = Settings(
        database_host="pgsql-aiuw-demo-wus3.postgres.database.azure.com",
        database_user="id-aiuw-demo-wus3-deploy",
        database_entra_auth=True,
    )

    with pytest.raises(Captured):
        command.upgrade(alembic_config(settings), "head")

    assert seen["password"] == FIRST_TOKEN
    assert seen["sslmode"] == "require"
    assert seen["user"] == "id-aiuw-demo-wus3-deploy"
    assert seen["host"] == "pgsql-aiuw-demo-wus3.postgres.database.azure.com"
    # Migrations are not bound by the service's statement timeout.
    assert "options" not in seen
    assert credential.scopes == [POSTGRESQL_TOKEN_SCOPE]


# --- Migrations ---------------------------------------------------------------


def test_story_1_6_the_bundled_head_is_the_newest_migration_in_the_package() -> None:
    versions = sorted(path.name for path in (MIGRATIONS_DIR / "versions").glob("*.py"))

    assert versions == [
        "v0001_case_status_page_status_and_audit_event.py",
        "v0002_audit_event_is_append_only.py",
        "v0003_human_decision.py",
    ]
    assert bundled_head() == "0003"
    # Inside the package, so the image carries it.
    assert MIGRATIONS_DIR.parent.name == "workflow"
    assert (MIGRATIONS_DIR / "env.py").is_file()


def test_story_1_6_alembic_command_line_uses_the_same_migrations() -> None:
    ini = (REPOSITORY_ROOT / "services" / "workflow" / "alembic.ini").read_text()

    assert "script_location = %(here)s/src/workflow/migrations" in ini
    assert alembic_config().get_main_option("script_location") == str(MIGRATIONS_DIR)
    # No database address or password is written in the file.
    assert "sqlalchemy.url" not in ini


def test_story_1_6_no_migration_and_no_code_updates_or_deletes_an_audit_event() -> None:
    package = MIGRATIONS_DIR.parent
    migration = (
        MIGRATIONS_DIR / "versions" / "v0001_case_status_page_status_and_audit_event.py"
    ).read_text()

    # AD-8, security rule 32: the service role is granted SELECT and INSERT only.
    assert 'grant_on_table("audit_event", ["SELECT", "INSERT"])' in migration
    assert migration.count('grant_on_table("audit_event"') == 1
    # DELETE is granted on no table at all.
    assert re.findall(r'grant_on_table\([^)]*"DELETE"', migration) == []
    # The one module that touches the table inserts into it and reads it.
    users = [
        path.relative_to(package).as_posix()
        for path in package.rglob("*.py")
        if "audit_event_table" in path.read_text()
    ]
    assert users == ["adapters/db.py"]
    adapter = (package / "adapters" / "db.py").read_text()
    assert "insert(audit_event_table)" in adapter
    assert "update(audit_event_table" not in adapter
    assert "delete(" not in adapter


def test_story_1_6_local_setup_reports_the_role_it_made(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked: list[Settings] = []

    def fake_ensure(settings: Settings) -> str:
        asked.append(settings)
        return "workflow"

    monkeypatch.setattr(local_setup, "ensure_local_service_role", fake_ensure)

    local_setup.main()

    assert len(asked) == 1
    assert capsys.readouterr().out == "Database role ready: workflow\n"


# --- Start-up and telemetry ---------------------------------------------------


def test_story_1_6_server_starts_on_the_configured_host_and_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: dict[str, Any] = {}

    def fake_run(app: str, **options: Any) -> None:
        started.update(options, app=app)

    monkeypatch.setenv("WORKFLOW_HOST", "127.0.0.1")
    monkeypatch.setenv("WORKFLOW_PORT", "8125")
    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(
        entry, "configure_logging", lambda: started.update(logging=True)
    )
    get_settings.cache_clear()
    try:
        entry.main()
    finally:
        get_settings.cache_clear()

    assert started["app"] == "workflow.adapters.http.app:create_app"
    assert started["factory"] is True
    assert (started["host"], started["port"]) == ("127.0.0.1", 8125)
    assert started["server_header"] is False
    assert started["logging"] is True


def test_story_1_6_app_built_from_the_environment_uses_the_real_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Nothing listens on these ports: building the app and starting its worker
    # open no connection that the health route needs.
    monkeypatch.setenv("WORKFLOW_SCHEDULER_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("WORKFLOW_DATABASE_PORT", "1")
    get_settings.cache_clear()
    try:
        app = create_app()
    finally:
        get_settings.cache_clear()

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}


def traced_client(
    settings: Settings, dependencies: Dependencies
) -> tuple[TestClient, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_app(settings, dependencies=dependencies)
    instrument_app(app, tracer_provider=provider)
    return TestClient(app, raise_server_exceptions=False), exporter


def request_spans(exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return [
        span for span in exporter.get_finished_spans() if span.kind is SpanKind.SERVER
    ]


def test_story_1_6_probes_are_not_traced_and_a_start_is(
    settings: Settings, dependencies: Dependencies, case_id: str
) -> None:
    client, exporter = traced_client(settings, dependencies)

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert request_spans(exporter) == []

    response = client.post(
        f"/cases/{case_id}/start",
        headers={
            "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        },
    )
    assert response.status_code == 200
    (span,) = request_spans(exporter)
    # One trace per request across services: the caller's trace is continued.
    context = span.get_span_context()
    assert context is not None
    assert format_trace_id(context.trace_id) == "0af7651916cd43dd8448eb211c80319c"


def test_story_1_6_unhandled_error_is_a_plain_500_recorded_on_the_span(
    settings: Settings,
    dependencies: Dependencies,
    case_id: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret detail SELECT 1")

    monkeypatch.setattr("workflow.adapters.http.routes.start_case", broken)
    client, exporter = traced_client(settings, dependencies)

    with caplog.at_level(logging.ERROR):
        response = client.post(f"/cases/{case_id}/start")

    assert response.status_code == 500
    body = ErrorBody.model_validate(response.json())
    assert body.error.code.value == "internal_error"
    assert "secret" not in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    (span,) = request_spans(exporter)
    assert span.status.status_code is StatusCode.ERROR
    (recorded,) = [item for item in span.events if item.name == "exception"]
    attributes = dict(recorded.attributes or {})
    assert attributes["exception.type"] == "RuntimeError"
    assert "secret detail" not in str(attributes)
    context = span.get_span_context()
    assert context is not None
    assert body.error.trace_id == format_trace_id(context.trace_id)
    # The log line: type, trace id and code location, without the message.
    assert "type=RuntimeError" in caplog.text
    assert "in broken" in caplog.text
    assert "secret detail" not in caplog.text


class FakeExporterSetup:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def test_story_1_6_telemetry_stays_off_without_a_connection_string() -> None:
    exporter_setup = FakeExporterSetup()

    assert configure_telemetry(Settings(), exporter_setup=exporter_setup) is False
    assert exporter_setup.calls == []


def test_story_1_6_telemetry_is_reported_as_workflow_with_the_service_identity() -> (
    None
):
    exporter_setup = FakeExporterSetup()
    settings = Settings(
        applicationinsights_connection_string=SecretStr(CONNECTION_STRING),
        otel_sampling_ratio=0.25,
        azure_client_id=CLIENT_ID,
    )

    assert configure_telemetry(settings, exporter_setup=exporter_setup) is True

    (call,) = exporter_setup.calls
    assert call["connection_string"] == CONNECTION_STRING
    assert call["sampling_ratio"] == 0.25
    assert isinstance(call["credential"], ManagedIdentityCredential)
    assert call["resource"].attributes[SERVICE_NAME] == "workflow"
    assert call["instrumentation_options"] == {"fastapi": {"enabled": False}}


def test_story_1_6_app_is_instrumented_only_when_telemetry_is_on(
    settings: Settings, dependencies: Dependencies, monkeypatch: pytest.MonkeyPatch
) -> None:
    instrumented: list[object] = []
    monkeypatch.setattr(
        "workflow.adapters.http.app.instrument_app", instrumented.append
    )

    create_app(settings, dependencies=dependencies)
    assert instrumented == []

    monkeypatch.setattr(
        "workflow.adapters.http.app.configure_telemetry", lambda _settings: True
    )
    app = create_app(settings, dependencies=dependencies)
    assert instrumented == [app]


@pytest.mark.parametrize(
    ("traceparent", "expected"),
    [
        (
            "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
            "0af7651916cd43dd8448eb211c80319c",
        ),
        (None, None),
        ("00-XYZ-b7ad6b7169203331-01", None),
    ],
)
def test_story_1_6_trace_id_falls_back_to_the_callers_traceparent(
    traceparent: str | None, expected: str | None
) -> None:
    assert current_trace_id(traceparent) == expected


def test_story_1_6_logging_is_configured_at_start_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(logging, "basicConfig", lambda **options: calls.append(options))

    configure_logging()

    assert calls == [{"level": logging.INFO, "format": telemetry.LOG_FORMAT}]
    # The Azure SDK logs request headers at INFO; those lines are switched off.
    assert logging.getLogger("azure").level == logging.WARNING
    assert logging.getLogger("azure.core.pipeline").getEffectiveLevel() == (
        logging.WARNING
    )
