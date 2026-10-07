"""Story 1.5: `intake`'s settings, its Azure sign-in, telemetry and start-up, without a network."""

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode
from pydantic import SecretStr
from sqlalchemy import create_engine, event

from intake.adapters import db, telemetry
from intake.adapters.blob import (
    ensure_local_containers,
)
from intake.adapters.db import (
    POSTGRESQL_TOKEN_SCOPE,
    EntraToken,
    database_url,
    use_entra_token,
)
from intake.settings import Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ACCOUNT_URL = "https://staiuwdemowus3.blob.core.windows.net"

# A made-up address in the connection string's format; it is never contacted.
CONNECTION_STRING = (
    "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
    "IngestionEndpoint=https://example.invalid/"
)


# --- Settings -----------------------------------------------------------------


def test_story_1_5_connection_strings_are_not_shown_when_settings_are_printed() -> None:
    settings = Settings(
        applicationinsights_connection_string=SecretStr(CONNECTION_STRING),
        blob_connection_string=SecretStr("UseDevelopmentStorage=true"),
    )

    assert "InstrumentationKey" not in repr(settings)
    assert "UseDevelopmentStorage" not in repr(settings)


# --- Azure sign-in ------------------------------------------------------------


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


def test_story_1_5_containers_are_never_created_outside_the_emulator() -> None:
    with pytest.raises(ValueError, match="local emulator"):
        ensure_local_containers(Settings(blob_account_url=ACCOUNT_URL))


# --- Migrations ---------------------------------------------------------------


# --- Start-up and telemetry ---------------------------------------------------


def test_story_1_8_an_error_inside_an_adapters_span_leaves_its_type_and_no_message() -> (
    None
):
    # Found in the review of story 1.8, for every service: the tracing
    # library puts a raised error's message on the span by itself, and a
    # database error's message holds the statement that failed.
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    with (
        pytest.raises(RuntimeError),
        telemetry.adapter_span(provider.get_tracer("test"), "intake.db.add_case"),
    ):
        raise RuntimeError("INSERT INTO document VALUES ('SECRET-VALUE')")

    (span,) = exporter.get_finished_spans()
    (event,) = span.events
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description is None
    assert event.name == "exception"
    assert set(event.attributes or {}) == {"exception.type", "exception.stacktrace"}
    assert (event.attributes or {})["exception.type"] == "RuntimeError"
    assert "SECRET" not in repr(dict(event.attributes or {}))
    # The engine's own errors never carry the values of a statement either.
    assert db.build_database(Settings()).engine.sync_engine.hide_parameters is True
    # No adapter opens a span any other way.
    for source in Path(telemetry.__file__).parent.rglob("*.py"):
        if source.name != "telemetry.py":
            assert "start_as_current_span" not in source.read_text(), source.name
