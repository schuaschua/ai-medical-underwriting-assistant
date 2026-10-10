"""Story 2.4: facts extracted from a synthetic case, each with a quote checked against the real page text.

`workflow`, `intake`, `classification` and `extraction` as they really run,
against a real PostgreSQL, the Durable Task Scheduler emulator and the blob
emulator (`docker compose up --detach --wait`), with the stand-ins of this
package where Azure AI Language and the Foundry chat deployment would be, and
a transport where the Dapr sidecars would be.

The tests are here and not under `services/` because they name the stand-ins'
package and read the answer key, which nothing there may do (spine AD-17).
"""

from typing import Any

import pytest
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.client import OrchestrationStatus
from fastapi.testclient import TestClient
from synthdata_stack import (
    LocalClassification,
    LocalExtraction,
    LocalIntake,
    LocalVerdict,
    ServicesBehindSidecar,
    answer_key,
    completed,
    end_lifecycle,
    start_and_wait,
    wait_for_extractions,
    workflow_service,
)
from workflow_local import wait_for_case_status

from contracts.models.extraction import Fact
from contracts.models.intake import PageBoxes
from contracts.text import QUOTE_OFFSET_UNIT, has_mask_token, normalise
from synthdata.foundry_standin import (
    FACT_NOT_ON_THE_PAGE,
    LOCAL_DEPLOYMENT,
    Mode,
    page_to_extract_of,
)
from workflow.settings import Settings

pytestmark = pytest.mark.integration

EXTRACTION_ACTOR = f"extraction:{LOCAL_DEPLOYMENT}"
UNDERWRITER = {"actor": "underwriter"}
CUSTOMER = {"actor": "customer"}


def sidecar_for(
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
) -> ServicesBehindSidecar:
    return ServicesBehindSidecar(
        intake=intake.app(),
        classification=classification.app(),
        extraction=extraction.app(),
        verdict=verdict.app(),
    )


def page_texts(intake: LocalIntake, case_id: str) -> dict[str, tuple[int, str]]:
    """The case's pages as `intake` stores them: page number and text, by page id."""
    return {
        page.page_id: (page.page_number, intake.page_text(page.page_id))
        for page in intake.pages(case_id).pages
    }


def assert_quotes_are_checked(
    facts: list[Fact], texts: dict[str, tuple[int, str]]
) -> None:
    """Every fact has its page, its number and a quote; a verified one's offsets select its quote."""
    for fact in facts:
        page_number, text = texts[fact.page_id]
        assert fact.page_number == page_number
        assert fact.statement.strip() and fact.quote.strip()
        if fact.quote_verified:
            assert fact.quote_start is not None and fact.quote_end is not None
            assert 0 <= fact.quote_start < fact.quote_end <= len(text)
            selected = text[fact.quote_start : fact.quote_end]
            assert normalise(selected) == normalise(fact.quote)
        else:
            assert (fact.quote_start, fact.quote_end) == (None, None)


def decide(
    workflow: TestClient, case_id: str, page_id: str, decision: str, who: dict[str, str]
) -> Any:
    return workflow.post(
        f"/cases/{case_id}/pages/{page_id}/decisions",
        json={"decision": decision, **who},
    )


