"""Story 1.3: settings come from `WEB_` variables; telemetry is off unless configured."""

import logging
import re
from pathlib import Path
from typing import Any

import pytest
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from fastapi.testclient import TestClient
from opentelemetry.sdk.resources import SERVICE_NAME
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode, format_trace_id
from pydantic import SecretStr

from contracts.errors import ErrorBody
from web.adapters import telemetry
from web.adapters.http import spa
from web.adapters.http.app import create_app
from web.adapters.telemetry import (
    configure_logging,
    configure_telemetry,
    current_trace_id,
    instrument_app,
)
from web.settings import Settings

APP_STACK_MAIN = (
    Path(__file__).resolve().parents[3] / "infra" / "demo" / "app" / "main.tf"
)

# A made-up address in the connection string's format; it is never contacted.
CONNECTION_STRING = (
    "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
    "IngestionEndpoint=https://example.invalid/"
)


class FakeExporterSetup:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def test_story_1_3_settings_read_the_web_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEB_PORT", "9001")
    monkeypatch.setenv("WEB_HOST", "0.0.0.0")  # noqa: S104 - a value under test, nothing binds to it
    monkeypatch.setenv("WEB_SPA_DIR", "/app/spa")
    monkeypatch.setenv("PORT", "1")

    settings = Settings()

    assert settings.port == 9001
    assert settings.host == "0.0.0.0"  # noqa: S104 - as above
    assert str(settings.spa_dir) == "/app/spa"


