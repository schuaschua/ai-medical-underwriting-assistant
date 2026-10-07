"""Story 1.9: a started case is redacted, classified and gated, with every real part but Azure and Dapr.

`workflow`, `intake` and `classification` as they really run, against a real
PostgreSQL, the Durable Task Scheduler emulator and the blob emulator
(`docker compose up --detach --wait`), with the stand-ins of this package
where Azure AI Language and the Foundry chat deployment would be.

These tests are here and not under `services/` because they name the
stand-ins' package and read the answer key, which nothing there may do
(spine AD-17).
"""

import pytest
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from synthdata_stack import (
    LocalClassification,
    LocalExtraction,
    LocalIntake,
    LocalVerdict,
    ServicesBehindSidecar,
    answer_key,
    start_and_wait,
)

from synthdata.foundry_standin import Mode
from workflow.settings import Settings

pytestmark = pytest.mark.integration


def test_story_1_9_one_case_ends_with_pages_on_all_three_routes(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
) -> None:
    # The stand-in's runs differ on laboratory reports and identity documents
    # only: such a page is classified at 0.6, every other page at 1.0.
    classification.model.mode = Mode.MIXED
    case_id, _ = intake.upload("case-002.pdf")
    key = answer_key("case-002")
    assert [page["page_type"] for page in key["pages"]] == [
        "application_form",
        "attending_physician_statement",
        "lab_report",
        "invoice",
        "other",
        "other",
    ]
    sidecar = ServicesBehindSidecar(
        intake=intake.app(),
        classification=classification.app(),
        extraction=extraction.app(),
        verdict=verdict.app(),
    )

    progress, trail, _ = start_and_wait(
        workflow_service_settings,
        scheduler_client,
        sidecar,
        case_id,
        waits_for_a_human=True,
    )

    # One case from `data/cases/`, and every route of the gate: medical and
    # sure, not medical and sure, and not sure.
    routes = [
        "extracting",
        "extracting",
        "awaiting_triage",
        "awaiting_customer",
        "awaiting_customer",
        "awaiting_customer",
    ]
    # A page sent on to extraction is extracted by now (story 2.4).
    assert [page.page_status.value for page in progress.pages] == [
        "extracted" if route == "extracting" else route for route in routes
    ]
    assert set(routes) == {"extracting", "awaiting_customer", "awaiting_triage"}
    assert progress.case_status.value == "awaiting_human"
    listed = {
        item.page_id: item for item in classification.listed(case_id).classifications
    }
    assert [listed[page.page_id].confidence for page in progress.pages] == [
        1.0,
        1.0,
        0.6,
        1.0,
        1.0,
        1.0,
    ]
    # Each page has its `page.routed` event, with the route it was given.
    stored = {
        event.page_id: event.model_dump(mode="json")["detail"]
        for event in trail.events
        if event.action.value == "page.routed"
    }
    assert [stored[page.page_id] for page in progress.pages] == [
        {"route": route, "threshold": 0.9} for route in routes
    ]


def test_story_1_9_pages_the_classifier_is_unsure_of_go_to_triage(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
) -> None:
    # Three of five runs agree: every page is classified at 0.6, medical or not.
    classification.model.mode = Mode.DISAGREE
    case_id, _ = intake.upload("case-002.pdf")
    sidecar = ServicesBehindSidecar(
        intake=intake.app(),
        classification=classification.app(),
        extraction=extraction.app(),
        verdict=verdict.app(),
    )

    progress, trail, _ = start_and_wait(
        workflow_service_settings,
        scheduler_client,
        sidecar,
        case_id,
        waits_for_a_human=True,
    )

    assert [page.page_status.value for page in progress.pages] == [
        "awaiting_triage"
    ] * 6
    assert progress.case_status.value == "awaiting_human"
    routed = [event for event in trail.events if event.action.value == "page.routed"]
    assert sorted(event.page_id or "" for event in routed) == sorted(
        page.page_id for page in progress.pages
    )
