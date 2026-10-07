"""Story 1.9, against a real PostgreSQL and the Durable Task Scheduler emulator.

Run `docker compose up --detach --wait` first. No test here calls Azure.
`workflow` runs as it really runs, with a stand-in where its Dapr sidecar
would be: behind it `intake` answers the redaction command and
`classification` the classify command, in the contracts' shapes.
"""

import asyncio
import contextlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.client import OrchestrationState, OrchestrationStatus
from fastapi.testclient import TestClient
from workflow_fakes import (
    FakeStages,
    SidecarStandIn,
    classification_done,
    classification_failed,
    redaction_done,
    redaction_failed,
)
from workflow_local import connect

from contracts.enums import (
    CaseStatus,
    ClassifierContender,
    PageStatus,
    RetrieverConfig,
)
from contracts.errors import ErrorCode
from contracts.ids import new_id
from contracts.models.workflow import AuditTrail, CaseProgress
from workflow.adapters.db import SqlCaseStore, build_database
from workflow.adapters.http.app import create_app
from workflow.adapters.scheduler import build_client
from workflow.domain.cases import (
    record_route,
    record_stage_result,
    settle_case_after_gate,
)
from workflow.domain.entities import StartParameters
from workflow.domain.gate import Route
from workflow.domain.lifecycle import new_case
from workflow.domain.recording import RecordOutcome
from workflow.settings import Settings

pytestmark = pytest.mark.integration

FAST_RETRIES = {
    "activity_first_retry_seconds": 0.2,
    "activity_backoff_coefficient": 1.0,
    "stage_max_attempts": 4,
}
NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
PARAMETERS = StartParameters(
    classifier_contender=ClassifierContender.LLM,
    retriever_configs=(RetrieverConfig.R3,),
    stop_after=None,
    eval_run_id=None,
)
# What the classifier reads on the pages of the mixed case, by page number:
# a medical page it is sure of, a non-medical page it is sure of, two it is
# not sure of, and one of each exactly at the default threshold.
MIXED = {
    1: ("lab_report", True, 0.95),
    2: ("invoice", False, 1.0),
    3: ("lab_report", True, 0.6),
    4: ("other", False, 0.6),
    5: ("application_form", True, 0.90),
    6: ("id_document", False, 0.90),
}
MIXED_ROUTES = [
    "extracting",
    "awaiting_customer",
    "awaiting_triage",
    "awaiting_triage",
    "extracting",
    "awaiting_customer",
]


def query(settings: Settings, statement: str, *parameters: object) -> list[Any]:
    with connect(settings) as connection:
        return connection.execute(statement, parameters).fetchall()


def audit_rows(settings: Settings, case_id: str) -> list[tuple[Any, ...]]:
    return query(
        settings,
        "SELECT action, page_id::text, ref::text, detail, actor, actor_kind "
        "FROM workflow.audit_event WHERE case_id = %s "
        "ORDER BY occurred_at, recorded_at, audit_event_id",
        case_id,
    )


@pytest.fixture
def scheduler_client(local_scheduler: Settings) -> Iterator[DurableTaskSchedulerClient]:
    client = build_client(local_scheduler)
    try:
        yield client
    finally:
        client.close()


def completed(client: DurableTaskSchedulerClient, case_id: str) -> OrchestrationState:
    state = client.wait_for_orchestration_completion(case_id, timeout=60)
    assert state is not None
    assert state.runtime_status is OrchestrationStatus.COMPLETED
    return state


@contextlib.contextmanager
def workflow_service(
    settings: Settings, sidecar: httpx.AsyncBaseTransport, **changes: Any
) -> Iterator[TestClient]:
    """The service as it really runs: its own role, its worker, the emulator."""
    with TestClient(
        create_app(
            settings.model_copy(update={**FAST_RETRIES, **changes}), sidecar=sidecar
        ),
        raise_server_exceptions=False,
    ) as client:
        yield client


