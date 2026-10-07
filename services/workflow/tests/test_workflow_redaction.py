"""Story 1.7: redaction is the first stage `workflow` commands.

Unit tests: the orchestrator's next step, the redaction activity and the
client module that reaches `intake` through the Dapr sidecar. No scheduler,
no database, no network.
"""

import asyncio
import json
from typing import Any

import httpx
import pytest
from workflow_fakes import (
    TRACE_ID,
    SidecarStandIn,
    redaction_done,
)

from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from contracts.operations import get_operation
from workflow.adapters.dapr import (
    StageClient,
    build_http_client,
    invoke_path,
)
from workflow.settings import Settings

TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"


# --- The client module: `intake` through the Dapr sidecar ----------------------------------


def client_for(
    handler: Any, settings: Settings | None = None
) -> tuple[StageClient, Settings]:
    settings = settings or Settings()
    return (
        StageClient(
            build_http_client(settings, httpx.MockTransport(handler)), settings
        ),
        settings,
    )


def call(client: StageClient, case_id: str, **options: Any) -> Any:
    async def scenario() -> Any:
        try:
            return await client.redact_document(
                case_id,
                eval_run_id=options.get("eval_run_id"),
                trace_context=options.get("trace_context", {}),
            )
        finally:
            await client.aclose()

    return asyncio.run(scenario())


def test_story_1_7_redaction_is_commanded_through_the_sidecar_by_app_id_with_the_trace(
    case_id: str,
) -> None:
    sidecar = SidecarStandIn()
    client, settings = client_for(sidecar.handle)
    eval_run_id = new_id()

    result = call(
        client,
        case_id,
        eval_run_id=eval_run_id,
        trace_context={"traceparent": TRACEPARENT},
    )

    (request,) = sidecar.requests
    operation = get_operation("redact_document")
    # AD-3: the sidecar on loopback, `intake` by its Dapr app id, the path
    # from the contracts. No hostname of another service.
    assert str(request.url) == (
        f"http://127.0.0.1:{settings.dapr_http_port}/v1.0/invoke/intake/method"
        f"/cases/{case_id}/redaction"
    )
    assert request.url.path == invoke_path(
        operation.owner, operation.path.format(case_id=case_id)
    )
    assert request.method == "POST"
    # Ids only (AD-6), and the W3C trace context goes with the call.
    assert json.loads(request.content) == {"eval_run_id": eval_run_id}
    assert request.headers["traceparent"] == TRACEPARENT
    assert result == sidecar.stages.results[case_id]


def test_story_1_7_a_result_about_another_case_is_never_recorded(case_id: str) -> None:
    other = redaction_done(new_id(), [new_id()])
    client, _ = client_for(
        lambda request: httpx.Response(200, json=other.model_dump(mode="json"))
    )

    with pytest.raises(DomainError) as raised:
        call(client, case_id)

    # No retry can mend it: the activity answers with it as refused.
    assert raised.value.code is ErrorCode.VALIDATION_FAILED