def test_story_2_4_case_001_ends_completed_with_facts_whose_quotes_are_verified_against_the_page_text(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
) -> None:
    case_id, _ = intake.upload("case-001.pdf")
    key = answer_key("case-001")
    sidecar = sidecar_for(intake, classification, extraction, verdict)

    progress, trail, output = start_and_wait(
        workflow_service_settings, scheduler_client, sidecar, case_id
    )

    # The three pages end `extracted` and the case `completed`; the
    # orchestration ended by itself when the case was final.
    assert [page.page_status.value for page in progress.pages] == ["extracted"] * 3
    assert progress.case_status.value == "completed"
    assert output == {"case_id": case_id, "case_status": "completed"}

    # Every stored fact has a page number and a quote, and every verified
    # fact's offsets select text on the page that equals its quote once both
    # are normalised. With the stand-in every quote is on its page.
    texts = page_texts(intake, case_id)
    facts = extraction.facts(case_id).facts
    assert facts
    assert_quotes_are_checked(facts, texts)
    assert all(fact.quote_verified for fact in facts)
    assert QUOTE_OFFSET_UNIT == "unicode_code_point"
    # In page order, with facts from every page: the application form, the
    # physician's statement and the laboratory report.
    assert [fact.page_number for fact in facts] == sorted(
        fact.page_number for fact in facts
    )
    assert {fact.page_number for fact in facts} == {1, 2, 3}
    assert len({fact.fact_id for fact in facts}) == len(facts)
    assert {fact.case_id for fact in facts} == {case_id}

    # The table page: a laboratory result whose test, value and unit sit in
    # three cells is one fact, verified across the cells' line breaks, in
    # the order the stored page text holds them.
    assert key["pages"][2]["page_type"] == "lab_report"
    hba1c = next(fact for fact in facts if fact.statement == "HbA1c 7.4 %")
    lab_text = texts[hba1c.page_id][1]
    assert hba1c.page_number == 3
    assert hba1c.quote == "HbA1c 7.4 %"
    assert hba1c.quote_start is not None and hba1c.quote_end is not None
    assert lab_text[hba1c.quote_start : hba1c.quote_end] == "HbA1c\n7.4\n%"
    # `intake` turns that offset range into the boxes of exactly those words:
    # the offsets are counted the way its word boxes count them.
    with TestClient(intake.app()) as intake_client:
        boxes = PageBoxes.model_validate(
            intake_client.get(
                f"/pages/{hba1c.page_id}/boxes",
                params={
                    "quote_start": hba1c.quote_start,
                    "quote_end": hba1c.quote_end,
                },
            ).json()
        ).boxes
    assert [lab_text[box.char_start : box.char_end] for box in boxes] == [
        "HbA1c",
        "7.4",
        "%",
    ]
    assert (boxes[0].char_start, boxes[-1].char_end) == (
        hba1c.quote_start,
        hba1c.quote_end,
    )
    # A row of another table, on the physician's statement.
    pressure = next(
        fact for fact in facts if fact.statement.startswith("Blood pressure 126/80")
    )
    assert pressure.page_number == 2 and pressure.quote_verified

    # No masked value is a fact, and no planted identifier is in one.
    stored = " ".join(f"{fact.statement} {fact.quote}" for fact in facts)
    assert not has_mask_token(stored)
    for identifier in key["identifiers"]:
        assert identifier["value"] not in stored, identifier["category"]

    # One extraction command per page; `extraction` asked `intake` for the
    # page list and the page text only, and the model was sent the redacted
    # text and nothing else, one call per page.
    assert sidecar.paths("extraction") == ["/fact-sets"] * 3
    reads = extraction.reads_of_intake()
    assert {method for method, _ in reads} == {"GET"}
    assert {path for _, path in reads} == {
        f"/cases/{case_id}/pages",
        *(f"/pages/{page_id}/text" for page_id in texts),
    }
    sent = [
        text
        for text in map(page_to_extract_of, extraction.model.requests)
        if text is not None
    ]
    assert sorted(sent) == sorted(text for _, text in texts.values())
    assert any("[Person]" in text for text in sent)
    for identifier in key["identifiers"]:
        for text in sent:
            assert identifier["value"] not in text, identifier["category"]

    # The trail: one `facts.extracted` event per page, by the service and the
    # model deployment, about the page's fact set; `case.completed` last.
    extracted = [
        event for event in trail.events if event.action.value == "facts.extracted"
    ]
    assert sorted(event.page_id or "" for event in extracted) == sorted(texts)
    assert {(event.actor_kind.value, event.actor) for event in extracted} == {
        ("ai", EXTRACTION_ACTOR)
    }
    assert len({event.ref for event in extracted}) == 3
    assert [event.action.value for event in trail.events][-1] == "case.completed"
    for page_id in texts:
        assert [
            event.action.value for event in trail.events if event.page_id == page_id
        ] == ["page.classified", "page.routed", "facts.extracted"]


