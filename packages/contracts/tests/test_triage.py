"""Story 1.11: the queue of waiting pages and the payload the triage screen is shown from."""

from typing import Any

import pytest
from pydantic import ValidationError

from contracts.enums import QueuedBy
from contracts.models.web import TriagePage, TriageQueue
from contracts.models.workflow import PageQueue, PageQueueQuery, QueuedPage
from contracts.operations import get_operation

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


def test_story_1_11_the_queue_operation_takes_a_status_and_answers_with_the_queue() -> (
    None
):
    operation = get_operation("list_pages_by_status")

    assert (operation.method.value, operation.path) == ("GET", "/pages")
    assert operation.query_model is PageQueueQuery
    assert operation.response_model is PageQueue


def test_story_1_11_the_queue_says_whether_more_pages_wait_than_it_lists() -> None:
    queue = PageQueue.model_validate({"pages": [queued_page()], "has_more": True})

    assert queue.has_more is True
    with pytest.raises(ValidationError):
        PageQueue.model_validate({"pages": [queued_page()]})


def test_story_1_11_a_queued_page_names_its_cases_classifier_and_how_it_got_there() -> (
    None
):
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


@pytest.mark.parametrize("status", [None, "", "waiting", "AWAITING_TRIAGE"])
def test_story_1_11_a_queue_query_without_a_known_status_is_rejected(
    status: str | None,
) -> None:
    with pytest.raises(ValidationError):
        PageQueueQuery.model_validate({} if status is None else {"status": status})


def test_story_1_11_a_triage_page_carries_the_reading_the_screen_shows() -> None:
    page = TriagePage.model_validate(triage_page())

    assert page.thumbnail_path == f"/api/pages/{PAGE}/thumbnail"
    assert (page.page_type, page.is_medical, page.confidence, page.reason) == (
        "invoice",
        False,
        0.96,
        "Shows an amount due.",
    )
    assert page.queued_by is QueuedBy.CUSTOMER


def test_story_1_11_a_triage_page_may_come_without_its_reading() -> None:
    page = TriagePage.model_validate(
        triage_page(page_type=None, is_medical=None, confidence=None, reason=None)
    )

    assert page.page_type is None
    assert TriageQueue(pages=[page], has_more=False).pages == [page]


@pytest.mark.parametrize(
    "changes",
    [
        {"page_type": None},
        {"confidence": None},
        {"reason": None},
        {"is_medical": None},
        # `is_medical` follows the page type, here as in a classification.
        {"is_medical": True},
        {"confidence": 1.2},
        {"reason": "Two\nlines."},
        {"thumbnail_path": " "},
        {"page_type": "receipt"},
    ],
)
def test_story_1_11_a_triage_page_with_half_a_reading_or_a_wrong_one_is_rejected(
    changes: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        TriagePage.model_validate(triage_page(**changes))
