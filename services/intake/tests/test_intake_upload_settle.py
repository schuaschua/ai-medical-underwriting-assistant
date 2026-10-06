"""Story 1.5: an upload that fails, is cancelled or runs out of time is left consistent.

Either the document's row exists and its original is kept, or neither is
there; and every such ending is logged by id.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from intake_fakes import MemoryCaseRepository, MemoryOriginalStore

from contracts.errors import DomainError, ErrorBody, ErrorCode
from contracts.models.intake import CaseCreated
from intake.adapters.http.app import create_app
from intake.adapters.http.routes import Dependencies
from intake.domain.upload import create_case
from intake.settings import Settings

PDF = b"%PDF-1.7\n%%EOF\n"
HEADERS = {"Content-Type": "application/pdf"}


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


def test_story_1_5_a_commit_that_landed_before_the_error_keeps_the_original(
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository.commit_then_fail = True

    with caplog.at_level(logging.INFO):
        created = asyncio.run(create_case(PDF, store=store, repository=repository))

    # The rows are there, so the case stands and its original is not removed.
    assert isinstance(created, CaseCreated)
    assert store.deleted == []
    assert store.blobs == {f"{created.case_id}/{created.document_id}.pdf": PDF}
    assert [d.document_id for d in repository.documents] == [created.document_id]
    lines = [r.getMessage() for r in caplog.records]
    assert any(
        f"case_id={created.case_id} document_id={created.document_id} "
        "stage=record_case type=StoreDown original=recorded" in line
        for line in lines
    )
    assert any(line.startswith("case created:") for line in lines)


def test_story_1_5_an_insert_that_failed_removes_the_original_only_after_asking(
    store: MemoryOriginalStore, repository: MemoryCaseRepository
) -> None:
    repository.fail = True

    with pytest.raises(DomainError) as raised:
        asyncio.run(create_case(PDF, store=store, repository=repository))

    assert raised.value.code is ErrorCode.UPSTREAM_UNAVAILABLE
    assert store.blobs == {}
    assert len(store.deleted) == 1


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


def test_story_1_5_cancelled_while_recording_removes_the_original_and_logs_by_id(
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository.hang = True

    run_cancelled(store, repository, repository.entered.wait)

    assert store.blobs == {}
    assert repository.documents == []
    (blob_name,) = store.deleted
    case_id, document_id = blob_name.removesuffix(".pdf").split("/")
    line = only_error_line(caplog, logging.ERROR)
    assert f"case_id={case_id} document_id={document_id}" in line
    assert "stage=record_case type=CancelledError original=removed" in line


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


def test_story_1_5_cancelled_while_storing_removes_whatever_was_written(
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store.hang_put = True

    run_cancelled(store, repository, store.entered.wait)

    assert store.blobs == {}
    assert len(store.deleted) == 1
    assert repository.documents == []
    assert "stage=store_original type=CancelledError original=removed" in (
        only_error_line(caplog, logging.ERROR)
    )


def test_story_1_5_the_clean_up_finishes_even_if_it_is_cancelled_again(
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository.hang = True
    release = asyncio.Event()
    plain_delete = store.delete

    async def slow_delete(blob_name: str) -> None:
        await release.wait()
        await plain_delete(blob_name)

    async def scenario() -> None:
        store.delete = slow_delete  # type: ignore[method-assign]  # a slower stand-in for this test
        upload = asyncio.create_task(
            create_case(PDF, store=store, repository=repository)
        )
        await repository.entered.wait()
        upload.cancel()
        await asyncio.sleep(0)
        # A second cancellation arrives while the clean-up is under way.
        upload.cancel()
        with pytest.raises(asyncio.CancelledError):
            await upload
        assert store.blobs != {}
        release.set()
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.run(scenario())

    assert store.blobs == {}
    assert "type=CancelledError original=removed" in caplog.text


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


def test_story_1_5_a_storage_call_that_hangs_ends_at_the_deadline(
    store: MemoryOriginalStore, repository: MemoryCaseRepository
) -> None:
    store.hang_put = True

    with pytest.raises(DomainError):
        asyncio.run(
            create_case(PDF, store=store, repository=repository, deadline_seconds=0.05)
        )

    assert store.blobs == {}
    assert repository.documents == []


def test_story_1_5_a_case_recorded_just_as_the_deadline_passes_stands(
    store: MemoryOriginalStore, repository: MemoryCaseRepository
) -> None:
    repository.commit_then_hang = True

    created = asyncio.run(
        create_case(PDF, store=store, repository=repository, deadline_seconds=0.05)
    )

    assert [d.document_id for d in repository.documents] == [created.document_id]
    assert store.deleted == []


def test_story_1_5_the_route_applies_the_deadline_from_the_settings(
    settings: Settings, dependencies: Dependencies, repository: MemoryCaseRepository
) -> None:
    repository.hang = True
    app = create_app(
        settings, dependencies=replace(dependencies, upload_deadline_seconds=0.05)
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/cases", content=PDF, headers=HEADERS)

    assert response.status_code == 502
    assert ErrorBody.model_validate(response.json()).error.code.value == (
        "upstream_unavailable"
    )


def test_story_1_5_intakes_deadline_is_shorter_than_webs() -> None:
    # The order the settings comment states: intake < web < browser.
    assert Settings().upload_deadline_seconds == 90.0
    assert Dependencies.__dataclass_fields__["upload_deadline_seconds"].default == 90.0
