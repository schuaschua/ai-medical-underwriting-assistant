"""Story 1.7: the whole path of a started case, with every real part but Azure and Dapr.

`workflow` and `intake` as they really run, against a real PostgreSQL, the
Durable Task Scheduler emulator and the blob emulator
(`docker compose up --detach --wait`), with the Language stand-in where Azure
AI Language would be. Where the Dapr sidecars would be, a transport hands
`workflow`'s service invocation to the `intake` app. Since story 1.8 the
lifecycle goes on to classification, so `classification` is behind the
sidecar as well; what it does is that story's test
(test_classification_end_to_end.py).

These tests are here and not under `services/` because they name the
stand-in's package, which nothing there may do (spine AD-17).
"""

import pytest
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from synthdata_stack import (
    LocalClassification,
    LocalIntake,
    ServicesBehindSidecar,
    audit_rows,
    completed,
    query,
    start_and_wait,
    workflow_service,
)

from contracts.models.workflow import CaseProgress
from intake.adapters.blob import build_blob_service
from synthdata.language_standin import Mode
from workflow.settings import Settings

pytestmark = pytest.mark.integration


def test_story_1_7_an_uploaded_and_started_case_shows_redaction_done_and_its_pages_uploaded(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
) -> None:
    case_id, _ = intake.upload("case-002.pdf")
    sidecar = ServicesBehindSidecar(
        intake=intake.app(), classification=classification.app()
    )

    progress, trail, _ = start_and_wait(
        workflow_service_settings,
        scheduler_client,
        sidecar,
        case_id,
        waits_for_a_human=True,
    )

    # Progress: redaction done, and the six pages of the case tracked: as
    # `uploaded` when redaction ended. By now each page is classified (story
    # 1.8) and routed by the gate (story 1.9), the three medical pages on to
    # extraction and the other three back to the customer, so the case waits
    # for a human, and its lifecycle with it (story 1.10).
    assert (progress.case_status.value, progress.redaction_status.value) == (
        "awaiting_human",
        "done",
    )
    assert [page.page_number for page in progress.pages] == [1, 2, 3, 4, 5, 6]
    assert [page.page_status.value for page in progress.pages] == [
        "extracting",
        "extracting",
        "extracting",
        "awaiting_customer",
        "awaiting_customer",
        "awaiting_customer",
    ]
    # They are the pages `intake` holds, in the same order.
    assert [page.page_id for page in progress.pages] == [
        page.page_id for page in intake.pages(case_id).pages
    ]
    # The audit trail: one `document.redacted` event, a count per category.
    (event,) = (
        event for event in trail.events if event.action.value == "document.redacted"
    )
    assert (event.action.value, event.actor) == (
        "document.redacted",
        "intake:azure-ai-language",
    )
    assert isinstance(event.detail, dict)
    assert event.detail["Person"] >= 2
    assert all(isinstance(count, int) for count in event.detail.values())
    assert set(event.detail) <= {
        "Person",
        "Address",
        "PhoneNumber",
        "Email",
        "USSocialSecurityNumber",
        "PolicyNumber",
    }
    # What a later stage will read holds tokens, not the planted name.
    text = intake.page_text(progress.pages[0].page_id)
    assert "[Person]" in text
    assert "Jordan Samplewick" not in text
    # Redaction was commanded once, and before anything else.
    assert sidecar.calls[0] == ("intake", "POST", f"/cases/{case_id}/redaction")
    assert sidecar.paths("intake").count(f"/cases/{case_id}/redaction") == 1


@pytest.mark.parametrize(
    ("mode", "error_code"),
    [(Mode.FAIL, "redaction_failed"), (Mode.HANG, "stage_timeout")],
)
def test_story_1_7_with_a_stand_in_told_to_fail_or_hang_the_case_fails_and_no_page_exists(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    workflow_admin: Settings,
    mode: Mode,
    error_code: str,
) -> None:
    intake.language.mode = mode
    # The stage's own deadline, made short for the stand-in that never ends.
    intake.settings = intake.settings.model_copy(
        update={"redaction_deadline_seconds": 1.0}
    )
    case_id, document_id = intake.upload("case-001.pdf")

    with workflow_service(
        workflow_service_settings, ServicesBehindSidecar(intake=intake.app())
    ) as client:
        client.post(f"/cases/{case_id}/start")
        completed(scheduler_client, case_id)
        progress = CaseProgress.model_validate(
            client.get(f"/cases/{case_id}/progress").json()
        )

    # The case is failed, with one `stage.failed` event, and no page exists
    # in `workflow` or in `intake`.
    assert (progress.case_status.value, progress.pages) == ("failed", [])
    assert audit_rows(workflow_service_settings, case_id) == [
        ("stage.failed", None, error_code, None, "intake:azure-ai-language")
    ]
    assert intake.pages(case_id).pages == []
    # Read as the database's owner: `workflow`'s own role has no right to
    # `intake`'s schema (AD-4).
    assert query(workflow_admin, "SELECT count(*) FROM intake.page") == [(0,)]
    # Nothing is left in `cases`, and the original was read by nobody but
    # the redaction call: one job was given its address, and the original's
    # container holds that one blob still.
    blobs = build_blob_service(intake.settings)
    cases = blobs.get_container_client(intake.settings.cases_container)
    originals = blobs.get_container_client(intake.settings.originals_container)
    assert list(cases.list_blob_names()) == []
    (original_name,) = originals.list_blob_names()
    assert original_name == f"{case_id}/{document_id}.pdf"
    (job,) = intake.language.submitted
    assert job["analysisInput"]["documents"][0]["source"]["location"].endswith(
        original_name
    )
    if mode is Mode.HANG:
        # The Language job was cancelled at the deadline.
        assert intake.language.cancelled == list(intake.language.jobs)
