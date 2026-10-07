"""The one settings object of the `workflow` service (coding-style rule 12)."""

from functools import lru_cache
from typing import Annotated, Self

from pydantic import (
    AliasChoices,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from contracts.enums import ClassifierContender, RetrieverConfig
from workflow.domain.case_list import DEFAULT_CASE_LIST_LIMIT
from workflow.domain.cases import DEFAULT_AUDIT_TRAIL_LIMIT
from workflow.domain.gate import DEFAULT_GATE_THRESHOLD
from workflow.domain.queue import DEFAULT_PAGE_QUEUE_LIMIT

# The Dapr app id; also the service name telemetry is reported under, and the
# name of the one database schema the service owns (spine AD-4).
APP_ID = "workflow"
SCHEMA = APP_ID
# Liveness: the process answers. Kept out of traces, like the readiness route.
HEALTH_PATH = "/health"
# Readiness: the database is at the migration head bundled with the service.
READY_PATH = "/ready"


class Settings(BaseSettings):
    """Read once from environment variables prefixed `WORKFLOW_`."""

    model_config = SettingsConfigDict(
        env_prefix="WORKFLOW_", extra="ignore", frozen=True
    )

    # Loopback by default; the container image sets WORKFLOW_HOST to listen on all interfaces.
    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 8002

    # PostgreSQL. The defaults are the container in compose.yaml, which has no password.
    database_host: str = "127.0.0.1"
    database_port: Annotated[int, Field(ge=1, le=65535)] = 5432
    database_name: str = "aiuw"
    # The role this process signs in as. The service runs as its own role (in
    # Azure the one mapped to the service identity, AD-4); migrations run as
    # the role that owns the schema.
    database_user: str = "aiuw"
    # Migrations only: the role the service runs as, which they grant its
    # rights to. Those rights leave out UPDATE and DELETE on the audit table (AD-8).
    database_service_role: str | None = None
    # In Azure the password is an Entra token for the identity, and TLS is required.
    database_entra_auth: bool = False
    # No database call waits for ever: opening a connection, one statement, and
    # a free connection from the pool each have a limit.
    database_connect_timeout_seconds: Annotated[int, Field(ge=1)] = 10
    database_statement_timeout_seconds: Annotated[int, Field(ge=1)] = 30
    database_pool_timeout_seconds: Annotated[float, Field(gt=0)] = 10.0
    # The most connections the service holds; there is no overflow beyond it.
    database_pool_size: Annotated[int, Field(ge=1)] = 10

    # AD-5: Azure Durable Task Scheduler. The defaults are the emulator in
    # compose.yaml, which takes no credential and speaks plain HTTP on loopback.
    scheduler_endpoint: str = "http://127.0.0.1:8080"
    scheduler_task_hub: str = "default"
    # In Azure: sign in to the scheduler with the service identity.
    scheduler_entra_auth: bool = False
    # How long one call to the scheduler (start, look up) may take.
    scheduler_timeout_seconds: Annotated[float, Field(gt=0)] = 10.0

    # AD-6: an activity that fails is tried again, each wait longer than the last.
    activity_max_attempts: Annotated[int, Field(ge=1)] = 5
    activity_first_retry_seconds: Annotated[float, Field(gt=0)] = 2.0
    activity_backoff_coefficient: Annotated[float, Field(ge=1)] = 2.0
    # How long an activity may wait for its own database work.
    activity_timeout_seconds: Annotated[float, Field(gt=0)] = 60.0
    # How many activities the worker runs at once. Each one needs a database
    # connection, so this is never larger than the pool, and smaller leaves
    # connections for the HTTP routes.
    worker_max_concurrent_activities: Annotated[int, Field(ge=1)] = 5
    # The worker starts only once the schema is at the bundled migration
    # head; until then the schema is looked at again this often.
    worker_start_check_seconds: Annotated[float, Field(gt=0)] = 5.0
    # How long shutdown waits for the worker, then for the scheduler client.
    shutdown_timeout_seconds: Annotated[float, Field(gt=0)] = 40.0

    # AD-3: the port of this service's own Dapr sidecar, on loopback. Dapr tells
    # the app its port in DAPR_HTTP_PORT, so that name is read as well.
    dapr_http_port: Annotated[
        int,
        Field(
            ge=1,
            le=65535,
            validation_alias=AliasChoices("WORKFLOW_DAPR_HTTP_PORT", "DAPR_HTTP_PORT"),
        ),
    ] = 3500
    # AD-6: how long a stage command may take. A stage ends its own work after
    # 180 s (for redaction, INTAKE_REDACTION_DEADLINE_SECONDS); this is longer,
    # so the stage's answer is heard.
    stage_timeout_seconds: Annotated[float, Field(gt=0)] = 200.0
    # A stage command that fails, or is answered `in_progress`, is sent again:
    # the waits grow from `activity_first_retry_seconds` to at most this long.
    # The attempts together must outlast a stage's deadline plus the margin
    # after which the stage settles a command left `running` (for redaction
    # 180 s + INTAKE_REDACTION_STALE_MARGIN_SECONDS 60 s = 240 s): a command
    # repeated while the stage still works on it, or after the stage died,
    # then gets a result in the end. The defaults wait
    # 2 + 4 + 8 + 16 + 30 x 9 = 300 s.
    stage_max_retry_seconds: Annotated[float, Field(gt=0)] = 30.0
    stage_max_attempts: Annotated[int, Field(ge=1)] = 14

    # What a case is started with when the start request leaves a field out
    # (spine, Operations: every field of the start request is optional).
    default_classifier_contender: ClassifierContender = ClassifierContender.LLM
    # Spine, Build order: the demo path runs with `r3`.
    default_retriever_configs: Annotated[list[RetrieverConfig], Field(min_length=1)] = [
        RetrieverConfig.R3
    ]
    # AD-11: the ladder rows a case may run with in this build: the ones
    # `verdict` and `retrieval` can answer (VERDICT_* and RETRIEVAL_* hold the
    # same list for now: `r3`). A start, or a request for one more verdict
    # run, that names another row is refused with `retriever_not_available`
    # at once, not after every stage and decision.
    available_retriever_configs: Annotated[
        list[RetrieverConfig], Field(min_length=1)
    ] = [RetrieverConfig.R3]

    # AD-7: the gate's threshold. A page classified with this confidence or
    # more is sent on by its label; one below it goes to triage. It is used
    # only by `workflow`'s gate and is never sent to another service, the SPA
    # or a prompt. A case takes the value in force when its lifecycle
    # confirms it and keeps it in its history: a change applies to cases
    # started after it, and a case in flight goes on with the value it has.
    gate_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = DEFAULT_GATE_THRESHOLD

    # AD-5: a decision is stored first and its orchestration told after. The
    # service itself looks, this often, for stored decisions that carry no
    # mark of having been told, and tells them: a waiting case does not
    # depend on the browser repeating the decision. A timer of the service,
    # never of the orchestration.
    decision_tell_interval_seconds: Annotated[float, Field(gt=0)] = 15.0
    # A decision younger than this is left alone: the request that stored it
    # is still telling it.
    decision_tell_grace_seconds: Annotated[float, Field(ge=0)] = 30.0
    # No decision is ever given up. After a look that failed (the scheduler
    # or the database could not be reached) the wait before the next look
    # doubles, up to this long; a look that worked brings it back to the
    # interval above.
    decision_tell_max_interval_seconds: Annotated[float, Field(gt=0)] = 300.0
    # How many decisions one look takes at most.
    decision_tell_batch_size: Annotated[int, Field(ge=1, le=1000)] = 50

    # How many pages one read of a queue (`GET /pages?status=`) lists at most.
    # When more wait, the answer says so and the rest follow as pages are decided.
    page_queue_limit: Annotated[int, Field(ge=1, le=1000)] = DEFAULT_PAGE_QUEUE_LIMIT

    # How many events one read of a case's audit trail (`GET /cases/{case_id}/audit`)
    # lists at most: the first ones. When the case has more, the answer says so.
    audit_trail_limit: Annotated[int, Field(ge=1, le=5000)] = DEFAULT_AUDIT_TRAIL_LIMIT

    # How many cases one read of the case list (`GET /cases`) holds at most:
    # the newest. When more exist, the answer says so.
    case_list_limit: Annotated[int, Field(ge=1, le=1000)] = DEFAULT_CASE_LIST_LIMIT

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
        "database_service_role",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # A variable that is present but empty or blank means "not configured".
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(text, str) and not text.strip():
            return None
        return value

    @field_validator("scheduler_endpoint")
    @classmethod
    def _endpoint_names_its_scheme(cls, value: str) -> str:
        # The scheme decides whether the channel is encrypted, so it is never guessed.
        if not value.startswith(("http://", "https://")):
            raise ValueError(
                "the scheduler endpoint must start with http:// or https://"
            )
        return value

    @model_validator(mode="after")
    def _defaults_are_a_valid_start(self) -> Self:
        configs = self.default_retriever_configs
        if len(set(configs)) != len(configs):
            raise ValueError("default_retriever_configs must not repeat a value")
        if not set(configs) <= set(self.available_retriever_configs):
            raise ValueError(
                "WORKFLOW_DEFAULT_RETRIEVER_CONFIGS must be among "
                "WORKFLOW_AVAILABLE_RETRIEVER_CONFIGS"
            )
        if self.worker_max_concurrent_activities > self.database_pool_size:
            raise ValueError(
                "WORKFLOW_WORKER_MAX_CONCURRENT_ACTIVITIES must not be larger "
                "than WORKFLOW_DATABASE_POOL_SIZE"
            )
        if self.scheduler_entra_auth and not self.scheduler_secure:
            # A token is never sent over an unencrypted channel.
            raise ValueError(
                "WORKFLOW_SCHEDULER_ENTRA_AUTH needs an https:// scheduler endpoint"
            )
        return self

    @property
    def scheduler_secure(self) -> bool:
        """Whether the scheduler is reached over TLS."""
        return self.scheduler_endpoint.startswith("https://")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build the settings once per process."""
    return Settings()