def test_story_1_9_every_page_of_a_started_case_leaves_classified_for_its_route(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=6, readings=MIXED))
    case_id, eval_run_id = new_id(), new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start", json={"eval_run_id": eval_run_id})
        state = completed(scheduler_client, case_id)
        progress = CaseProgress.model_validate(
            client.get(f"/cases/{case_id}/progress").json()
        )
        trail = AuditTrail.model_validate(client.get(f"/cases/{case_id}/audit").json())

    page_ids = sidecar.stages.results[case_id].page_ids
    # Progress shows the routed statuses; a page waits for a person, so the
    # case does too. Nothing failed: no failure reason anywhere.
    assert [page.page_status.value for page in progress.pages] == MIXED_ROUTES
    assert progress.case_status is CaseStatus.AWAITING_HUMAN
    assert progress.error_code is None
    assert {page.error_code for page in progress.pages} == {None}
    assert json.loads(state.serialized_output or "") == {
        "case_id": case_id,
        "case_status": "awaiting_human",
    }
    # The trail: one `page.routed` event per page, after that page's
    # `page.classified` event, and no page is routed twice.
    for page_id, route in zip(page_ids, MIXED_ROUTES, strict=True):
        events = [event for event in trail.events if event.page_id == page_id]
        assert [event.action.value for event in events] == [
            "page.classified",
            "page.routed",
        ]
        classified, routed = events
        stored = sidecar.stages.classifications[(case_id, page_id)]
        assert (routed.actor_kind.value, routed.actor) == ("ai", "workflow:gate")
        assert routed.ref == classified.ref == stored.classification_id
        assert routed.model_dump(mode="json")["detail"] == {
            "route": route,
            "threshold": 0.9,
        }
        assert routed.eval_run_id == eval_run_id
        assert routed.occurred_at >= classified.occurred_at
    assert len(trail.events) == 1 + 2 * 6
    # Every page status change has its event: the detail is stored as JSON.
    stored_details = [
        row[3]
        for row in audit_rows(service_settings, case_id)
        if row[0] == "page.routed"
    ]
    assert sorted(detail["route"] for detail in stored_details) == sorted(MIXED_ROUTES)


def test_story_1_9_a_case_started_with_another_threshold_is_routed_with_that_one(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=2, readings={1: MIXED[3], 2: MIXED[1]}))
    case_id = new_id()

    with workflow_service(
        service_settings, sidecar.transport(), gate_threshold=0.5
    ) as client:
        client.post(f"/cases/{case_id}/start")
        state = completed(scheduler_client, case_id)
        progress = CaseProgress.model_validate(
            client.get(f"/cases/{case_id}/progress").json()
        )

    # WORKFLOW_GATE_THRESHOLD=0.5: a medical page at 0.6 goes on to extraction.
    assert [page.page_status.value for page in progress.pages] == ["extracting"] * 2
    assert progress.case_status is CaseStatus.RUNNING
    assert json.loads(state.serialized_output or "")["case_status"] == "running"
    details = [
        row[3]
        for row in audit_rows(service_settings, case_id)
        if row[0] == "page.routed"
    ]
    assert details == [{"route": "extracting", "threshold": 0.5}] * 2


def test_story_1_9_a_case_started_with_stop_after_gate_ends_completed(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=2, readings={2: MIXED[4]}))
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        first = client.post(f"/cases/{case_id}/start", json={"stop_after": "gate"})
        state = completed(scheduler_client, case_id)
        # A repeat of the start, once the lifecycle has ended: what the case
        # was started with, and the status it has now.
        again = client.post(f"/cases/{case_id}/start", json={"stop_after": "gate"})
        progress = CaseProgress.model_validate(
            client.get(f"/cases/{case_id}/progress").json()
        )

    # The pages are routed like any other case's, and the case ends there.
    assert [page.page_status.value for page in progress.pages] == [
        "extracting",
        "awaiting_triage",
    ]
    assert progress.case_status is CaseStatus.COMPLETED
    assert first.json()["case_status"] == "running"
    assert again.json() == {**first.json(), "case_status": "completed"}
    assert json.loads(state.serialized_output or "")["case_status"] == "completed"


