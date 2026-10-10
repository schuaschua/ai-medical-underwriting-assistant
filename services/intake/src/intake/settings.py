"""The one settings object of the `intake` service (coding-style rule 12)."""

import re
from functools import lru_cache
from typing import Annotated, Self
from urllib.parse import urlsplit

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

# AD-21: what redaction removes unless a setting says otherwise, by the names
# Azure AI Language reports them under: person names, addresses, phone
# numbers, email addresses, identity numbers and policy numbers. Dates, ages
# and medical terms are not in the list, so they are kept. `PolicyNumber` is
# this project's name for a category the service may not know: see the Azure
# checks in `_bmad-output/implementation-artifacts/deferred-work.md`.
DEFAULT_REDACTION_CATEGORIES = (
    "Person",
    "Address",
    "PhoneNumber",
    "Email",
    "USSocialSecurityNumber",
    "PolicyNumber",
)
# The hosts a plain-HTTP Language or read endpoint may have: a local stand-in only.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


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

    # AD-21: Azure AI Language, reached over REST. No default: in Azure the
    # `app` stack sets the account's endpoint, and on a developer machine
    # dapr.yaml names the local stand-in, a dev tool outside the services.
    language_endpoint: str | None = None
    # Spine, open question: 2026-05-01 or a preview version; confirmed in Azure.
    language_api_version: str = "2026-05-01"
    # In Azure: sign in to Language with the service identity. There is no key.
    language_entra_auth: bool = False
    # How long one HTTP call to Language may take, and the wait between two
    # looks at a running job.
    language_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    language_poll_seconds: Annotated[float, Field(gt=0)] = 1.0
    redaction_categories: Annotated[list[str], Field(min_length=1)] = list(
        DEFAULT_REDACTION_CATEGORIES
    )
    # AD-14: Document Intelligence's read model, reached over REST. The
    # redaction service writes each page of the redacted PDF as one picture,
    # so the page text and the word places are read from that PDF by OCR
    # (owner's decision of 2026-10-10). No default: in Azure the `app` stack
    # sets the account's endpoint, and dapr.yaml names the local stand-in.
    read_endpoint: str | None = None
    # In Azure: sign in with the service identity. There is no key.
    read_entra_auth: bool = False
    read_api_version: str = "2024-11-30"
    # The model's id is part of the request path.
    read_model: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,63}$")] = (
        "prebuilt-read"
    )
    # How long one HTTP call may take (the submit carries the whole PDF), the
    # wait between two looks at a running analysis, and how often a submit
    # that got no answer is sent again. The redaction's deadline bounds it all.
    read_timeout_seconds: Annotated[float, Field(gt=0)] = 60.0
    read_poll_seconds: Annotated[float, Field(gt=0)] = 1.0
    read_max_retries: Annotated[int, Field(ge=0, le=10)] = 3
    # AD-6: the stage ends its own work after this long, as failed
    # (`stage_timeout`). `workflow` waits 200 s (WORKFLOW_STAGE_TIMEOUT_SECONDS).
    redaction_deadline_seconds: Annotated[float, Field(gt=0)] = 180.0
    # A redaction still `running` this long after its deadline was left behind
    # by a process that died: the next command for the case settles it as failed.
    redaction_stale_margin_seconds: Annotated[float, Field(ge=0)] = 60.0
    # After the deadline, how long the failure path waits for the job's
    # cancel: deadline plus this stays under `workflow`'s 200 s.
    redaction_cancel_seconds: Annotated[float, Field(gt=0, le=15)] = 10.0
    # Width of a page thumbnail, in pixels, and the most it may be high: a
    # very long page is made narrower instead.
    thumbnail_width_px: Annotated[int, Field(ge=16, le=2000)] = 320
    thumbnail_max_height_px: Annotated[int, Field(ge=16, le=4000)] = 1280
    # A redacted document of more pages is not split: the redaction fails.
    max_pages: Annotated[int, Field(ge=1)] = 200

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
        "language_endpoint",
        "read_endpoint",
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

    @field_validator("redaction_categories")
    @classmethod
    def _categories_are_names(cls, value: list[str]) -> list[str]:
        # ASCII only: `str.isalnum` would let letters of any script through.
        if any(re.fullmatch(r"[A-Za-z0-9]+", name) is None for name in value) or len(
            set(value)
        ) != len(value):
            raise ValueError(
                "redaction_categories must be distinct category names of "
                "letters and digits"
            )
        return value

    @model_validator(mode="after")
    def _ai_services_are_reached_safely(self) -> Self:
        if self.language_endpoint is not None and self.read_endpoint is None:
            # A redacted PDF has no text but its masks' labels: without the
            # read model no page would have its text.
            raise ValueError(
                "Set INTAKE_READ_ENDPOINT with INTAKE_LANGUAGE_ENDPOINT: the "
                "Document Intelligence account, or the local stand-in, reads "
                "the page text from each redacted PDF"
            )
        for name, address, entra_auth in (
            ("LANGUAGE", self.language_endpoint, self.language_entra_auth),
            ("READ", self.read_endpoint, self.read_entra_auth),
        ):
            if address is not None:
                _reached_safely(name, address, entra_auth)
        return self


def _reached_safely(name: str, address: str, entra_auth: bool) -> None:
    """Refuse an AI service endpoint that is neither the real account nor a local stand-in."""
    endpoint = urlsplit(address)
    if endpoint.scheme not in ("http", "https") or not endpoint.hostname:
        raise ValueError(f"INTAKE_{name}_ENDPOINT must start with http:// or https://")
    if endpoint.scheme == "http":
        # Plain HTTP is the local stand-in. It never stands in for the
        # service in Azure, and a token is never sent to it.
        if endpoint.hostname not in _LOOPBACK_HOSTS or entra_auth:
            raise ValueError(
                f"A plain-HTTP INTAKE_{name}_ENDPOINT is the local stand-in: "
                f"it must be on loopback, without INTAKE_{name}_ENTRA_AUTH"
            )
    elif not entra_auth:
        # Security rule 9: the real service is reached with the identity.
        raise ValueError(
            f"An https:// INTAKE_{name}_ENDPOINT needs INTAKE_{name}_ENTRA_AUTH=true"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build the settings once per process."""
    return Settings()
