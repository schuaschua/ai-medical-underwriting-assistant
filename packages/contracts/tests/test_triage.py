"""Story 1.11: the queue of waiting pages and the payload the triage screen is shown from."""

from typing import Any

import pytest
from pydantic import ValidationError

from contracts.enums import QueuedBy
from contracts.models.web import TriagePage, TriageQueue
from contracts.models.workflow import PageQueue, PageQueueQuery, QueuedPage

CASE = "0199b7a0-0000-7000-8000-000000000001"
PAGE = "0199b7a0-0000-7000-8000-000000000003"


def queued_page(**changes: Any) -> dict[str, Any]:
    return {
        "case_id": CASE,
        "page_id": PAGE,
        "page_number": 2,
        "page_status": "awaiting_triage",
        "classifier_contender": "llm",
        **changes,
    }


def triage_page(**changes: Any) -> dict[str, Any]:
    return {
        "case_id": CASE,
        "page_id": PAGE,
        "page_number": 2,
        "thumbnail_path": f"/api/pages/{PAGE}/thumbnail",
        "page_type": "invoice",
        "is_medical": False,
        "confidence": 0.96,
        "reason": "Shows an amount due.",
        "queued_by": "customer",
        **changes,
    }


def test_story_1_11_the_queue_takes_a_known_status_says_whether_more_wait_and_how_a_page_got_there() -> (
    None
):
    queue = PageQueue.model_validate({"pages": [queued_page()], "has_more": True})

    assert queue.has_more is True
    with pytest.raises(ValidationError):
        PageQueue.model_validate({"pages": [queued_page()]})
    assert PageQueueQuery.model_validate({"status": "awaiting_triage"})
    for query in (
        {},
        {"status": ""},
        {"status": "waiting"},
        {"status": "AWAITING_TRIAGE"},
    ):
        with pytest.raises(ValidationError):
            PageQueueQuery.model_validate(query)

    # A queued page names its case's classifier and how it got there.
    by_gate = QueuedPage.model_validate(queued_page(queued_by="gate"))
    unsaid = QueuedPage.model_validate(queued_page())

    assert by_gate.classifier_contender.value == "llm"
    assert by_gate.queued_by is QueuedBy.GATE
    assert unsaid.queued_by is None
    with pytest.raises(ValidationError):
        QueuedPage.model_validate(queued_page(queued_by="underwriter"))
    with pytest.raises(ValidationError):
        QueuedPage.model_validate(
            {k: v for k, v in queued_page().items() if k != "classifier_contender"}
        )


def test_story_1_11_a_triage_page_carries_a_whole_reading_or_none() -> None:
    page = TriagePage.model_validate(triage_page())

    assert page.thumbnail_path == f"/api/pages/{PAGE}/thumbnail"
    assert (page.page_type, page.is_medical, page.confidence, page.reason) == (
        "invoice",
        False,
        0.96,
        "Shows an amount due.",
    )
    assert page.queued_by is QueuedBy.CUSTOMER
    # A page whose classification could not be read comes without its reading.
    bare = TriagePage.model_validate(
        triage_page(page_type=None, is_medical=None, confidence=None, reason=None)
    )
    assert bare.page_type is None
    assert TriageQueue(pages=[bare], has_more=False).pages == [bare]
    wrong: list[dict[str, Any]] = [
        {"page_type": None},
        {"confidence": None},
        {"reason": None},
        {"is_medical": None},
        # `is_medical` follows the page type, here as in a classification.
        {"is_medical": True},
        {"confidence": 1.2},
        {"reason": "Two\nlines."},
        {"page_type": "receipt"},
    ]
    for changes in wrong:
        with pytest.raises(ValidationError):
            TriagePage.model_validate(triage_page(**changes))
