"""Redact a case's document, then read and split it into pages (AD-21, AD-14, AD-6).

The first stage `workflow` commands. Azure AI Language reads the original and
writes a redacted PDF; from then on only that redacted PDF is read. The
service writes each page of it as one picture, so the page text comes from a
second reading: Document Intelligence's read model is sent the redacted PDF
(owner's decision of 2026-10-10). Nothing here, or anywhere else in the
service, falls back to the original.
"""

import asyncio
import json
import logging
import re
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from contracts.audit import AuditAction, AuditRecord, ai_actor
from contracts.enums import ActorKind, Service, StageStatus
from contracts.errors import NO_TRACE_ID, DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.intake import RedactionCommand, RedactionResult
from contracts.operations import JSON, PDF, PNG
from intake.domain.entities import (
    Document,
    MaskNames,
    NewPage,
    PageRecord,
    case_prefix,
    redacted_blob_name,
    result_blob_name,
    thumbnail_blob_name,
)
from intake.domain.ports import (
    CaseFiles,
    PageReader,
    PageSplitter,
    RedactionJobError,
    RedactionRepository,
    RedactionService,
)
from intake.domain.reading import page_reading
from intake.domain.upload import utc_now

logger = logging.getLogger(__name__)

# AD-8: the actor of a redaction, as `<app id>:<model deployment>`.
REDACTION_ACTOR = ai_actor(Service.INTAKE, "azure-ai-language")

UNKNOWN_CASE_MESSAGE = "That case could not be found."
IN_PROGRESS_MESSAGE = "The document is still being redacted."
NOT_RECORDED_MESSAGE = "The redaction could not be recorded. Please try again."

# What a category is called in the service's result: a name, never a found value.
_CATEGORY_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,63}")
# A mask's label (`PER`) and an entity's number: short names, never found values.
_MASK_LABEL = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}")
_ENTITY_ID = re.compile(r"[0-9]{1,9}")
_ENTITIES = "entities"
_CATEGORY = "category"
_TYPE = "type"
_MASK = "mask"
_ENTITY_ID_KEY = "entityId"


@dataclass(frozen=True, slots=True)
class RedactionPorts:
    """What a redaction works with; the app factory or a test provides it."""

    repository: RedactionRepository
    language: RedactionService
    files: CaseFiles
    splitter: PageSplitter
    reader: PageReader


@dataclass(slots=True)
class _Run:
    """One redaction under way: who it is for, and how far it got."""

    document: Document
    eval_run_id: str | None
    trace_id: str
    started: float
    # How long the failure path waits for the job's cancel to be taken.
    cancel_seconds: float = 10.0
    # Set once the job is submitted, cleared once it has ended by itself.
    job_id: str | None = None

    @property
    def case_id(self) -> str:
        return self.document.case_id


def _entities(result_file: bytes) -> list[object]:
    """Every entry of every `entities` list in the service's result file."""
    try:
        parsed = json.loads(result_file)
    except ValueError:
        raise RedactionJobError("result_not_json") from None
    if not isinstance(parsed, dict):
        raise RedactionJobError("result_not_an_object")
    entities: list[object] = []
    pending: list[object] = [parsed]
    while pending:
        node = pending.pop()
        if isinstance(node, list):
            pending.extend(node)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key == _ENTITIES and isinstance(value, list):
                    entities.extend(value)
                else:
                    pending.append(value)
    return entities


def count_categories(result_file: bytes) -> dict[str, int]:
    """Count the redacted items per category in the service's result file.

    Only category names are read (AD-8): never the text that was found, its
    place or its score. A file that is not the expected JSON is an error.
    """
    counts = Counter(_category_of(entity) for entity in _entities(result_file))
    return dict(sorted(counts.items()))


