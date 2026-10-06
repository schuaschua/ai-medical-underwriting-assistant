"""The one settings object of the `web` service (coding-style rule 12)."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The Dapr app id; also the service name telemetry is reported under.
APP_ID = "web"
# The probe target. It is kept out of traces: the platform calls it every few seconds.
HEALTH_PATH = "/api/health"

# services/web/spa/dist in a checkout; the container image sets WEB_SPA_DIR.
_DEFAULT_SPA_DIR = Path(__file__).resolve().parents[2] / "spa" / "dist"


class Settings(BaseSettings):
    """Read once from environment variables prefixed `WEB_`."""

    model_config = SettingsConfigDict(
        env_prefix="WEB_", extra="ignore", frozen=True, populate_by_name=True
    )

    # Loopback by default; the container image sets WEB_HOST to listen on all interfaces.
    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 8000
    # Folder holding the built SPA (index.html and its assets).
    spa_dir: Path = _DEFAULT_SPA_DIR

    # AD-3: the port of this service's own Dapr sidecar, on loopback. Dapr tells
    # the app its port in DAPR_HTTP_PORT, so that name is read as well.
    dapr_http_port: Annotated[
        int,
        Field(
            ge=1,
            le=65535,
            validation_alias=AliasChoices("WEB_DAPR_HTTP_PORT", "DAPR_HTTP_PORT"),
        ),
    ] = 3500
    # How long a call to another service may take.
    service_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    # Deadlines of a start, a progress read and an audit read, shortest first,
    # so each caller outlasts the one it calls:
    #   workflow's scheduler call 10 s (WORKFLOW_SCHEDULER_TIMEOUT_SECONDS)
    #   <  web 20 s (this setting)  <  browser 30 s (REQUEST_TIMEOUT_MS in the
    #   SPA's api/client.ts).
    # This one bounds the whole call to `workflow`, from request to answer, so
    # the browser shows the server's answer, not its own timeout.
    lifecycle_timeout_seconds: Annotated[float, Field(gt=0)] = 20.0
    # Upload deadlines, shortest first, so each caller outlasts the one it calls:
    #   intake 90 s (INTAKE_UPLOAD_DEADLINE_SECONDS)  <  web 120 s (this setting)
    #   <  browser 150 s (UPLOAD_TIMEOUT_MS in the SPA's api/client.ts).
    # This one bounds the whole call to `intake`, from first byte sent to answer.
    upload_timeout_seconds: Annotated[float, Field(gt=0)] = 120.0

    # Telemetry is exported only when a connection string is set. It is an address,
    # not a credential, but it is still kept out of logs and reprs.
    applicationinsights_connection_string: SecretStr | None = None
    # Share of requests traced (azure.md rule 16).
    otel_sampling_ratio: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0
    # Client id of the service's user-assigned identity; unset on a developer machine.
    azure_client_id: str | None = None

    @field_validator(
        "applicationinsights_connection_string", "azure_client_id", mode="before"
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # A variable that is present but empty or blank means "not configured".
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(text, str) and not text.strip():
            return None
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build the settings once per process."""
    return Settings()
