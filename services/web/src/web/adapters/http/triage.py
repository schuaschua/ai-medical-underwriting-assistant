"""The underwriter's triage queue, composed from what two services answer (story 1.11).

`workflow` says which pages wait for the underwriter; `classification` says
what the classifier read on each. `web` only joins the two: it holds no rule
about which pages are listed, filters nothing and keeps nothing (AD-2, AD-4).
A reading that cannot be had leaves its page listed without it, so the page
can still be decided.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Mapping

from contracts.enums import PageStatus
from contracts.errors import DomainError
from contracts.models.classification import Classification, ClassificationList
from contracts.models.web import TriagePage, TriageQueue
from contracts.models.workflow import QueuedPage
from contracts.operations import get_operation
from web.adapters.dapr import ServiceClient
from web.adapters.http.errors import API_PREFIX

logger = logging.getLogger(__name__)


def thumbnail_path(page_id: str) -> str:
    """Where `web` serves a page's thumbnail: the owning service's path, under `/api`."""
    return f"{API_PREFIX}{get_operation('read_page_thumbnail').path.format(page_id=page_id)}"


def _reading_of(
    page: QueuedPage, listed: ClassificationList | None
) -> Classification | None:
    """The reading of the page by the classifier its case was started with, if listed."""
    if listed is None:
        return None
    for classification in listed.classifications:
        if (
            classification.page_id == page.page_id
            and classification.case_id == page.case_id
            and classification.contender is page.classifier_contender
        ):
            return classification
    return None


def triage_page(page: QueuedPage, listed: ClassificationList | None) -> TriagePage:
    """One waiting page with its reading, or without it when there is none to show."""
    reading = _reading_of(page, listed)
    return TriagePage(
        case_id=page.case_id,
        page_id=page.page_id,
        page_number=page.page_number,
        thumbnail_path=thumbnail_path(page.page_id),
        page_type=reading.page_type if reading is not None else None,
        is_medical=reading.is_medical if reading is not None else None,
        confidence=reading.confidence if reading is not None else None,
        reason=reading.reason if reading is not None else None,
        queued_by=page.queued_by,
    )


class TriageReader:
    """Reads the triage queue: `workflow`'s queue, joined with `classification`'s readings."""

    def __init__(
        self,
        services: ServiceClient,
        *,
        max_concurrent_reads: int,
        deadline_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._services = services
        self._max_concurrent_reads = max_concurrent_reads
        self._deadline_seconds = deadline_seconds
        self._clock = clock

    async def read(self, *, traceparent: str | None) -> TriageQueue:
        """The pages that wait for the underwriter, in the order `workflow` lists them."""
        started = self._clock()
        queue = await self._services.list_pages_by_status(
            PageStatus.AWAITING_TRIAGE, traceparent=traceparent
        )
        readings = await self._readings(
            # Once per case, however many of its pages wait.
            list(dict.fromkeys(page.case_id for page in queue.pages)),
            traceparent=traceparent,
            seconds_left=self._deadline_seconds - (self._clock() - started),
        )
        return TriageQueue(
            pages=[
                triage_page(page, readings.get(page.case_id)) for page in queue.pages
            ],
            has_more=queue.has_more,
        )

    async def _readings(
        self, case_ids: list[str], *, traceparent: str | None, seconds_left: float
    ) -> Mapping[str, ClassificationList]:
        """The classifications of each case that could be read in the time left."""
        if not case_ids:
            return {}
        if seconds_left <= 0:
            # The queue's own read used the deadline up: no reading is begun.
            logger.warning("triage readings late: cases=%d", len(case_ids))
            return {}
        gate = asyncio.Semaphore(self._max_concurrent_reads)

        async def read(case_id: str) -> ClassificationList | None:
            async with gate:
                try:
                    listed = await self._services.list_classifications(
                        case_id, traceparent=traceparent
                    )
                except DomainError as error:
                    # security rule 31: the case and the code. The queue does
                    # not fail as a whole for one case's readings.
                    logger.warning(
                        "triage reading unavailable: case_id=%s code=%s",
                        case_id,
                        error.code.value,
                    )
                    return None
                except Exception as error:  # noqa: BLE001 - whatever went wrong with one case's readings, the queue is still answered
                    # The error's type only; its message can hold anything.
                    logger.error(
                        "triage reading unavailable: case_id=%s type=%s",
                        case_id,
                        type(error).__qualname__,
                    )
                    return None
            if listed.case_id != case_id:
                # Another case's readings are never shown as this case's.
                logger.error(
                    "triage reading unavailable: case_id=%s code=wrong_case", case_id
                )
                return None
            return listed

        tasks = {case_id: asyncio.create_task(read(case_id)) for case_id in case_ids}
        try:
            await asyncio.wait(tasks.values(), timeout=max(seconds_left, 0.0))
        finally:
            # Also when the request itself is cancelled: no reading runs on
            # for an answer nobody waits for.
            late = [task for task in tasks.values() if not task.done()]
            for task in late:
                task.cancel()
            if late:
                await asyncio.gather(*late, return_exceptions=True)
        if late:
            logger.warning("triage readings late: cases=%d", len(late))
        return {
            case_id: listed
            for case_id, task in tasks.items()
            if not task.cancelled() and (listed := task.result()) is not None
        }