def mask_names(result_file: bytes) -> MaskNames:
    """What the result file says of the masks drawn on the pages: names only.

    Each entity has the label its mask shows (`PER`), the number the mask
    carries and its category (`Person`). An entity without a usable label is
    passed over: its masks are then not told from the page. Never the text
    that was found.
    """
    by_entity: dict[tuple[str, str], str] = {}
    by_label: dict[str, set[str]] = {}
    for entity in _entities(result_file):
        label = entity.get(_MASK) if isinstance(entity, dict) else None
        if not isinstance(label, str) or _MASK_LABEL.fullmatch(label) is None:
            continue
        category = _category_of(entity)
        by_label.setdefault(label, set()).add(category)
        number = entity.get(_ENTITY_ID_KEY) if isinstance(entity, dict) else None
        if isinstance(number, str) and _ENTITY_ID.fullmatch(number) is not None:
            by_entity[(label, number)] = category
    return MaskNames(
        category_of_entity=by_entity,
        category_of_label={
            label: next(iter(categories))
            for label, categories in by_label.items()
            if len(categories) == 1
        },
        labels=frozenset(by_label),
    )


def _category_of(entity: object) -> str:
    # The service at API version 2026-05-01 names it `type` (seen in the Azure
    # session of 2026-10-10); the preview named it `category`.
    category = (
        entity.get(_TYPE, entity.get(_CATEGORY)) if isinstance(entity, dict) else None
    )
    if not isinstance(category, str) or _CATEGORY_NAME.fullmatch(category) is None:
        # Not a name: it is not copied anywhere, in case it is a found value.
        raise RedactionJobError("result_category_not_a_name")
    return category


def _audit(run: _Run, action: AuditAction, occurred_at: datetime) -> dict[str, object]:
    return {
        "actor_kind": ActorKind.AI,
        "actor": REDACTION_ACTOR,
        "action": action,
        "occurred_at": occurred_at,
        "case_id": run.case_id,
        "page_id": None,
        # The owning record of a redaction is the document it redacts.
        "ref": run.document.document_id,
        "trace_id": run.trace_id,
        "eval_run_id": run.eval_run_id,
    }


def done_result(
    run: _Run, page_ids: Sequence[str], counts: dict[str, int], occurred_at: datetime
) -> RedactionResult:
    """The result of a finished redaction: ids and counts only (AD-6)."""
    return RedactionResult(
        case_id=run.case_id,
        status=StageStatus.DONE,
        error_code=None,
        audit=AuditRecord.model_validate(
            {
                **_audit(run, AuditAction.DOCUMENT_REDACTED, occurred_at),
                "detail": counts,
            }
        ),
        document_id=run.document.document_id,
        page_ids=list(page_ids),
        redaction_counts=counts,
    )


def failed_result(
    run: _Run, error_code: ErrorCode, occurred_at: datetime
) -> RedactionResult:
    """The result of a redaction that failed: no pages, and the code that says why."""
    return RedactionResult(
        case_id=run.case_id,
        status=StageStatus.FAILED,
        error_code=error_code,
        audit=AuditRecord.model_validate(
            {**_audit(run, AuditAction.STAGE_FAILED, occurred_at), "detail": None}
        ),
        document_id=run.document.document_id,
        page_ids=[],
        redaction_counts={},
    )


