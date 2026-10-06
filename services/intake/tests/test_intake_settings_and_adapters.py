"""Story 1.5: `intake`'s settings, its Azure sign-in, telemetry and start-up, without a network."""

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
from pydantic import SecretStr, ValidationError
from sqlalchemy import create_engine, event

import intake.__main__ as entry
from contracts.errors import ErrorBody
from intake import local_setup
from intake.adapters import blob, db, telemetry
from intake.adapters.blob import (
    BlobOriginalStore,
    build_blob_service,
    ensure_local_containers,
)
from intake.adapters.credential import azure_credential
from intake.adapters.db import (
    POSTGRESQL_TOKEN_SCOPE,
    TOKEN_REFRESH_MARGIN_SECONDS,
    EntraToken,
    build_database,
    database_url,
    use_entra_token,
)
from intake.adapters.http.app import create_app
from intake.adapters.http.routes import Dependencies
from intake.adapters.migrations import MIGRATIONS_DIR, alembic_config, bundled_head
from intake.adapters.telemetry import (
    configure_logging,
    configure_telemetry,
    current_trace_id,
    instrument_app,
)
from intake.settings import Settings, get_settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
APP_STACK_MAIN = REPOSITORY_ROOT / "infra" / "demo" / "app" / "main.tf"
CLIENT_ID = "00000000-0000-0000-0000-000000000001"
ACCOUNT_URL = "https://staiuwdemowus3.blob.core.windows.net"

# A made-up address in the connection string's format; it is never contacted.
CONNECTION_STRING = (
    "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
    "IngestionEndpoint=https://example.invalid/"
)


# --- Settings -----------------------------------------------------------------


def test_story_1_5_settings_read_the_intake_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INTAKE_PORT", "9002")
    monkeypatch.setenv("INTAKE_DATABASE_HOST", "db.example.invalid")
    monkeypatch.setenv("INTAKE_DATABASE_ENTRA_AUTH", "true")
    monkeypatch.setenv("INTAKE_BLOB_ACCOUNT_URL", ACCOUNT_URL)
    monkeypatch.setenv("PORT", "1")
    monkeypatch.setenv("WEB_PORT", "2")

    settings = Settings()

    assert settings.port == 9002
    assert settings.database_host == "db.example.invalid"
    assert settings.database_entra_auth is True
    assert settings.blob_account_url == ACCOUNT_URL


