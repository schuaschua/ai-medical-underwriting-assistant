"""The one settings object of the `retrieval` service and its ingestion job (coding-style rule 12)."""

from functools import lru_cache
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from contracts.enums import ChunkSet
from contracts.models.retrieval import MAX_TOP_K

# The Dapr app id; also the service name telemetry is reported under, and the
# name of the one database schema the service owns (spine AD-4).
APP_ID = "retrieval"
SCHEMA = APP_ID
# Liveness: the process answers. Kept out of traces, like the readiness route.
HEALTH_PATH = "/health"
# Readiness: the database is at the migration head bundled with the service.
READY_PATH = "/ready"

# The hosts a plain-HTTP endpoint may have: a local stand-in only.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _reached_safely(name: str, value: str, entra_auth: bool) -> None:
    """Refuse an endpoint that is not an account's own, or a stand-in outside this machine."""
    endpoint = urlsplit(value)
    if endpoint.scheme not in ("http", "https") or not endpoint.hostname:
        raise ValueError(
            f"RETRIEVAL_{name}_ENDPOINT must start with http:// or https://"
        )
    if endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment:
        # The API's own path is added to it: anything after the host would
        # end up in the middle of the address that is called.
        raise ValueError(
            f"RETRIEVAL_{name}_ENDPOINT must be the account's endpoint alone, "
            "without a path, a query or a fragment"
        )
    if endpoint.scheme == "http":
        # Plain HTTP is a local stand-in. It never stands in for the service
        # in Azure, and a token is never sent to it.
        if endpoint.hostname not in _LOOPBACK_HOSTS or entra_auth:
            raise ValueError(
                f"A plain-HTTP RETRIEVAL_{name}_ENDPOINT is the local stand-in: "
                f"it must be on loopback, without RETRIEVAL_{name}_ENTRA_AUTH"
            )
    elif not entra_auth:
        # Security rule 9: the real service is reached with the identity.
        raise ValueError(
            f"An https:// RETRIEVAL_{name}_ENDPOINT needs "
            f"RETRIEVAL_{name}_ENTRA_AUTH=true"
        )