def test_story_2_4_a_quote_that_is_not_on_the_page_is_stored_unverified_and_the_case_still_completes(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
) -> None:
    # The stand-in is told to return, beside each page's facts, one whose
    # quote is on no page.
    extraction.model.mode = Mode.QUOTE_NOT_ON_PAGE
    case_id, _ = intake.upload("case-001.pdf")
    sidecar = sidecar_for(intake, classification, extraction, verdict)

    progress, trail, output = start_and_wait(
        workflow_service_settings, scheduler_client, sidecar, case_id
    )

    assert progress.case_status.value == "completed"
    assert output["case_status"] == "completed"
    assert [page.page_status.value for page in progress.pages] == ["extracted"] * 3
    facts = extraction.facts(case_id).facts
    assert_quotes_are_checked(facts, page_texts(intake, case_id))
    unverified = [fact for fact in facts if not fact.quote_verified]
    # One per page: stored, flagged, with no offsets; never dropped.
    assert [(fact.page_number, fact.quote) for fact in unverified] == [
        (number, FACT_NOT_ON_THE_PAGE["quote"]) for number in (1, 2, 3)
    ]
    assert {(fact.quote_start, fact.quote_end) for fact in unverified} == {(None, None)}
    # The page's own facts are verified beside it.
    assert sum(fact.quote_verified for fact in facts) == len(facts) - 3
    assert "stage.failed" not in [event.action.value for event in trail.events]


def test_story_2_4_a_masked_value_the_model_proposes_is_not_stored(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
) -> None:
    extraction.model.mode = Mode.MASKED_VALUE
    case_id, _ = intake.upload("case-001.pdf")
    sidecar = sidecar_for(intake, classification, extraction, verdict)

    progress, _, _ = start_and_wait(
        workflow_service_settings, scheduler_client, sidecar, case_id
    )

    assert progress.case_status.value == "completed"
    facts = extraction.facts(case_id).facts
    assert len(facts) > 3
    for fact in facts:
        assert not has_mask_token(fact.statement)
        assert fact.quote != "[Person]"


def test_story_2_4_a_page_accepted_in_triage_is_extracted_then_and_a_page_with_nothing_medical_has_no_facts(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
) -> None:
    case_id, _ = intake.upload("case-002.pdf")
    key = answer_key("case-002")
    assert [page["page_type"] for page in key["pages"]][3:] == [
        "invoice",
        "other",
        "other",
    ]
    sidecar = sidecar_for(intake, classification, extraction, verdict)

    with workflow_service(workflow_service_settings, sidecar) as workflow:
        try:
            workflow.post(f"/cases/{case_id}/start", json=CUSTOMER)
            wait_for_case_status(workflow, case_id, "awaiting_human", 90)
            # The three medical pages are extracted while the others wait.
            waiting = wait_for_extractions(workflow, case_id)
            invoice, fifth, sixth = (page["page_id"] for page in waiting["pages"][3:])
            facts_while_waiting = extraction.facts(case_id).facts
            commands_while_waiting = len(sidecar.paths("extraction"))
            # The customer keeps the invoice and discards the rest; the
            # underwriter accepts the invoice.
            for page_id, decision, who in (
                (fifth, "discard", CUSTOMER),
                (sixth, "discard", CUSTOMER),
                (invoice, "keep", CUSTOMER),
                (invoice, "accept", UNDERWRITER),
            ):
                assert (
                    decide(workflow, case_id, page_id, decision, who).status_code == 200
                )
            state = completed(scheduler_client, case_id)
            progress = workflow.get(f"/cases/{case_id}/progress").json()
            trail = workflow.get(f"/cases/{case_id}/audit").json()
        finally:
            end_lifecycle(scheduler_client, case_id)

    assert [page["page_status"] for page in waiting["pages"]] == [
        "extracted",
        "extracted",
        "extracted",
        "awaiting_customer",
        "awaiting_customer",
        "awaiting_customer",
    ]
    assert {fact.page_number for fact in facts_while_waiting} == {1, 2, 3}
    assert commands_while_waiting == 3
    # Extracted when it was accepted, and not before: a fourth command. The
    # case completes when that last page is final.
    assert sidecar.paths("extraction") == ["/fact-sets"] * 4
    assert state.runtime_status is OrchestrationStatus.COMPLETED
    assert progress["case_status"] == "completed"
    assert [page["page_status"] for page in progress["pages"]] == [
        "extracted",
        "extracted",
        "extracted",
        "extracted",
        "discarded",
        "discarded",
    ]
    # The invoice has nothing medical: a done result with no facts, with its
    # event all the same, and the page is `extracted`.
    facts = extraction.facts(case_id).facts
    assert facts == facts_while_waiting
    assert_quotes_are_checked(facts, page_texts(intake, case_id))
    assert [
        event["action"] for event in trail["events"] if event["page_id"] == invoice
    ] == [
        "page.classified",
        "page.routed",
        "page.kept",
        "page.accepted",
        "facts.extracted",
    ]
    assert trail["events"][-1]["action"] == "case.completed"