def test_story_1_5_settings_default_to_the_local_containers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in list(Settings.model_fields):
        monkeypatch.delenv(f"INTAKE_{name.upper()}", raising=False)

    settings = Settings()

    assert (settings.host, settings.port) == ("127.0.0.1", 8001)
    assert (settings.database_host, settings.database_port) == ("127.0.0.1", 5432)
    assert settings.database_entra_auth is False
    assert (settings.originals_container, settings.cases_container) == (
        "originals",
        "cases",
    )
    assert settings.blob_account_url is None
    assert settings.blob_connection_string is None
    assert settings.applicationinsights_connection_string is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_story_1_5_blank_variables_count_as_unset(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    for name in (
        "INTAKE_APPLICATIONINSIGHTS_CONNECTION_STRING",
        "INTAKE_AZURE_CLIENT_ID",
        "INTAKE_BLOB_ACCOUNT_URL",
        "INTAKE_BLOB_CONNECTION_STRING",
    ):
        monkeypatch.setenv(name, blank)

    settings = Settings()

    assert settings.applicationinsights_connection_string is None
    assert settings.azure_client_id is None
    assert settings.blob_account_url is None
    assert settings.blob_connection_string is None


def test_story_1_5_connection_strings_are_not_shown_when_settings_are_printed() -> None:
    settings = Settings(
        applicationinsights_connection_string=SecretStr(CONNECTION_STRING),
        blob_connection_string=SecretStr("UseDevelopmentStorage=true"),
    )

    assert "InstrumentationKey" not in repr(settings)
    assert "UseDevelopmentStorage" not in repr(settings)


def test_story_1_5_app_stack_sets_only_variables_the_settings_read() -> None:
    names = set(
        re.findall(r'name\s*=\s*"(INTAKE_[A-Z0-9_]+)"', APP_STACK_MAIN.read_text())
    )

    # What the service needs in Azure: its identity, the database and the account.
    assert {
        "INTAKE_AZURE_CLIENT_ID",
        "INTAKE_BLOB_ACCOUNT_URL",
        "INTAKE_DATABASE_ENTRA_AUTH",
        "INTAKE_DATABASE_HOST",
        "INTAKE_DATABASE_NAME",
        "INTAKE_DATABASE_USER",
    } <= names
    assert {name.removeprefix("INTAKE_").lower() for name in names} <= set(
        Settings.model_fields
    )
    # Key access is off in Azure: the emulator's setting is never set there.
    assert "INTAKE_BLOB_CONNECTION_STRING" not in names


# --- Azure sign-in ------------------------------------------------------------


def test_story_1_5_azure_calls_sign_in_with_the_service_identity() -> None:
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


def test_story_1_5_database_url_holds_no_password_and_requires_tls_in_azure() -> None:
    local = database_url(Settings())
    azure = database_url(
        Settings(
            database_host="pgsql-aiuw-demo-wus3.postgres.database.azure.com",
            database_user="id-aiuw-demo-wus3-intake",
            database_entra_auth=True,
            database_connect_timeout_seconds=7,
        )
    )

    assert local.render_as_string(hide_password=False) == (
        "postgresql+psycopg://aiuw@127.0.0.1:5432/aiuw?connect_timeout=10"
    )
    assert azure.password is None
    assert azure.query == {"connect_timeout": "7", "sslmode": "require"}
    assert azure.username == "id-aiuw-demo-wus3-intake"


def test_story_1_5_database_connections_sign_in_with_an_entra_token() -> None:
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


def test_story_1_5_the_token_is_fetched_off_the_event_loop_and_then_kept() -> None:
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


def test_story_1_5_a_token_near_its_end_is_replaced_before_use() -> None:
    credential = FakeCredential(lifetime=TOKEN_REFRESH_MARGIN_SECONDS - 1)
    token = EntraToken(credential)

    asyncio.run(token.refresh())
    asyncio.run(token.refresh())

    assert len(credential.threads) == 2
    # Without a loop (migrations), the token is fetched on the spot.
    assert EntraToken(FakeCredential()).value() == FIRST_TOKEN


def test_story_1_5_the_service_refreshes_the_token_before_it_connects(
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


def test_story_1_5_the_engine_has_connect_statement_and_pool_timeouts(
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


def test_story_1_5_migrations_sign_in_with_an_entra_token_too(
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


class SlowContainer:
    """A container client whose upload waits until the test lets it finish."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.started = threading.Event()
        self.release = threading.Event()

    def upload_blob(self, name: str, content: bytes, **options: Any) -> None:
        self.started.set()
        self.release.wait(timeout=5)
        self.calls.append(f"upload {name}")

    def delete_blob(self, name: str) -> None:
        self.calls.append(f"delete {name}")


class OneContainerService:
    def __init__(self, container: SlowContainer) -> None:
        self.container = container

    def get_container_client(self, name: str) -> SlowContainer:
        return self.container


def test_story_1_5_a_delete_waits_for_a_write_that_outlived_its_cancelled_caller() -> (
    None
):
    container = SlowContainer()
    store = BlobOriginalStore(OneContainerService(container), "originals")  # type: ignore[arg-type]  # a stand-in for the SDK's client

    async def scenario() -> None:
        put = asyncio.create_task(store.put("case/doc.pdf", b"%PDF-"))
        await asyncio.to_thread(container.started.wait)
        put.cancel()
        with pytest.raises(asyncio.CancelledError):
            await put
        # The worker thread is still writing. A delete now must not run first.
        delete = asyncio.create_task(store.delete("case/doc.pdf"))
        await asyncio.sleep(0.05)
        assert not delete.done()
        assert container.calls == []
        container.release.set()
        await delete

    asyncio.run(scenario())

    assert container.calls == ["upload case/doc.pdf", "delete case/doc.pdf"]


def test_story_1_5_blob_storage_is_reached_with_the_service_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = FakeCredential()
    monkeypatch.setattr(blob, "azure_credential", lambda _settings: credential)

    service = build_blob_service(Settings(blob_account_url=ACCOUNT_URL))

    assert service.url == f"{ACCOUNT_URL}/"
    assert service.credential is credential


def test_story_1_5_the_emulator_is_reached_with_its_connection_string() -> None:
    service = build_blob_service(
        Settings(blob_connection_string=SecretStr("UseDevelopmentStorage=true"))
    )

    assert service.url == "http://127.0.0.1:10000/devstoreaccount1/"


def test_story_1_5_both_ways_to_blob_storage_at_once_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="not both"):
        Settings(
            blob_account_url=ACCOUNT_URL,
            blob_connection_string=SecretStr("UseDevelopmentStorage=true"),
        )

    # From the environment too, where the emulator's setting would otherwise win.
    monkeypatch.setenv("INTAKE_BLOB_ACCOUNT_URL", ACCOUNT_URL)
    monkeypatch.setenv("INTAKE_BLOB_CONNECTION_STRING", "UseDevelopmentStorage=true")
    with pytest.raises(ValidationError, match="not both"):
        Settings()
    # A blank one counts as unset.
    monkeypatch.setenv("INTAKE_BLOB_CONNECTION_STRING", " ")
    assert Settings().blob_account_url == ACCOUNT_URL


def test_story_1_5_blob_storage_must_be_configured() -> None:
    with pytest.raises(ValueError, match="INTAKE_BLOB_ACCOUNT_URL"):
        build_blob_service(Settings())
    with pytest.raises(ValueError, match="INTAKE_BLOB_ACCOUNT_URL"):
        create_app(Settings())


def test_story_1_5_containers_are_never_created_outside_the_emulator() -> None:
    with pytest.raises(ValueError, match="local emulator"):
        ensure_local_containers(Settings(blob_account_url=ACCOUNT_URL))


def test_story_1_5_local_setup_reports_the_containers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        local_setup, "ensure_local_containers", lambda _settings: ["originals", "cases"]
    )

    local_setup.main()

    assert capsys.readouterr().out == "Blob containers ready: originals, cases\n"


# --- Migrations ---------------------------------------------------------------


def test_story_1_5_the_bundled_head_is_the_newest_migration_in_the_package() -> None:
    versions = sorted(path.name for path in (MIGRATIONS_DIR / "versions").glob("*.py"))

    assert versions == ["v0001_case_and_document.py"]
    assert bundled_head() == "0001"
    # Inside the package, so the image carries it.
    assert MIGRATIONS_DIR.parent.name == "intake"
    assert (MIGRATIONS_DIR / "env.py").is_file()


def test_story_1_5_alembic_command_line_uses_the_same_migrations() -> None:
    ini = (REPOSITORY_ROOT / "services" / "intake" / "alembic.ini").read_text()

    assert "script_location = %(here)s/src/intake/migrations" in ini
    assert alembic_config().get_main_option("script_location") == str(MIGRATIONS_DIR)
    # No database address or password is written in the file.
    assert "sqlalchemy.url" not in ini


def test_story_1_5_the_first_migration_creates_only_cases_and_documents() -> None:
    source = (MIGRATIONS_DIR / "versions" / "v0001_case_and_document.py").read_text()

    assert re.findall(r'op\.create_table\(\s*"(\w+)"', source) == ["case", "document"]
    # Pages, page text and boxes arrive with story 1.7.
    assert "page" not in source


# --- Start-up and telemetry ---------------------------------------------------


def test_story_1_5_server_starts_on_the_configured_host_and_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: dict[str, Any] = {}

    def fake_run(app: str, **options: Any) -> None:
        started.update(options, app=app)

    monkeypatch.setenv("INTAKE_HOST", "127.0.0.1")
    monkeypatch.setenv("INTAKE_PORT", "8124")
    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(
        entry, "configure_logging", lambda: started.update(logging=True)
    )
    get_settings.cache_clear()
    try:
        entry.main()
    finally:
        get_settings.cache_clear()

    assert started["app"] == "intake.adapters.http.app:create_app"
    assert started["factory"] is True
    assert (started["host"], started["port"]) == ("127.0.0.1", 8124)
    assert started["server_header"] is False
    assert started["logging"] is True


def test_story_1_5_app_built_from_the_environment_uses_the_real_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INTAKE_BLOB_CONNECTION_STRING", "UseDevelopmentStorage=true")
    get_settings.cache_clear()
    try:
        # Building the app opens no connection, so no container is needed.
        app = create_app()
    finally:
        get_settings.cache_clear()

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}


class FakeExporterSetup:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def test_story_1_5_telemetry_stays_off_without_a_connection_string() -> None:
    exporter_setup = FakeExporterSetup()

    assert configure_telemetry(Settings(), exporter_setup=exporter_setup) is False
    assert exporter_setup.calls == []


def test_story_1_5_telemetry_is_reported_as_intake_with_the_service_identity() -> None:
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
    assert call["resource"].attributes[SERVICE_NAME] == "intake"
    assert call["instrumentation_options"] == {"fastapi": {"enabled": False}}


def test_story_1_5_app_is_instrumented_only_when_telemetry_is_on(
    settings: Settings, dependencies: Dependencies, monkeypatch: pytest.MonkeyPatch
) -> None:
    instrumented: list[object] = []
    monkeypatch.setattr("intake.adapters.http.app.instrument_app", instrumented.append)

    create_app(settings, dependencies=dependencies)
    assert instrumented == []

    monkeypatch.setattr(
        "intake.adapters.http.app.configure_telemetry", lambda _settings: True
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
def test_story_1_5_trace_id_falls_back_to_the_callers_traceparent(
    traceparent: str | None, expected: str | None
) -> None:
    assert current_trace_id(traceparent) == expected


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


def test_story_1_5_probes_are_not_traced_and_an_upload_is(
    settings: Settings, dependencies: Dependencies, case_pdf: bytes
) -> None:
    client, exporter = traced_client(settings, dependencies)

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert request_spans(exporter) == []

    response = client.post(
        "/cases",
        content=case_pdf,
        headers={
            "Content-Type": "application/pdf",
            "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        },
    )
    assert response.status_code == 201
    (span,) = request_spans(exporter)
    # One trace per request across services: the caller's trace is continued.
    context = span.get_span_context()
    assert context is not None
    assert format_trace_id(context.trace_id) == "0af7651916cd43dd8448eb211c80319c"


def test_story_1_5_unhandled_error_is_a_plain_500_recorded_on_the_span(
    settings: Settings,
    dependencies: Dependencies,
    case_pdf: bytes,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret detail SELECT 1")

    monkeypatch.setattr("intake.adapters.http.routes.read_upload", broken)
    client, exporter = traced_client(settings, dependencies)

    with caplog.at_level(logging.ERROR):
        response = client.post(
            "/cases", content=case_pdf, headers={"Content-Type": "application/pdf"}
        )

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


def test_story_1_5_logging_is_configured_at_start_up(
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