async def redact_document(
    case_id: str,
    command: RedactionCommand,
    *,
    ports: RedactionPorts,
    categories: Sequence[str],
    deadline_seconds: float,
    stale_margin_seconds: float,
    cancel_seconds: float = 10.0,
    trace_id: str | None = None,
    now: Callable[[], datetime] = utc_now,
) -> RedactionResult:
    """Redact the case's document, read it and split it into pages; idempotent on `case_id`.

    The key row is inserted as running before any work. A repeat while it
    runs is `in_progress`; a repeat after the end is answered with the stored
    result and does no work. After `deadline_seconds` the Language job is
    cancelled, no pages are created and a failed result is stored. A key row
    left running by a process that died is settled as failed, by the first
    repeat that comes `stale_margin_seconds` after its deadline. Ending as
    failed waits at most `cancel_seconds` for the job's cancel, so the answer
    still comes before the caller's own limit (AD-6: 200 s).
    """
    repository = ports.repository
    document = await repository.document_of_case(case_id)
    if document is None:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_CASE_MESSAGE)
    run = _Run(
        document=document,
        eval_run_id=command.eval_run_id,
        trace_id=trace_id or NO_TRACE_ID,
        started=time.monotonic(),
        cancel_seconds=cancel_seconds,
    )
    earlier = await repository.begin(case_id, document.document_id, now())
    if earlier is not None:
        if earlier.result_json is not None:
            logger.info("redaction repeated: case_id=%s", case_id)
            return RedactionResult.model_validate_json(earlier.result_json)
        limit = timedelta(seconds=deadline_seconds + stale_margin_seconds)
        if now() - earlier.started_at <= limit:
            raise DomainError(ErrorCode.IN_PROGRESS, IN_PROGRESS_MESSAGE)
        # Nothing is working on it any more. The case fails, as it would
        # have at its deadline, and the customer uploads again.
        run.job_id = earlier.job_id
        return await asyncio.shield(
            _fail(run, ErrorCode.STAGE_TIMEOUT, "stale", ports, now)
        )

    # One deadline for everything: the job, the reading of the redacted
    # PDF, the page split and the storing of the result.
    deadline = asyncio.timeout(deadline_seconds)
    try:
        async with deadline:
            return await _redact_and_split(run, ports, categories, now)
    except asyncio.CancelledError:
        # The caller went away or the service is stopping.
        error_code, reason = ErrorCode.REDACTION_FAILED, "cancelled"
        cancelled = True
    except DomainError:
        raise
    except Exception as error:  # noqa: BLE001 - whatever failed, the redaction ends as failed
        cancelled = False
        if isinstance(error, TimeoutError) and deadline.expired():
            error_code, reason = ErrorCode.STAGE_TIMEOUT, "deadline"
        else:
            # A timeout of one call, before the deadline, is a failure like
            # any other. security rule 31: a code or the error's type, never
            # its message.
            error_code = ErrorCode.REDACTION_FAILED
            reason = (
                error.reason
                if isinstance(error, RedactionJobError)
                else type(error).__qualname__
            )
    # The redaction is settled whatever happens to the request from here on:
    # a request cancelled now must not leave the key row `running`.
    settled = asyncio.shield(_fail(run, error_code, reason, ports, now))
    if cancelled:
        await settled
        raise asyncio.CancelledError
    return await settled


async def _redact_and_split(
    run: _Run,
    ports: RedactionPorts,
    categories: Sequence[str],
    now: Callable[[], datetime],
) -> RedactionResult:
    case_id, document = run.case_id, run.document
    # The one use of the original: the redaction service is told where it is.
    run.job_id = await ports.language.start(
        document.original_blob_name, case_id, categories
    )
    await ports.repository.note_job(case_id, run.job_id)
    output = await ports.language.output(run.job_id, case_id)
    run.job_id = None

    # From here on only the redacted PDF is read (AD-21).
    pdf = await ports.files.read(output.redacted_blob_name)
    result_file = await ports.files.read(output.result_blob_name)
    counts = count_categories(result_file)
    names = mask_names(result_file)
    sheets = await ports.splitter.split(pdf)
    if not sheets:
        raise RedactionJobError("no_pages")
    # The redacted PDF's pages are pictures: its text is read from them by
    # the read model, once for the whole document. A reading that fails
    # fails the redaction: no page is ever stored without its text.
    read = sorted(await ports.reader.read(pdf), key=lambda page: page.page_number)
    if [page.page_number for page in read] != list(range(1, len(sheets) + 1)):
        raise RedactionJobError("read_page_count")
    readings = [
        page_reading(page, sheet, names)
        for page, sheet in zip(read, sheets, strict=True)
    ]

    # The files of record get names of our own under `<case_id>/`, wherever
    # in the container the service put them.
    redacted_name = redacted_blob_name(case_id, document.document_id)
    kept = {
        redacted_name: (pdf, PDF),
        result_blob_name(case_id, document.document_id): (result_file, JSON),
    }
    for name, (content, content_type) in kept.items():
        await ports.files.put(name, content, content_type)
    for name in {output.redacted_blob_name, output.result_blob_name} - kept.keys():
        try:
            await ports.files.delete(name)
        except Exception as error:  # noqa: BLE001 - a second copy under the case's prefix; the redaction is good
            logger.warning(
                "redaction output not removed: case_id=%s document_id=%s type=%s",
                case_id,
                document.document_id,
                type(error).__qualname__,
            )

    pages: list[NewPage] = []
    for number, (sheet, reading) in enumerate(
        zip(sheets, readings, strict=True), start=1
    ):
        page_id = new_id()
        thumbnail_name = thumbnail_blob_name(case_id, page_id)
        await ports.files.put(thumbnail_name, sheet.thumbnail, PNG)
        pages.append(
            NewPage(
                record=PageRecord(
                    page_id=page_id,
                    case_id=case_id,
                    document_id=document.document_id,
                    page_number=number,
                    width=sheet.width,
                    height=sheet.height,
                    thumbnail_blob_name=thumbnail_name,
                ),
                text=reading.text,
                words=reading.words,
            )
        )
    result = done_result(run, [page.record.page_id for page in pages], counts, now())
    stored = RedactionResult.model_validate_json(
        await ports.repository.finish(
            case_id, result.model_dump_json(), pages, redacted_name
        )
    )
    if stored != result:
        # The redaction was settled as failed by another call first: that
        # result stands, no page of this one was stored, and its files go.
        if stored.status is StageStatus.FAILED:
            await _remove_files(run, ports.files)
        logger.warning("redaction superseded: case_id=%s", case_id)
        return stored
    for code, odd in (
        ("no_items_redacted", not counts),
        ("no_page_text", not any(page.text.strip() for page in pages)),
        # A mask the read model saw nothing of has no token in the page text.
        ("masks_unread", any(reading.masks_unread for reading in readings)),
    ):
        if odd:
            # Done all the same; a document like that deserves a look.
            logger.warning("redaction unusual: case_id=%s code=%s", case_id, code)
    logger.info(
        "document redacted: case_id=%s document_id=%s pages=%d items=%d "
        "categories=%d words=%d masks=%d masks_unread=%d duration_ms=%d",
        case_id,
        document.document_id,
        len(pages),
        sum(counts.values()),
        len(counts),
        sum(len(reading.words) for reading in readings),
        sum(reading.masks for reading in readings),
        sum(reading.masks_unread for reading in readings),
        int((time.monotonic() - run.started) * 1000),
    )
    return stored