def test_story_1_9_progress_of_a_failed_case_carries_the_failure_reason(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(
        FakeStages(
            pages=2,
            failing_page_numbers=frozenset({2}),
            classification_error_code="invalid_model_output",
        )
    )
    failed_redaction = SidecarStandIn(
        FakeStages(redaction="failed", error_code="stage_timeout")
    )
    page_failed, case_failed, never_started = new_id(), new_id(), new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{page_failed}/start")
        completed(scheduler_client, page_failed)
        of_page = CaseProgress.model_validate(
            client.get(f"/cases/{page_failed}/progress").json()
        )
    with workflow_service(service_settings, failed_redaction.transport()) as client:
        client.post(f"/cases/{case_failed}/start")
        completed(scheduler_client, case_failed)
        of_case = client.get(f"/cases/{case_failed}/progress").json()
        unknown = client.get(f"/cases/{never_started}/progress")

    # A page stage failed: the code is on the case and on that page, and on
    # no other page.
    assert of_page.case_status is CaseStatus.FAILED
    assert of_page.error_code is ErrorCode.INVALID_MODEL_OUTPUT
    assert [(page.page_status, page.error_code) for page in of_page.pages][1] == (
        PageStatus.FAILED,
        ErrorCode.INVALID_MODEL_OUTPUT,
    )
    assert of_page.pages[0].error_code is None
    # No page of a failed case is routed.
    assert "page.routed" not in {
        row[0] for row in audit_rows(service_settings, page_failed)
    }
    # A case-level stage failed: the code is on the case.
    assert of_case == {
        "case_id": case_failed,
        "case_status": "failed",
        "redaction_status": "failed",
        "pages": [],
        "error_code": "stage_timeout",
    }
    assert unknown.status_code == 404


def test_story_1_9_the_real_store_records_a_route_once_and_only_from_classified(
    service_settings: Settings,
) -> None:
    case_id = new_id()
    first, second, third = new_id(), new_id(), new_id()
    done = classification_done(case_id, first, page_type="invoice", is_medical=False)

    async def scenario() -> tuple[
        list[tuple[RecordOutcome, Route | None]], list[CaseStatus], CaseProgress
    ]:
        database = build_database(service_settings)
        store = SqlCaseStore(database)
        try:
            await store.start(new_case(case_id, PARAMETERS, NOW))
            await record_stage_result(
                redaction_done(case_id, [first, second, third]), store=store
            )
            await record_stage_result(done, store=store)

            async def route(
                page_id: str, ref: str, to: Route
            ) -> tuple[RecordOutcome, Route | None]:
                return await record_route(
                    case_id, page_id, ref, to, 0.9, store=store, now=lambda: NOW
                )

            outcomes = [
                await route(first, done.classification_id, Route.CUSTOMER),
                # The route activity ran again, asked for another route: no
                # second event, and the stored route is what it answers.
                await route(first, done.classification_id, Route.TRIAGE),
                # Under another reference: the page is no longer `classified`.
                await route(first, new_id(), Route.TRIAGE),
                # A page that was never classified is not routed.
                await route(second, new_id(), Route.EXTRACTION),
            ]
            statuses = [
                await settle_case_after_gate(
                    case_id, CaseStatus.AWAITING_HUMAN, store=store
                ),
                await settle_case_after_gate(
                    case_id, CaseStatus.AWAITING_HUMAN, store=store
                ),
            ]
            # A later stage fails a page: the case fails, and stays failed.
            await record_stage_result(
                classification_failed(case_id, third, "model_unavailable"), store=store
            )
            statuses.append(
                await settle_case_after_gate(case_id, CaseStatus.COMPLETED, store=store)
            )
            progress = await store.progress(case_id)
            assert progress is not None
            assert await store.move_case(new_id(), CaseStatus.COMPLETED, NOW) is None
            return outcomes, statuses, progress
        finally:
            await database.dispose()

    outcomes, statuses, progress = asyncio.run(scenario())

    assert outcomes == [
        (RecordOutcome.RECORDED, Route.CUSTOMER),
        (RecordOutcome.DUPLICATE, Route.CUSTOMER),
        (RecordOutcome.OUT_OF_ORDER, None),
        (RecordOutcome.OUT_OF_ORDER, None),
    ]
    assert statuses == [
        CaseStatus.AWAITING_HUMAN,
        CaseStatus.AWAITING_HUMAN,
        CaseStatus.FAILED,
    ]
    assert [(page.page_status.value, page.error_code) for page in progress.pages] == [
        ("awaiting_customer", None),
        ("uploaded", None),
        ("failed", ErrorCode.MODEL_UNAVAILABLE),
    ]
    assert progress.error_code is ErrorCode.MODEL_UNAVAILABLE
    routed = [
        row for row in audit_rows(service_settings, case_id) if row[0] == "page.routed"
    ]
    assert routed == [
        (
            "page.routed",
            first,
            done.classification_id,
            {"route": "awaiting_customer", "threshold": 0.9},
            "workflow:gate",
            "ai",
        )
    ]


def test_story_1_9_a_failed_redaction_is_the_reason_the_real_store_reports(
    service_settings: Settings,
) -> None:
    case_id = new_id()

    async def scenario() -> CaseProgress | None:
        database = build_database(service_settings)
        store = SqlCaseStore(database)
        try:
            await store.start(new_case(case_id, PARAMETERS, NOW))
            before = await store.progress(case_id)
            assert before is not None and before.error_code is None
            await record_stage_result(redaction_failed(case_id), store=store)
            return await store.progress(case_id)
        finally:
            await database.dispose()

    progress = asyncio.run(scenario())

    assert progress is not None
    assert progress.error_code is ErrorCode.REDACTION_FAILED