class Settings(BaseSettings):
    """Read once from environment variables prefixed `RETRIEVAL_`."""

    model_config = SettingsConfigDict(
        env_prefix="RETRIEVAL_", extra="ignore", frozen=True
    )

    # Loopback by default; the container image sets RETRIEVAL_HOST to listen
    # on all interfaces.
    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 8004

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

    # Blob Storage, where the manual PDF is. In Azure: the account's blob
    # endpoint, reached with the service identity.
    blob_account_url: str | None = None
    # The local emulator only. Azure has key access off, so there is never a
    # connection string there.
    blob_connection_string: SecretStr | None = None
    # AD-4: the container `retrieval` owns, and the manual in it.
    manual_container: str = "manual"
    manual_blob_name: str = "underwriting-manual.pdf"

    # AD-12: Document Intelligence, whose layout model parses the manual. No
    # default: in Azure the `app` stack sets the account's endpoint, and on a
    # developer machine tools/ingest-local.sh names the local stand-in, a dev
    # tool outside the services.
    layout_endpoint: str | None = None
    # In Azure: sign in to Document Intelligence with the service identity. There is no key.
    layout_entra_auth: bool = False
    layout_api_version: str = "2024-11-30"
    layout_model: str = "prebuilt-layout"
    # How long one HTTP call to Document Intelligence may take, the wait
    # between two looks at a running analysis, and how long the analysis of
    # the whole manual may take before the job gives up.
    layout_timeout_seconds: Annotated[float, Field(gt=0)] = 60.0
    layout_poll_seconds: Annotated[float, Field(gt=0)] = 2.0
    layout_deadline_seconds: Annotated[float, Field(gt=0)] = 600.0
    # A submit answered 429 or 5xx, or not at all, is sent again this often.
    layout_max_retries: Annotated[int, Field(ge=0, le=10)] = 3

    # AD-16: the Foundry account, reached with the service identity. No
    # default, as for Document Intelligence.
    model_endpoint: str | None = None
    # In Azure: sign in to the model deployments with the service identity. There is no key.
    model_entra_auth: bool = False
    # AD-16: the names of the shared chat deployment, which writes each
    # chunk's context line, and of the one embedding deployment
    # (`text-embedding-3-large`). They reach code only here.
    chat_deployment: str | None = None
    embedding_deployment: str | None = None
    # How long one call to a model may take.
    model_timeout_seconds: Annotated[float, Field(gt=0)] = 60.0
    # AD-16: a call answered 429 or 5xx is sent again this often, then the
    # model is `model_unavailable`.
    model_max_retries: Annotated[int, Field(ge=0, le=10)] = 3
    # The wait before the first retry when the answer names none
    # (`Retry-After`); it doubles with every further retry, less a random
    # part. And the longest wait there is, asked for or not.
    model_retry_seconds: Annotated[float, Field(gt=0)] = 1.0
    model_max_retry_seconds: Annotated[float, Field(gt=0)] = 30.0
    # The most tokens one context line's answer may take: one line, and room
    # for a model that reasons before it answers.
    model_max_completion_tokens: Annotated[int, Field(ge=16)] = 2000
    # The most calls to the models under way at once in the whole process.
    # The chat deployment is shared with the other services.
    model_max_concurrent_calls: Annotated[int, Field(ge=1, le=100)] = 5
    # How many chunks one embedding call carries.
    embedding_batch_size: Annotated[int, Field(ge=1, le=256)] = 16

    # AD-12: a context line longer than this is refused, not cut.
    context_line_max_chars: Annotated[int, Field(ge=40, le=2000)] = 300
    # The job gives up after this long, and leaves the index as it was. The
    # one transaction that stores a finished run is not cut off by it.
    ingest_deadline_seconds: Annotated[float, Field(gt=0)] = 1800.0
    # A run after which more than this share of the rules a chunk set
    # defines would be defined by none of its chunks is refused: a manual
    # that was parsed badly looks like one that lost its rules. The second
    # names the chunk sets for which the one run of a manual that really
    # lost them is let through (`["smart"]`, `["fixed"]` or both).
    ingest_max_removed_share: Annotated[float, Field(ge=0.0, le=1.0)] = 0.1
    ingest_allow_large_removal: list[ChunkSet] = []
    # AD-11: the chunk sets the job writes, in this order, each as a run of
    # its own. `smart` is what rows `r2` to `r6` read, `fixed` what `r1` reads.
    ingest_chunk_sets: Annotated[list[ChunkSet], Field(min_length=1)] = [
        ChunkSet.SMART,
        ChunkSet.FIXED,
    ]
    # Row `r1`: how many words a `fixed` chunk holds, and how many of them it
    # shares with the chunk before it. They are part of what a run records:
    # a change cuts the `fixed` set again. The overlap is at least one word:
    # without any, a cut that falls inside a definition marker leaves that
    # rule in no chunk, and the run fails.
    fixed_chunk_words: Annotated[int, Field(ge=20, le=2000)] = 350
    fixed_chunk_overlap_words: Annotated[int, Field(ge=1, le=1000)] = 35

    # AD-11, row `r3`: how many chunks the vector search and the full-text
    # search each hand to the fusion. Never fewer than the most items a search
    # may ask for, so each side's list is at least as deep as any `top_k`.
    search_candidate_depth: Annotated[int, Field(ge=MAX_TOP_K, le=1000)] = 50
    # A search is a call someone waits for, several times per fact: it has a
    # short budget of its own, not the ingestion job's. How long the one
    # embedding call of a query may take, how often it is sent again when it
    # is answered 429 or 5xx, and the deadline over the whole search.
    search_embedding_timeout_seconds: Annotated[float, Field(gt=0)] = 3.0
    search_embedding_max_retries: Annotated[int, Field(ge=0, le=10)] = 1
    search_deadline_seconds: Annotated[float, Field(gt=0)] = 8.0

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
        "embedding_deployment",
        "layout_endpoint",
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

    @field_validator("chat_deployment", "embedding_deployment")
    @classmethod
    def _deployment_is_a_name(cls, value: str | None) -> str | None:
        # The names are stored with every chunk and compared on the next run.
        if value is not None and value != value.strip():
            raise ValueError("a deployment name must not be padded")
        return value

    @field_validator("manual_container", "manual_blob_name")
    @classmethod
    def _named(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("must be a name, without padding")
        return value

    @model_validator(mode="after")
    def _one_way_to_blob_storage(self) -> Self:
        if (
            self.blob_account_url is not None
            and self.blob_connection_string is not None
        ):
            raise ValueError(
                "Set RETRIEVAL_BLOB_ACCOUNT_URL or RETRIEVAL_BLOB_CONNECTION_STRING, "
                "not both."
            )
        return self

    @model_validator(mode="after")
    def _services_are_reached_safely(self) -> Self:
        if self.model_endpoint is not None:
            _reached_safely("MODEL", self.model_endpoint, self.model_entra_auth)
        if self.layout_endpoint is not None:
            _reached_safely("LAYOUT", self.layout_endpoint, self.layout_entra_auth)
        return self

    @model_validator(mode="after")
    def _the_fixed_cut_moves_on(self) -> Self:
        if len(set(self.ingest_chunk_sets)) != len(self.ingest_chunk_sets):
            raise ValueError("RETRIEVAL_INGEST_CHUNK_SETS must not repeat a value")
        if self.fixed_chunk_overlap_words * 2 > self.fixed_chunk_words:
            # Each chunk must bring more new words than it repeats.
            raise ValueError(
                "RETRIEVAL_FIXED_CHUNK_OVERLAP_WORDS must not be more than half of "
                "RETRIEVAL_FIXED_CHUNK_WORDS"
            )
        return self

    @model_validator(mode="after")
    def _waits_fit_together(self) -> Self:
        if self.search_embedding_timeout_seconds > self.search_deadline_seconds:
            raise ValueError(
                "RETRIEVAL_SEARCH_EMBEDDING_TIMEOUT_SECONDS must not be longer than "
                "RETRIEVAL_SEARCH_DEADLINE_SECONDS"
            )
        if self.model_retry_seconds > self.model_max_retry_seconds:
            raise ValueError(
                "RETRIEVAL_MODEL_RETRY_SECONDS must not be longer than "
                "RETRIEVAL_MODEL_MAX_RETRY_SECONDS"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build the settings once per process."""
    return Settings()
