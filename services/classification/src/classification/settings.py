"""The one settings object of the `classification` service (coding-style rule 12)."""

from functools import lru_cache
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# The Dapr app id; also the service name telemetry is reported under, and the
# name of the one database schema the service owns (spine AD-4).
APP_ID = "classification"
SCHEMA = APP_ID
# Liveness: the process answers. Kept out of traces, like the readiness route.
HEALTH_PATH = "/health"
# Readiness: the database is at the migration head bundled with the service.
READY_PATH = "/ready"

# The hosts a plain-HTTP model endpoint may have: the local stand-in only.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# A classifier id as Document Intelligence takes one.
_CLASSIFIER_ID = r"^[A-Za-z0-9][A-Za-z0-9._~-]{1,63}$"


def _reached_safely(name: str, value: str, entra_auth: bool) -> None:
    """Refuse an endpoint a token could leak to, or one that is not the service's alone."""
    endpoint = urlsplit(value)
    if endpoint.scheme not in ("http", "https") or not endpoint.hostname:
        raise ValueError(
            f"CLASSIFICATION_{name}_ENDPOINT must start with http:// or https://"
        )
    if endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment:
        # The API's own path is added to it: anything after the host
        # would end up in the middle of the address that is called.
        raise ValueError(
            f"CLASSIFICATION_{name}_ENDPOINT must be the account's endpoint "
            "alone, without a path, a query or a fragment"
        )
    if endpoint.scheme == "http":
        # Plain HTTP is the local stand-in. It never stands in for the
        # service in Azure, and a token is never sent to it.
        if endpoint.hostname not in LOOPBACK_HOSTS or entra_auth:
            raise ValueError(
                f"A plain-HTTP CLASSIFICATION_{name}_ENDPOINT is the local "
                "stand-in: it must be on loopback, without "
                f"CLASSIFICATION_{name}_ENTRA_AUTH"
            )
    elif not entra_auth:
        # Security rule 9: the real service is reached with the identity.
        raise ValueError(
            f"An https:// CLASSIFICATION_{name}_ENDPOINT needs "
            f"CLASSIFICATION_{name}_ENTRA_AUTH=true"
        )


