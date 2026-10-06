"""Create a case from an uploaded PDF: store the original, then record it (AD-21)."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from contracts.errors import DomainError, ErrorCode
from contracts.models.intake import CaseCreated
from contracts.upload import check_upload
from intake.domain.entities import Document, new_case_with_document
from intake.domain.ports import CaseRepository, OriginalStore

logger = logging.getLogger(__name__)

NOT_STORED_MESSAGE = "The document could not be stored. Please try again."

# How an interrupted or failed upload was left, as the log names it.
RECORDED = "recorded"  # the rows are in the database: the case stands
REMOVED = "removed"  # no rows, and the original is gone again
LEFT_UNREFERENCED = "left_unreferenced"  # no rows; the original could not be removed
KEPT_UNKNOWN = "kept_unknown"  # the database could not say; the original is kept


def utc_now() -> datetime:
    """The current time, in UTC."""
    return datetime.now(UTC)


@dataclass(slots=True)
class _Attempt:
    """One upload under way: what it is called in the log, and how far it got."""

    document: Document
    ids: str
    stage: str = "store_original"
    # Set when the attempt was interrupted and then settled.
    outcome: str | None = None


async def create_case(
    content: bytes,
    *,
    store: OriginalStore,
    repository: CaseRepository,
    now: Callable[[], datetime] = utc_now,
    deadline_seconds: float | None = None,
) -> CaseCreated:
    """Check an upload, store the original and record the case and its document.

    The original is written first, so a failed write leaves no rows. If the
    rows then cannot be written, or the call is cancelled, or
    `deadline_seconds` pass, the upload is settled: the original is removed
    again unless the document's row is in the database after all, in which
    case the case stands. Nothing else happens here: redaction and pages come
    on `workflow`'s command (AD-2).
    """
    check_upload(content)
    started = time.monotonic()
    case, document = new_case_with_document(content, now())
    created = CaseCreated(case_id=case.case_id, document_id=document.document_id)
    attempt = _Attempt(
        document, f"case_id={case.case_id} document_id={document.document_id}"
    )

    try:
        async with asyncio.timeout(deadline_seconds):
            try:
                await store.put(document.original_blob_name, content)
                attempt.stage = "record_case"
                await repository.add(case, document)
            except asyncio.CancelledError:
                # The caller went away, or the deadline passed. The clean-up
                # must not be cancelled with the request, and it logs its own
                # outcome so the line is written even if this task dies first.
                await asyncio.shield(
                    _settle(attempt, store, repository, "CancelledError")
                )
                raise
            except Exception as error:
                outcome = await _settle(
                    attempt, store, repository, type(error).__qualname__
                )
                if outcome != RECORDED:
                    raise DomainError(
                        ErrorCode.UPSTREAM_UNAVAILABLE, NOT_STORED_MESSAGE
                    ) from error
    except TimeoutError:
        if attempt.outcome != RECORDED:
            raise DomainError(
                ErrorCode.UPSTREAM_UNAVAILABLE, NOT_STORED_MESSAGE
            ) from None

    logger.info(
        "case created: %s size_bytes=%d duration_ms=%d",
        attempt.ids,
        document.size_bytes,
        int((time.monotonic() - started) * 1000),
    )
    return created


async def _settle(
    attempt: _Attempt, store: OriginalStore, repository: CaseRepository, cause: str
) -> str:
    """Leave an interrupted upload in a state that holds together, and log it by id.

    security rule 31: the log line carries ids and the error's type, never its message.
    """
    outcome = await _settle_original(attempt, store, repository)
    attempt.outcome = outcome
    log = logger.warning if outcome == RECORDED else logger.error
    log(
        "upload failed: %s stage=%s type=%s original=%s",
        attempt.ids,
        attempt.stage,
        cause,
        outcome,
    )
    return outcome


async def _settle_original(
    attempt: _Attempt, store: OriginalStore, repository: CaseRepository
) -> str:
    document = attempt.document
    if attempt.stage == "record_case":
        # An insert can fail, or be cut off, after its commit reached the
        # database. Removing the original then would leave rows pointing at
        # nothing, so the database is asked first.
        try:
            if await repository.document_exists(document.document_id):
                return RECORDED
        except Exception:  # noqa: BLE001 - whatever the failure, the outcome is reported, not raised
            # A row may point at the original: an unreferenced blob is the
            # lesser harm, and no API serves it either way.
            return KEPT_UNKNOWN
    try:
        await store.delete(document.original_blob_name)
    except Exception:  # noqa: BLE001 - as above
        return LEFT_UNREFERENCED
    return REMOVED
