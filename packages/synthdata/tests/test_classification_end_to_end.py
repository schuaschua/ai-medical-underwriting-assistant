"""Story 1.8: a started case is redacted and every page classified, with every real part but Azure and Dapr.

`workflow`, `intake` and `classification` as they really run, against a real
PostgreSQL, the Durable Task Scheduler emulator and the blob emulator
(`docker compose up --detach --wait`), with the stand-ins of this package
where Azure AI Language and the Foundry chat deployment would be. Where the
Dapr sidecars would be, a transport hands a service invocation to the app of
the service it names.

These tests are here and not under `services/` because they name the
stand-ins' package and read the answer key, which nothing there may do
(spine AD-17).
"""

import socket
from typing import Any

import pytest
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from synthdata_stack import (
    LocalClassification,
    LocalExtraction,
    LocalIntake,
    LocalVerdict,
    ServicesBehindSidecar,
    answer_key,
    audit_rows,
    query,
    start_and_wait,
)

from contracts.rules import is_medical
from synthdata.foundry_standin import LOCAL_DEPLOYMENT, Mode, page_text_of
from workflow.settings import Settings

pytestmark = pytest.mark.integration

ACTOR = f"classification:{LOCAL_DEPLOYMENT}"


def classification_rows(admin: Settings) -> list[tuple[Any, ...]]:
    # Read as the database's owner: `workflow`'s own role has no right to
    # another service's schema (AD-4).
    return query(
        admin,
        "SELECT page_id::text, status, page_type, is_medical, confidence "
        "FROM classification.classification ORDER BY started_at, classification_id",
    )


# --- The whole case ------------------------------------------------------------------------


def test_story_1_8_an_uploaded_and_started_case_ends_with_every_page_classified(
    workflow_service_settings: Settings,
    workflow_admin: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
) -> None:
    case_id, _ = intake.upload("case-002.pdf")
    key = answer_key("case-002")
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

    # Every page was classified, and the gate sent each medical page on to
    # extraction, where it is extracted by now (story 2.4), and each other
    # page back to the customer (story 1.9), so the case waits for a human,
    # and its lifecycle with it (story 1.10).
    page_ids = [page.page_id for page in intake.pages(case_id).pages]
    assert len(page_ids) == 6
    assert [(page.page_id, page.page_status.value) for page in progress.pages] == [
        (page_id, "extracted" if expected["is_medical"] else "awaiting_customer")
        for page_id, expected in zip(page_ids, key["pages"], strict=True)
    ]
    assert progress.case_status.value == "awaiting_human"

    # The service lists one classification per page, each with a page type,
    # medical or not, a confidence from 0 to 1, a one-line reason and the
    # contender.
    listed = classification.listed(case_id).classifications
    by_page = {item.page_id: item for item in listed}
    assert sorted(by_page) == sorted(page_ids)
    assert len(listed) == 6
    for page_id, expected in zip(page_ids, key["pages"], strict=True):
        item = by_page[page_id]
        # The stand-in reads the redacted page text; for these pages that
        # gives the answer key's page type.
        assert item.page_type.value == expected["page_type"]
        assert item.is_medical is expected["is_medical"]
        assert item.is_medical is is_medical(item.page_type)
        assert item.confidence == 1.0
        assert item.reason.strip()
        assert "\n" not in item.reason
        assert (item.case_id, item.contender.value) == (case_id, "llm")
    assert {item.is_medical for item in listed} == {True, False}

    # The audit trail: the start (story 1.13), then one `page.classified`
    # event per page, whose actor names the service and the model
    # deployment, after the redaction.
    assert [event.action.value for event in trail.events[:2]] == [
        "case.started",
        "document.redacted",
    ]
    # Then one `page.routed` event per page (story 1.9), and one
    # `facts.extracted` event per medical page (story 2.4).
    assert sorted(event.action.value for event in trail.events) == [
        "case.started",
        "document.redacted",
        *["facts.extracted"] * 3,
        *["page.classified"] * 6,
        *["page.routed"] * 6,
    ]
    classified = [
        event for event in trail.events if event.action.value == "page.classified"
    ]
    assert sorted(event.page_id or "" for event in classified) == sorted(page_ids)
    for event in classified:
        assert (event.actor_kind.value, event.actor) == ("ai", ACTOR)
        assert event.ref == by_page[event.page_id or ""].classification_id
        assert event.detail is None

    # One classify command per page, sent once redaction was done.
    assert sidecar.calls[0] == ("intake", "POST", f"/cases/{case_id}/redaction")
    assert sidecar.paths("classification") == ["/classifications"] * 6
    # Five runs of the model per page, each on the redacted reading only: no
    # planted identifier was ever sent to the model.
    # (The stand-in also answered `extraction` for the three medical pages:
    # story 2.4. Those requests carry no picture and are left out here.)
    requests = [
        request
        for request in classification.model.requests
        if page_text_of(request) is not None
    ]
    assert len(requests) == 30
    assert classification.model.extraction_calls == 3
    sent = [page_text_of(request) or "" for request in requests]
    assert all("SYNTHETIC TEST DOCUMENT" in text for text in sent)
    assert any("[Person]" in text for text in sent)
    for identifier in key["identifiers"]:
        for text in sent:
            assert identifier["value"] not in text, identifier["category"]
    # `classification` asked `intake` for pages only: their list, their text
    # and their thumbnail, one page at a time. Never the document file.
    reads = classification.reads_of_intake()
    assert {method for method, _ in reads} == {"GET"}
    assert {path for _, path in reads} == {
        f"/cases/{case_id}/pages",
        *(f"/pages/{page_id}/text" for page_id in page_ids),
        *(f"/pages/{page_id}/thumbnail" for page_id in page_ids),
    }
    # What it stored: ids and the classification, in its own schema.
    assert classification_rows(workflow_admin) == [
        (item.page_id, "done", item.page_type.value, item.is_medical, 1.0)
        for item in listed
    ]


