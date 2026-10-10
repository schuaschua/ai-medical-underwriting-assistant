"""Story 1.5: an upload that fails, is cancelled or runs out of time is left consistent.

Either the document's row exists and its original is kept, or neither is
there; and every such ending is logged by id.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import pytest
from intake_fakes import MemoryCaseRepository, MemoryOriginalStore

from contracts.errors import DomainError, ErrorCode
from intake.domain.upload import create_case

PDF = b"%PDF-1.7\n%%EOF\n"


def run_cancelled(
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    started: Callable[[], Awaitable[object]],
) -> None:
    """Start an upload, cancel it once it is under way, and wait for it to end."""

    async def scenario() -> None:
        upload = asyncio.create_task(
            create_case(PDF, store=store, repository=repository)
        )
        await started()
        # Let the call it is waiting in settle (a commit, in one of the tests).
        await asyncio.sleep(0)
        upload.cancel()
        with pytest.raises(asyncio.CancelledError):
            await upload

    asyncio.run(scenario())


def only_error_line(caplog: pytest.LogCaptureFixture, level: int) -> str:
    (record,) = [r for r in caplog.records if r.name == "intake.domain.upload"]
    assert record.levelno == level
    return record.getMessage()


# --- The insert fails ---------------------------------------------------------


def test_story_1_5_when_the_database_cannot_say_the_original_is_kept(
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository.fail = True
    repository.fail_exists = True

    with pytest.raises(DomainError):
        asyncio.run(create_case(PDF, store=store, repository=repository))

    # A row may point at it: an unreferenced blob is the lesser harm.
    assert store.deleted == []
    (blob_name,) = store.blobs
    line = only_error_line(caplog, logging.ERROR)
    assert f"case_id={blob_name.split('/')[0]}" in line
    assert "original=kept_unknown" in line
    assert "secret-store-detail" not in caplog.text


# --- The request is cancelled -------------------------------------------------


def test_story_1_5_cancelled_after_the_commit_landed_keeps_the_original(
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository.commit_then_hang = True

    run_cancelled(store, repository, repository.entered.wait)

    assert store.deleted == []
    (document,) = repository.documents
    assert document.original_blob_name in store.blobs
    line = only_error_line(caplog, logging.WARNING)
    assert f"document_id={document.document_id}" in line
    assert "type=CancelledError original=recorded" in line


# --- The deadline passes ------------------------------------------------------


def test_story_1_5_past_its_deadline_the_stored_original_is_undone(
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository.hang = True

    with pytest.raises(DomainError) as raised:
        asyncio.run(
            create_case(PDF, store=store, repository=repository, deadline_seconds=0.05)
        )

    assert raised.value.code is ErrorCode.UPSTREAM_UNAVAILABLE
    assert store.blobs == {}
    assert len(store.deleted) == 1
    assert "stage=record_case type=CancelledError original=removed" in caplog.text
