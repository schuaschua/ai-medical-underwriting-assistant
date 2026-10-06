"""The one settings object of the `intake` service (coding-style rule 12)."""

from functools import lru_cache
from typing import Annotated, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The Dapr app id; also the service name telemetry is reported under, and the
# name of the one database schema the service owns (spine AD-4).
APP_ID = "intake"
SCHEMA = APP_ID
# Liveness: the process answers. Kept out of traces, like the readiness route.
HEALTH_PATH = "/health"
# Readiness: the database is at the migration head bundled with the service.
READY_PATH = "/ready"


class Settings(BaseSettings):
    """Read once from environment variables prefixed `INTAKE_`."""

    model_config = SettingsConfigDict(env_prefix="INTAKE_", extra="ignore", frozen=True)

    # Loopback by default; the container image sets INTAKE_HOST to listen on all interfaces.
    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 8001

    # PostgreSQL. The defaults are the container in compose.yaml, which has no password.
    database_host: str = "127.0.0.1"
    database_port: Annotated[int, Field(ge=1, le=65535)] = 5432
    database_name: str = "aiuw"
    # In Azure: the database role mapped to the service identity (AD-4).
    database_user: str = "aiuw"
    # In Azure the password is an Entra token for the service identity, and TLS is required.
    database_entra_auth: bool = False
    # No database call waits for ever: opening a connection, one statement, and
    # a free connection from the pool each have a limit.
    database_connect_timeout_seconds: Annotated[int, Field(ge=1)] = 10
    database_statement_timeout_seconds: Annotated[int, Field(ge=1)] = 30
    database_pool_timeout_seconds: Annotated[float, Field(gt=0)] = 10.0

    # Upload deadlines, shortest first, so each caller outlasts the one it calls:
    #   intake 90 s (this setting)  <  web 120 s (WEB_UPLOAD_TIMEOUT_SECONDS)
    #   <  browser 150 s (UPLOAD_TIMEOUT_MS in the SPA's api/client.ts).
    # When this one passes, the stored original is undone and the call fails.
    upload_deadline_seconds: Annotated[float, Field(gt=0)] = 90.0

    # Blob Storage. In Azure: the account's blob endpoint, reached with the service identity.
    blob_account_url: str | None = None
    # The local emulator only: it cannot check an Entra token over plain HTTP.
    # Never set in Azure, where key access to the account is off.
    blob_connection_string: SecretStr | None = None
    # AD-21: the uploaded originals; no API serves them.
    originals_container: str = "originals"
    # AD-21: redacted PDFs and thumbnails, under the prefix `<case_id>/` (story 1.7).
    cases_container: str = "cases"

    # Telemetry is exported only when a connection string is set. It is an address,
    # not a credential, but it is still kept out of logs and reprs.
    applicationinsights_connection_string: SecretStr | None = None
    # Share of requests traced (azure.md rule 16).
    otel_sampling_ratio: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0
    # Client id of the service's user-assigned identity; unset on a developer machine.
    azure_client_id: str | None = None

    @field_validator(
        "applicationinsights_connection_string",
        "azure_client_id",
        "blob_account_url",
        "blob_connection_string",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # A variable that is present but empty or blank means "not configured".
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(text, str) and not text.strip():
            return None
        return value

    @model_validator(mode="after")
    def _one_way_to_blob_storage(self) -> Self:
        # With both set, one would win silently: the emulator's setting in
        # Azure, or the identity on a developer machine.
        if (
            self.blob_account_url is not None
            and self.blob_connection_string is not None
        ):
            raise ValueError(
                "Set INTAKE_BLOB_ACCOUNT_URL or INTAKE_BLOB_CONNECTION_STRING, not both."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build the settings once per process."""
    return Settings()
