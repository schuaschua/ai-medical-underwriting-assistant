"""The routes of `verdict`: the probes, the run command and the three reads (spine, Operations).

`POST /verdict-runs` is `workflow`'s command, one run per case and retriever
row. The reads answer what was stored: the runs of a case, the steps of a
run, and the steps of a case with their filters. Every run payload carries
the label "AI suggestion, not a decision" (AD-10).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Protocol

from fastapi import APIRouter, Path, Query, Request

from contracts.errors import NO_TRACE_ID, DomainError, ErrorCode
from contracts.ids import UUID7_PATTERN
from contracts.models.verdict import (
    AgentStepList,
    AgentStepQuery,
    RunStepQuery,
    VerdictRunCommand,
    VerdictRunList,
    VerdictRunResult,
)
from contracts.models.web import Health
from contracts.operations import get_operation
from verdict.adapters.telemetry import current_trace_id
from verdict.domain.run import (
    DEFAULT_RUN_LIST_LIMIT,
    DEFAULT_STEP_LIST_LIMIT,
    RunOptions,
    RunPorts,
    list_case_agent_steps,
    list_run_steps,
    list_verdict_runs,
    run_verdict,
    utc_now,
)
from verdict.settings import HEALTH_PATH, READY_PATH

logger = logging.getLogger(__name__)

NOT_READY_MESSAGE = "The service is not ready."
# The W3C trace context headers passed on with the tools' calls to other services.
_TRACE_HEADERS = ("traceparent", "tracestate")

# An id that is not a UUIDv7 is refused before anything is looked up.
IdPath = Annotated[str, Path(pattern=UUID7_PATTERN)]


class SchemaRevision(Protocol):
    async def current(self) -> str | None:
        """The migration revision the database is at, or None if there is none."""
        ...


@dataclass(frozen=True, slots=True)
class Dependencies:
    """What the routes work with; the app factory or a test provides it."""

    run: RunPorts
    options: RunOptions
    schema_revision: SchemaRevision
    # The newest migration bundled with this build.
    head_revision: str
    # How many runs, and how many steps, one read lists at most.
    run_list_limit: int = DEFAULT_RUN_LIST_LIMIT
    step_list_limit: int = DEFAULT_STEP_LIST_LIMIT
    now: Callable[[], datetime] = field(default=utc_now)


def build_router(dependencies: Dependencies) -> APIRouter:
    """Build the service's routes around one set of dependencies."""
    router = APIRouter()
    command_operation = get_operation("run_verdict")
    runs_operation = get_operation("list_verdict_runs")
    run_steps_operation = get_operation("list_run_steps")
    case_steps_operation = get_operation("list_case_agent_steps")

    @router.api_route(HEALTH_PATH, methods=["GET", "HEAD"])
    async def health() -> Health:
        return Health()

    @router.api_route(READY_PATH, methods=["GET", "HEAD"])
    async def ready() -> Health:
        # azure.md rule 22: ready only when the schema is at the bundled head.
        try:
            current = await dependencies.schema_revision.current()
        except Exception as error:
            # The database cannot be reached. security rule 31: the error's
            # type only; its message can hold the connection's address.
            logger.warning("not ready: database type=%s", type(error).__qualname__)
            raise DomainError(
                ErrorCode.UPSTREAM_UNAVAILABLE, NOT_READY_MESSAGE
            ) from error
        if current != dependencies.head_revision:
            logger.warning(
                "not ready: schema_revision=%s head_revision=%s",
                current,
                dependencies.head_revision,
            )
            raise DomainError(ErrorCode.UPSTREAM_UNAVAILABLE, NOT_READY_MESSAGE)
        return Health()

    @router.post(command_operation.path)
    async def run_verdict_route(
        command: VerdictRunCommand, request: Request
    ) -> VerdictRunResult:
        return await run_verdict(
            command,
            ports=dependencies.run,
            options=dependencies.options,
            trace_id=current_trace_id(request.headers.get("traceparent"))
            or NO_TRACE_ID,
            trace_context={
                name: request.headers[name]
                for name in _TRACE_HEADERS
                if name in request.headers
            },
            now=dependencies.now,
        )

    # A case with no run, or one this service has never heard of, is an
    # empty list: 404 is not used here.
    @router.get(runs_operation.path)
    async def list_verdict_runs_route(case_id: IdPath) -> VerdictRunList:
        return await list_verdict_runs(
            case_id,
            repository=dependencies.run.repository,
            limit=dependencies.run_list_limit,
        )

    # AD-15: the agent's log by run, with the same two filters as by case
    # and the cursor (the last step number seen). A run that is not stored
    # is 404; a query that is not valid is 422 before anything is read.
    @router.get(run_steps_operation.path)
    async def list_run_steps_route(
        verdict_run_id: IdPath, query: Annotated[RunStepQuery, Query()]
    ) -> AgentStepList:
        return await list_run_steps(
            verdict_run_id,
            query,
            repository=dependencies.run.repository,
            limit=dependencies.step_list_limit,
        )

    # AD-15: the agent's log by case, with the optional `tool` and `rule_id`
    # filters and the cursor (the last step seen: its run and step number).
    # An unknown tool, a rule id of another form, half a cursor, or any
    # other query parameter is refused with 422 before anything is read.
    @router.get(case_steps_operation.path)
    async def list_case_agent_steps_route(
        case_id: IdPath, query: Annotated[AgentStepQuery, Query()]
    ) -> AgentStepList:
        return await list_case_agent_steps(
            case_id,
            query,
            repository=dependencies.run.repository,
            limit=dependencies.step_list_limit,
        )

    return router