async def _fail(
    run: _Run,
    error_code: ErrorCode,
    reason: str,
    ports: RedactionPorts,
    now: Callable[[], datetime],
) -> RedactionResult:
    """End a redaction as failed: stop the job, store the result, remove the files."""
    case_id = run.case_id
    job = "none"
    if run.job_id is not None:
        try:
            # Bounded: the deadline has passed already, and the caller's own
            # limit is not far behind it.
            async with asyncio.timeout(run.cancel_seconds):
                await ports.language.cancel(run.job_id)
            job = "cancelled"
        except Exception as error:  # noqa: BLE001 - the redaction fails either way; the outcome is logged
            job = f"not_cancelled:{type(error).__qualname__}"
    result = failed_result(run, error_code, now())
    try:
        stored = RedactionResult.model_validate_json(
            await ports.repository.finish(case_id, result.model_dump_json(), (), None)
        )
    except Exception as error:
        # The key row stays `running`; a later repeat settles it.
        logger.error(
            "redaction failed: case_id=%s error_code=%s reason=%s job=%s "
            "stored=false type=%s",
            case_id,
            error_code.value,
            reason,
            job,
            type(error).__qualname__,
        )
        raise DomainError(
            ErrorCode.UPSTREAM_UNAVAILABLE, NOT_RECORDED_MESSAGE
        ) from error
    if stored.status is StageStatus.FAILED:
        # No page was stored, so nothing may be left to read.
        await _remove_files(run, ports.files)
    logger.error(
        "redaction failed: case_id=%s error_code=%s reason=%s job=%s stored=%s "
        "duration_ms=%d",
        case_id,
        error_code.value,
        reason,
        job,
        stored.status.value,
        int((time.monotonic() - run.started) * 1000),
    )
    return stored


async def _remove_files(run: _Run, files: CaseFiles) -> None:
    try:
        await files.delete_all(case_prefix(run.case_id))
    except Exception as error:  # noqa: BLE001 - files no row points at; no route serves them
        logger.warning(
            "redaction files not removed: case_id=%s type=%s",
            run.case_id,
            type(error).__qualname__,
        )