def test_story_1_8_with_runs_that_differ_every_page_has_the_agreement_rate_as_its_confidence(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
) -> None:
    classification.model.mode = Mode.DISAGREE
    case_id, _ = intake.upload("case-001.pdf")
    key = answer_key("case-001")
    sidecar = ServicesBehindSidecar(
        intake=intake.app(),
        classification=classification.app(),
        extraction=extraction.app(),
        verdict=verdict.app(),
    )

    progress, _, _ = start_and_wait(
        workflow_service_settings,
        scheduler_client,
        sidecar,
        case_id,
        waits_for_a_human=True,
    )

    # Three of five runs agree: the majority's type, at 0.6.
    listed = {
        item.page_id: item for item in classification.listed(case_id).classifications
    }
    # Under the gate's 0.90 every such page goes to triage (story 1.9), and
    # the case waits for a human.
    assert [page.page_status.value for page in progress.pages] == [
        "awaiting_triage"
    ] * 3
    assert progress.case_status.value == "awaiting_human"
    for page, expected in zip(progress.pages, key["pages"], strict=True):
        item = listed[page.page_id]
        assert item.page_type.value == expected["page_type"]
        assert item.confidence == 0.6
        assert item.is_medical is expected["is_medical"]


# --- Failures ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "error_code"),
    [(Mode.INVALID, "invalid_model_output")],
)
def test_story_1_8_with_a_stand_in_told_to_fail_no_classification_is_stored_and_the_case_fails(
    workflow_service_settings: Settings,
    workflow_admin: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
    mode: Mode,
    error_code: str,
) -> None:
    classification.model.mode = mode
    case_id, _ = intake.upload("case-001.pdf")
    sidecar = ServicesBehindSidecar(
        intake=intake.app(),
        classification=classification.app(),
        extraction=extraction.app(),
        verdict=verdict.app(),
    )

    progress, _, output = start_and_wait(
        workflow_service_settings, scheduler_client, sidecar, case_id
    )

    # No classification of any page is stored or listed.
    page_ids = [page.page_id for page in progress.pages]
    assert classification.listed(case_id).classifications == []
    stored = classification_rows(workflow_admin)
    assert stored
    assert {row[1:] for row in stored} == {("failed", None, None, None)}
    # The case is failed, with a `stage.failed` event for the page whose
    # failure came first; a failed case takes no further result, so the
    # other pages' failures write nothing.
    assert (progress.case_status.value, output["case_status"]) == ("failed", "failed")
    events = audit_rows(workflow_service_settings, case_id)
    assert [event[0] for event in events[:2]] == ["case.started", "document.redacted"]
    ((action, page_id, code, detail, actor),) = events[2:]
    assert (action, code, detail, actor) == ("stage.failed", error_code, None, ACTOR)
    assert page_id in page_ids
    statuses = {page.page_id: page.page_status.value for page in progress.pages}
    assert statuses[page_id] == "failed"
    assert set(statuses.values()) <= {"failed", "uploaded"}
    # Each page was commanded once: a failed result is an answer, not retried.
    assert sidecar.paths("classification") == ["/classifications"] * len(page_ids)


# --- The stand-in as a process -----------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