def test_story_1_3_settings_default_to_loopback_and_no_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("WEB_HOST", "WEB_PORT", "WEB_APPLICATIONINSIGHTS_CONNECTION_STRING"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings()

    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.applicationinsights_connection_string is None
    assert settings.spa_dir.parts[-3:] == ("web", "spa", "dist")


def test_story_1_3_connection_string_is_not_shown_when_settings_are_printed() -> None:
    settings = Settings(
        applicationinsights_connection_string=SecretStr(CONNECTION_STRING)
    )

    assert "InstrumentationKey" not in repr(settings)
    assert "InstrumentationKey" not in str(settings)


def test_story_1_3_telemetry_stays_off_without_a_connection_string() -> None:
    exporter_setup = FakeExporterSetup()

    switched_on = configure_telemetry(
        Settings(applicationinsights_connection_string=None),
        exporter_setup=exporter_setup,
    )

    assert switched_on is False
    assert exporter_setup.calls == []


def test_story_1_3_telemetry_is_switched_on_with_the_service_identity() -> None:
    exporter_setup = FakeExporterSetup()
    settings = Settings(
        applicationinsights_connection_string=SecretStr(CONNECTION_STRING),
        otel_sampling_ratio=0.25,
        azure_client_id="00000000-0000-0000-0000-000000000001",
    )

    switched_on = configure_telemetry(settings, exporter_setup=exporter_setup)

    assert switched_on is True
    (call,) = exporter_setup.calls
    assert call["connection_string"] == CONNECTION_STRING
    assert call["sampling_ratio"] == 0.25
    assert isinstance(call["credential"], ManagedIdentityCredential)


def test_story_1_3_telemetry_uses_the_developer_sign_in_without_a_client_id() -> None:
    exporter_setup = FakeExporterSetup()
    settings = Settings(
        applicationinsights_connection_string=SecretStr(CONNECTION_STRING),
        azure_client_id=None,
    )

    configure_telemetry(settings, exporter_setup=exporter_setup)

    assert isinstance(exporter_setup.calls[0]["credential"], DefaultAzureCredential)


@pytest.mark.parametrize(
    ("traceparent", "expected"),
    [
        (
            "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
            "0af7651916cd43dd8448eb211c80319c",
        ),
        (None, None),
        ("", None),
        ("00-XYZ-b7ad6b7169203331-01", None),
    ],
)
def test_story_1_3_trace_id_falls_back_to_the_callers_traceparent(
    traceparent: str | None, expected: str | None
) -> None:
    assert current_trace_id(traceparent) == expected


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_story_1_3_blank_connection_string_and_client_id_count_as_unset(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    monkeypatch.setenv("WEB_APPLICATIONINSIGHTS_CONNECTION_STRING", blank)
    monkeypatch.setenv("WEB_AZURE_CLIENT_ID", blank)
    exporter_setup = FakeExporterSetup()

    settings = Settings()

    assert settings.applicationinsights_connection_string is None
    assert settings.azure_client_id is None
    assert configure_telemetry(settings, exporter_setup=exporter_setup) is False
    assert exporter_setup.calls == []
    passed_in = Settings(applicationinsights_connection_string=SecretStr(blank))
    assert passed_in.applicationinsights_connection_string is None


def test_story_1_3_app_stack_sets_only_variables_the_settings_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = set(
        re.findall(r'name\s*=\s*"(WEB_[A-Z0-9_]+)"', APP_STACK_MAIN.read_text())
    )
    telemetry_names = {
        "WEB_APPLICATIONINSIGHTS_CONNECTION_STRING",
        "WEB_AZURE_CLIENT_ID",
        "WEB_OTEL_SAMPLING_RATIO",
    }

    # Every variable the stack sets is a field of the settings object...
    assert telemetry_names <= names
    assert {name.removeprefix("WEB_").lower() for name in names} <= set(
        Settings.model_fields
    )

    # ...and the three telemetry ones arrive in the fields the code uses.
    monkeypatch.setenv("WEB_APPLICATIONINSIGHTS_CONNECTION_STRING", CONNECTION_STRING)
    monkeypatch.setenv("WEB_AZURE_CLIENT_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("WEB_OTEL_SAMPLING_RATIO", "0.5")
    settings = Settings()
    connection_string = settings.applicationinsights_connection_string
    assert connection_string is not None
    assert connection_string.get_secret_value() == CONNECTION_STRING
    assert settings.azure_client_id == "00000000-0000-0000-0000-000000000001"
    assert settings.otel_sampling_ratio == 0.5


def test_story_1_3_telemetry_is_reported_under_the_app_id() -> None:
    exporter_setup = FakeExporterSetup()

    configure_telemetry(
        Settings(applicationinsights_connection_string=SecretStr(CONNECTION_STRING)),
        exporter_setup=exporter_setup,
    )

    (call,) = exporter_setup.calls
    assert call["resource"].attributes[SERVICE_NAME] == "web"
    # The app is instrumented by hand so that the health route can be left out.
    assert call["instrumentation_options"] == {"fastapi": {"enabled": False}}


def test_story_1_3_app_is_instrumented_only_when_telemetry_is_on(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    instrumented: list[object] = []
    monkeypatch.setattr("web.adapters.http.app.instrument_app", instrumented.append)

    create_app(settings)
    assert instrumented == []

    monkeypatch.setattr(
        "web.adapters.http.app.configure_telemetry", lambda _settings: True
    )
    app = create_app(settings)
    assert instrumented == [app]


def test_story_1_3_trace_id_is_the_active_spans() -> None:
    tracer = TracerProvider().get_tracer("test")

    with tracer.start_as_current_span("request") as span:
        inside = current_trace_id(
            "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        )

    # The span wins over the header, and its id is in the 32-hex wire form.
    assert inside == format_trace_id(span.get_span_context().trace_id)
    assert inside != "0af7651916cd43dd8448eb211c80319c"
    assert re.fullmatch(r"[0-9a-f]{32}", inside or "")


def traced_client(settings: Settings) -> tuple[TestClient, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_app(settings)
    instrument_app(app, tracer_provider=provider)
    return TestClient(app, raise_server_exceptions=False), exporter


def request_spans(exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return [span for span in exporter.get_finished_spans() if span.parent is None]


def test_story_1_3_health_probes_are_not_traced(settings: Settings) -> None:
    client, exporter = traced_client(settings)

    assert client.get("/api/health").status_code == 200
    assert client.head("/api/health").status_code == 200
    assert request_spans(exporter) == []

    client.get("/api/me", headers={"X-Demo-Role": "customer"})
    assert len(request_spans(exporter)) == 1


def test_story_1_3_unhandled_error_is_recorded_on_the_requests_span(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_response(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret detail")

    monkeypatch.setattr(spa, "FileResponse", broken_response)
    client, exporter = traced_client(settings)

    response = client.get("/")

    (span,) = request_spans(exporter)
    assert span.status.status_code is StatusCode.ERROR
    (event,) = [event for event in span.events if event.name == "exception"]
    attributes = dict(event.attributes or {})
    assert attributes["exception.type"] == "RuntimeError"
    assert "in broken_response" in str(attributes["exception.stacktrace"])
    # security rule 31: the message is not in the event.
    assert "secret detail" not in str(attributes)
    # The error body names the same trace.
    context = span.get_span_context()
    assert context is not None
    body = ErrorBody.model_validate(response.json())
    assert body.error.trace_id == format_trace_id(context.trace_id)


def test_story_1_3_logging_is_configured_at_start_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(logging, "basicConfig", lambda **options: calls.append(options))

    configure_logging()

    assert calls == [{"level": logging.INFO, "format": telemetry.LOG_FORMAT}]
