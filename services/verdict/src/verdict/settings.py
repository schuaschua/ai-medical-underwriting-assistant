"""The one settings object of the `verdict` service (coding-style rule 12)."""

from functools import lru_cache
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from contracts.enums import RetrieverConfig
from contracts.models.retrieval import DEFAULT_TOP_K, MAX_TOP_K
from verdict.domain.run import (
    DEFAULT_AVAILABLE_RETRIEVER_CONFIGS,
    RUNNABLE_RETRIEVER_CONFIGS,
)

# The Dapr app id; also the service name telemetry is reported under, and the
# name of the one database schema the service owns (spine AD-4).
APP_ID = "verdict"
SCHEMA = APP_ID
# Liveness: the process answers. Kept out of traces, like the readiness route.
HEALTH_PATH = "/health"
# Readiness: the database is at the migration head bundled with the service.
READY_PATH = "/ready"

# The hosts a plain-HTTP model endpoint may have: the local stand-in only.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class Settings(BaseSettings):
    """Read once from environment variables prefixed `VERDICT_`."""

    model_config = SettingsConfigDict(
        env_prefix="VERDICT_", extra="ignore", frozen=True
    )

    # Loopback by default; the container image sets VERDICT_HOST to
    # listen on all interfaces.
    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 8006

    # PostgreSQL. The defaults are the container in compose.yaml, which has no password.
    database_host: str = "127.0.0.1"
    database_port: Annotated[int, Field(ge=1, le=65535)] = 5432
    database_name: str = "aiuw"
    # The role this process signs in as. The service runs as its own role (in
    # Azure the one mapped to the service identity, AD-4); migrations run as
    # the role that owns the schema.
    database_user: str = "aiuw"
    # Migrations only: the role the service runs as, which they grant its
    # rights to. Those rights leave out UPDATE and DELETE on the agent's
    # step log (AD-15).
    database_service_role: str | None = None
    # In Azure the password is an Entra token for the service identity, and TLS is required.
    database_entra_auth: bool = False
    # No database call waits for ever: opening a connection, one statement, and
    # a free connection from the pool each have a limit.
    database_connect_timeout_seconds: Annotated[int, Field(ge=1)] = 10
    database_statement_timeout_seconds: Annotated[int, Field(ge=1)] = 30
    database_pool_timeout_seconds: Annotated[float, Field(gt=0)] = 10.0

    # AD-3: the port of this service's own Dapr sidecar, on loopback, through
    # which it reads the case's facts from `extraction` and searches and reads
    # the manual's rules at `retrieval`. Dapr tells the app its port in
    # DAPR_HTTP_PORT, so that name is read as well.
    dapr_http_port: Annotated[
        int,
        Field(
            ge=1,
            le=65535,
            validation_alias=AliasChoices("VERDICT_DAPR_HTTP_PORT", "DAPR_HTTP_PORT"),
        ),
    ] = 3500
    # How long one call of a tool to `extraction` or `retrieval` may take.
    # A search has its own deadline at `retrieval`: 8 s, 20 s with row
    # `r4`, whose reranker is a chat call
    # (RETRIEVAL_SEARCH_RERANK_DEADLINE_SECONDS), and 20 s with row `r6`,
    # whose search service makes model calls of its own
    # (RETRIEVAL_SEARCH_AGENTIC_DEADLINE_SECONDS). This stays above all of
    # them, so that a run is told `retrieval`'s own answer and not a
    # time-out.
    upstream_timeout_seconds: Annotated[float, Field(gt=0)] = 25.0
    # A call that got no answer, or 408, 429, 502, 503 or 504, is sent again this
    # often, waiting this long first and twice as long each time after.
    # Then the run fails with `upstream_unavailable`.
    upstream_max_retries: Annotated[int, Field(ge=0, le=5)] = 2
    upstream_retry_seconds: Annotated[float, Field(gt=0)] = 0.5

    # AD-16: the Foundry account, reached with the service identity. No
    # default: in Azure the `app` stack sets the account's endpoint, and on a
    # developer machine dapr.yaml names the local stand-in, a dev tool outside
    # the services.
    model_endpoint: str | None = None
    # In Azure: sign in to the model deployment with the service identity. There is no key.
    model_entra_auth: bool = False
    # AD-16: the name of the shared chat deployment the agent runs on. It
    # reaches code only here, and it is the model the audit trail names as
    # the actor (AD-8).
    chat_deployment: str | None = None
    # How long one call to the model may take.
    model_timeout_seconds: Annotated[float, Field(gt=0)] = 35.0
    # AD-16: a call answered 429 or 5xx is sent again this often, then the
    # model is `model_unavailable`.
    model_max_retries: Annotated[int, Field(ge=0, le=10)] = 3
    # The wait before the first retry when the answer names none
    # (`Retry-After`); it doubles with every further retry, less a random
    # part. And the longest wait there is, asked for or not: a longer one
    # would outlast the stage.
    model_retry_seconds: Annotated[float, Field(gt=0)] = 1.0
    model_max_retry_seconds: Annotated[float, Field(gt=0)] = 10.0
    # The most tokens one answer of the model may take: a turn's tool calls,
    # or the final answer with its reasons. The rest is room for a model
    # that reasons before it answers, whose reasoning counts here too.
    model_max_completion_tokens: Annotated[int, Field(ge=16)] = 4000
    # The most calls to the model under way at once in the whole process,
    # however many runs are under way. The chat deployment's tokens a
    # minute are shared with classification and extraction (AD-16).
    model_max_concurrent_calls: Annotated[int, Field(ge=1, le=100)] = 5

    # AD-6: the stage ends its own work after this long, as failed
    # (`stage_timeout`): the facts, every call of the agent and of its tools,
    # and the storing of the result together. `workflow` waits 200 s
    # (WORKFLOW_STAGE_TIMEOUT_SECONDS).
    run_deadline_seconds: Annotated[float, Field(gt=0)] = 180.0
    # A run still `running` this long after its deadline was left behind by
    # a process that died: the next command for it settles it as failed.
    run_stale_margin_seconds: Annotated[float, Field(ge=0)] = 60.0
    # AD-15: the most tool calls one run may make. A run that would make one
    # more is stopped, and its case referred (`step_limit`).
    step_limit: Annotated[int, Field(ge=1, le=200)] = 30
    # AD-15: how long the agent may work in all, model calls and tool calls
    # together. When it is spent the agent is stopped and the case referred
    # (`step_limit`), as at the step limit. It must leave the stage time to
    # store that before its own deadline.
    agent_time_budget_seconds: Annotated[float, Field(gt=0)] = 150.0
    # AD-15: a run whose agent is less confident than this refers its case
    # (`low_confidence`). At the floor it is not low.
    confidence_floor: Annotated[float, Field(ge=0.0, le=1.0)] = 0.70
    # AD-11: the ladder rows a verdict may be commanded with: the ones
    # `retrieval` answers in this environment. `r5` is named only where
    # `retrieval` was given a search service
    # (RETRIEVAL_SEARCH_SERVICE_ENDPOINT), `r4` only where it was given
    # the chat deployment, its reranker (RETRIEVAL_CHAT_DEPLOYMENT), and
    # `r6` only where it was given both;
    # `workflow` names the same rows
    # (WORKFLOW_AVAILABLE_RETRIEVER_CONFIGS). A command with another row is
    # refused with `retriever_not_available` before anything is done.
    available_retriever_configs: Annotated[
        list[RetrieverConfig], Field(min_length=1)
    ] = sorted(DEFAULT_AVAILABLE_RETRIEVER_CONFIGS)
    # How many rules one `search_rules` call returns to the agent.
    search_top_k: Annotated[int, Field(ge=1, le=MAX_TOP_K)] = DEFAULT_TOP_K
    # How many runs, and how many steps, one read lists at most.
    run_list_limit: Annotated[int, Field(ge=1, le=1000)] = 50
    step_list_limit: Annotated[int, Field(ge=1, le=5000)] = 500

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
        "chat_deployment",
        "database_service_role",
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
            raise ValueError("VERDICT_CHAT_DEPLOYMENT must not be padded")
        return value

    @model_validator(mode="after")
    def _model_is_reached_safely(self) -> Self:
        if self.model_endpoint is None:
            return self
        endpoint = urlsplit(self.model_endpoint)
        if endpoint.scheme not in ("http", "https") or not endpoint.hostname:
            raise ValueError(
                "VERDICT_MODEL_ENDPOINT must start with http:// or https://"
            )
        if endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment:
            # The API's own path is added to it: anything after the host
            # would end up in the middle of the address that is called.
            raise ValueError(
                "VERDICT_MODEL_ENDPOINT must be the account's endpoint "
                "alone, without a path, a query or a fragment"
            )
        if endpoint.scheme == "http":
            # Plain HTTP is the local stand-in. It never stands in for the
            # model in Azure, and a token is never sent to it.
            if endpoint.hostname not in _LOOPBACK_HOSTS or self.model_entra_auth:
                raise ValueError(
                    "A plain-HTTP VERDICT_MODEL_ENDPOINT is the local "
                    "stand-in: it must be on loopback, without "
                    "VERDICT_MODEL_ENTRA_AUTH"
                )
        elif not self.model_entra_auth:
            # Security rule 9: the real deployment is reached with the identity.
            raise ValueError(
                "An https:// VERDICT_MODEL_ENDPOINT needs VERDICT_MODEL_ENTRA_AUTH=true"
            )
        return self

    @model_validator(mode="after")
    def _rows_are_ones_this_build_runs(self) -> Self:
        configs = self.available_retriever_configs
        if len(set(configs)) != len(configs):
            raise ValueError(
                "VERDICT_AVAILABLE_RETRIEVER_CONFIGS must not repeat a value"
            )
        if not set(configs) <= RUNNABLE_RETRIEVER_CONFIGS:
            # Every row of today's ladder can be run. A row the contracts
            # gain later cannot, until `verdict` knows how to run it.
            raise ValueError(
                "VERDICT_AVAILABLE_RETRIEVER_CONFIGS names a row this build cannot "
                "run a verdict with"
            )
        return self

    @model_validator(mode="after")
    def _waits_fit_together(self) -> Self:
        if self.model_retry_seconds > self.model_max_retry_seconds:
            raise ValueError(
                "VERDICT_MODEL_RETRY_SECONDS must not be longer than "
                "VERDICT_MODEL_MAX_RETRY_SECONDS"
            )
        return self

    @property
    def model_worst_case_seconds(self) -> float:
        """The longest one call of the model can take, retries and waits included."""
        attempts = self.model_max_retries + 1
        return (
            attempts * self.model_timeout_seconds
            + self.model_max_retries * self.model_max_retry_seconds
        )

    @model_validator(mode="after")
    def _model_fits_the_deadline(self) -> Self:
        # AD-6: one call of a model that is slow and throttled must end as
        # `model_unavailable`, by the gateway, before the stage's own
        # deadline ends the run as `stage_timeout`. A run makes several
        # calls: all of them together are held by the deadline alone.
        if self.model_worst_case_seconds >= self.run_deadline_seconds:
            raise ValueError(
                "The model's attempts and waits do not fit the stage deadline: "
                "(VERDICT_MODEL_MAX_RETRIES + 1) x VERDICT_MODEL_TIMEOUT_SECONDS "
                "+ VERDICT_MODEL_MAX_RETRIES x VERDICT_MODEL_MAX_RETRY_SECONDS "
                f"is {self.model_worst_case_seconds:g} s, which must be under "
                f"VERDICT_RUN_DEADLINE_SECONDS ({self.run_deadline_seconds:g} s)"
            )
        return self

    @model_validator(mode="after")
    def _the_agents_budget_is_inside_the_deadline(self) -> Self:
        if self.agent_time_budget_seconds + 10 > self.run_deadline_seconds:
            raise ValueError(
                "VERDICT_AGENT_TIME_BUDGET_SECONDS must be at least 10 s under "
                f"VERDICT_RUN_DEADLINE_SECONDS ({self.run_deadline_seconds:g} s)"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build the settings once per process."""
    return Settings()