class Settings(BaseSettings):
    """Read once from environment variables prefixed `CLASSIFICATION_`."""

    model_config = SettingsConfigDict(
        env_prefix="CLASSIFICATION_", extra="ignore", frozen=True
    )

    # Loopback by default; the container image sets CLASSIFICATION_HOST to
    # listen on all interfaces.
    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 8003

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

    # AD-3: the port of this service's own Dapr sidecar, on loopback, through
    # which it reads a page from `intake`. Dapr tells the app its port in
    # DAPR_HTTP_PORT, so that name is read as well.
    dapr_http_port: Annotated[
        int,
        Field(
            ge=1,
            le=65535,
            validation_alias=AliasChoices(
                "CLASSIFICATION_DAPR_HTTP_PORT", "DAPR_HTTP_PORT"
            ),
        ),
    ] = 3500
    # How long one read from `intake` may take.
    intake_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0

    # AD-16: the Foundry account, reached with the service identity. No
    # default: in Azure the `app` stack sets the account's endpoint, and on a
    # developer machine dapr.yaml names the local stand-in, a dev tool outside
    # the services.
    model_endpoint: str | None = None
    # In Azure: sign in to the model deployment with the service identity. There is no key.
    model_entra_auth: bool = False
    # AD-16: the name of the shared chat deployment. It reaches code only
    # here, and it is the model the audit trail names as the actor (AD-8).
    chat_deployment: str | None = None
    # How long one call to the model may take.
    model_timeout_seconds: Annotated[float, Field(gt=0)] = 60.0
    # AD-16: a call answered 429 or 5xx is sent again this often, then the
    # model is `model_unavailable`.
    model_max_retries: Annotated[int, Field(ge=0, le=10)] = 3
    # The wait before the first retry when the answer names none
    # (`Retry-After`); it doubles with every further retry, less a random
    # part. And the longest wait there is, asked for or not: a longer one
    # would outlast the stage.
    model_retry_seconds: Annotated[float, Field(gt=0)] = 1.0
    model_max_retry_seconds: Annotated[float, Field(gt=0)] = 30.0
    # The most tokens one answer may take. The answer is a page type and a
    # one-line reason; the rest is room for a model that reasons before it
    # answers, whose reasoning counts here too.
    model_max_completion_tokens: Annotated[int, Field(ge=16)] = 2000
    # The most calls to the model under way at once in the whole process,
    # however many pages are being classified.
    model_max_concurrent_calls: Annotated[int, Field(ge=1, le=100)] = 10

    # AD-13: the LLM contender's confidence is the agreement rate across this
    # many runs of the model on the same page.
    classifier_runs: Annotated[int, Field(ge=1, le=25)] = 5
    # How many of those runs are under way at once.
    classifier_max_concurrent_runs: Annotated[int, Field(ge=1, le=25)] = 5
    # AD-6: the stage ends its own work after this long, as failed
    # (`stage_timeout`): all runs of one page together. `workflow` waits
    # 200 s (WORKFLOW_STAGE_TIMEOUT_SECONDS).
    classify_deadline_seconds: Annotated[float, Field(gt=0)] = 180.0
    # A classification still `running` this long after its deadline was left
    # behind by a process that died: the next command for it settles it as failed.
    classify_stale_margin_seconds: Annotated[float, Field(ge=0)] = 60.0

    # AD-13, story 4.2: the second contender, a custom classification model
    # of Azure AI Document Intelligence. It is available where both the
    # endpoint and the classifier id are set; elsewhere a command that names
    # `doc-intelligence` is refused and `llm` works as before. No default
    # endpoint: in Azure the `app` stack sets the account's, and on a
    # developer machine dapr.yaml names the local stand-in.
    doc_intelligence_endpoint: str | None = None
    # In Azure: sign in to Document Intelligence with the service identity. There is no key.
    doc_intelligence_entra_auth: bool = False
    doc_intelligence_api_version: Annotated[
        str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}(?:-preview)?$")
    ] = "2024-11-30"
    # The id of the classifier the training job builds and a classification
    # asks. It is also what the audit trail names as the actor (AD-8). A
    # retrained classifier is a new id.
    doc_intelligence_classifier_id: (
        Annotated[str, StringConstraints(pattern=_CLASSIFIER_ID)] | None
    ) = None
    # How long one HTTP call may take, how often a call answered 429 or 5xx
    # (or not at all) is sent again, and how long the wait between two
    # looks at an analysis or at the build is when the service names none.
    doc_intelligence_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    doc_intelligence_max_retries: Annotated[int, Field(ge=0, le=10)] = 3
    doc_intelligence_poll_seconds: Annotated[float, Field(gt=0)] = 1.0

    # The training job (`python -m classification.train`): Blob Storage,
    # where the labelled, redacted training pages are. In Azure: the
    # account's blob endpoint, reached with the service identity.
    blob_account_url: str | None = None
    # The local emulator only. Azure has key access off, so this is never
    # set there.
    blob_connection_string: SecretStr | None = None
    # The container `classification` owns (AD-4).
    training_container: str = "classifier-training"
    # The job gives up after this long, and says so. Building a classifier
    # takes the service minutes.
    training_deadline_seconds: Annotated[float, Field(gt=0)] = 1800.0
    # How often the job looks at a build that is under way.
    training_poll_seconds: Annotated[float, Field(gt=0)] = 5.0

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
        "chat_deployment",
        "doc_intelligence_classifier_id",
        "doc_intelligence_endpoint",
        "model_endpoint",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # A variable that is present but empty or blank means "not configured".
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(text, str) and not text.strip():
            return None
        return value

    @field_validator("chat_deployment")
    @classmethod
    def _deployment_is_a_name(cls, value: str | None) -> str | None:
        # AD-8: the name is part of the audit actor, which takes no padded name.
        if value is not None and value != value.strip():
            raise ValueError("CLASSIFICATION_CHAT_DEPLOYMENT must not be padded")
        return value

    @field_validator("training_container")
    @classmethod
    def _named(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("must be a name, without padding")
        return value

    @model_validator(mode="after")
    def _services_are_reached_safely(self) -> Self:
        if self.model_endpoint is not None:
            _reached_safely("MODEL", self.model_endpoint, self.model_entra_auth)
        if self.doc_intelligence_endpoint is not None:
            _reached_safely(
                "DOC_INTELLIGENCE",
                self.doc_intelligence_endpoint,
                self.doc_intelligence_entra_auth,
            )
        return self

    @model_validator(mode="after")
    def _one_way_to_blob_storage(self) -> Self:
        if (
            self.blob_account_url is not None
            and self.blob_connection_string is not None
        ):
            raise ValueError(
                "Set CLASSIFICATION_BLOB_ACCOUNT_URL or "
                "CLASSIFICATION_BLOB_CONNECTION_STRING, not both."
            )
        if self.blob_account_url is not None:
            # The account is reached with the service identity, and its
            # address is handed to Document Intelligence: over TLS, the
            # account's endpoint alone, and never an address that carries a
            # signature, a key or a sign-in of its own.
            account = urlsplit(self.blob_account_url)
            if (
                account.scheme != "https"
                or not account.hostname
                or account.username is not None
                or account.path not in ("", "/")
                or account.query
                or account.fragment
            ):
                raise ValueError(
                    "CLASSIFICATION_BLOB_ACCOUNT_URL must be the storage "
                    "account's https:// blob endpoint alone, without a path, "
                    "a query or a fragment"
                )
        return self

    @model_validator(mode="after")
    def _runs_fit_together(self) -> Self:
        if self.model_retry_seconds > self.model_max_retry_seconds:
            raise ValueError(
                "CLASSIFICATION_MODEL_RETRY_SECONDS must not be longer than "
                "CLASSIFICATION_MODEL_MAX_RETRY_SECONDS"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build the settings once per process."""
    return Settings()
