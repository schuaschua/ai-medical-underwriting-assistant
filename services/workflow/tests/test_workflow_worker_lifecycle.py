"""Story 1.6: when the lifecycle worker starts and how the service shuts down."""

import asyncio
import logging
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient
from workflow_fakes import MemorySchemaRevision, MemoryTrailGuard

from contracts.errors import ErrorBody
from workflow.adapters.http import app as app_module
from workflow.adapters.http.app import (
    create_app,
    running_worker,
    start_worker_when_ready,
)
from workflow.adapters.migrations import bundled_head
from workflow.settings import Settings

HEAD = "0002"


@dataclass
class FakeWorker:
    events: list[str] = field(default_factory=list)
    fail_stop: bool = False

    def start(self) -> None:
        self.events.append("start")

    def stop(self) -> None:
        self.events.append("stop")
        if self.fail_stop:
            raise RuntimeError("secret-worker-detail")


def test_story_1_6_the_worker_starts_only_once_the_schema_is_at_the_bundled_head(
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = FakeWorker()
    revision = MemorySchemaRevision(None, fail=True)
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        # Each wait, the pipeline gets one step further.
        waits.append(seconds)
        assert worker.events == []
        if len(waits) == 1:
            revision.fail = False  # the database is reachable, not migrated
        elif len(waits) == 2:
            revision.revision = "0001"  # one migration behind
        else:
            revision.revision = HEAD

    with caplog.at_level(logging.INFO):
        asyncio.run(start_worker_when_ready(worker, revision, HEAD, 7.0, sleep))

    assert worker.events == ["start"]
    assert waits == [7.0, 7.0, 7.0]
    assert "lifecycle worker waiting: database type=StoreDown" in caplog.text
    assert "lifecycle worker waiting: schema_revision=0001 head_revision=0002" in (
        caplog.text
    )
    assert "secret-store-detail" not in caplog.text


def test_story_1_6_a_worker_that_never_started_is_not_left_waiting_at_shutdown() -> (
    None
):
    worker = FakeWorker()

    async def scenario() -> None:
        async with running_worker(
            worker,
            MemorySchemaRevision(None),
            HEAD,
            check_seconds=3600.0,
            shutdown_seconds=5.0,
        ):
            await asyncio.sleep(0)

    asyncio.run(scenario())

    # Never started; the wait for the schema is ended, and stop is harmless.
    assert worker.events == ["stop"]


def test_story_1_6_shutdown_goes_on_when_the_worker_cannot_be_stopped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = FakeWorker(fail_stop=True)

    async def scenario() -> None:
        async with running_worker(
            worker,
            MemorySchemaRevision(HEAD),
            HEAD,
            check_seconds=1.0,
            shutdown_seconds=5.0,
        ):
            for _ in range(5):
                await asyncio.sleep(0)

    with caplog.at_level(logging.ERROR):
        asyncio.run(scenario())

    assert worker.events == ["start", "stop"]
    assert "shutdown step failed: step=worker type=RuntimeError" in caplog.text
    assert "secret-worker-detail" not in caplog.text


@dataclass
class Closing:
    """Stands in for the scheduler client's owner and for the database at shutdown."""

    closed: list[str]
    name: str
    fail: bool = False
    hang: bool = False

    async def _close(self) -> None:
        self.closed.append(self.name)
        if self.hang:
            await asyncio.Event().wait()
        if self.fail:
            raise RuntimeError("secret-close-detail")

    async def aclose(self) -> None:
        await self._close()

    async def dispose(self) -> None:
        await self._close()


@pytest.mark.parametrize("trouble", ["worker", "client-fails", "client-hangs", "body"])
def test_story_1_6_every_resource_is_closed_whatever_fails_before_it(
    monkeypatch: pytest.MonkeyPatch, trouble: str
) -> None:
    closed: list[str] = []
    worker = FakeWorker(fail_stop=trouble == "worker")
    engine = Closing(
        closed, "client", fail=trouble == "client-fails", hang=trouble == "client-hangs"
    )
    database = Closing(closed, "database")
    monkeypatch.setattr(app_module, "build_database", lambda _settings: database)
    monkeypatch.setattr(app_module, "build_client", lambda _settings: None)
    monkeypatch.setattr(app_module, "SchedulerEngine", lambda _client: engine)
    monkeypatch.setattr(app_module, "build_worker", lambda _settings, _acts: worker)
    monkeypatch.setattr(
        app_module,
        "SqlSchemaRevision",
        lambda _db: MemorySchemaRevision(bundled_head()),
    )
    monkeypatch.setattr(app_module, "SqlTrailGuard", lambda _db: MemoryTrailGuard())
    monkeypatch.setattr(app_module, "SqlCaseStore", lambda _db: object())
    app = create_app(
        Settings(
            applicationinsights_connection_string=None, shutdown_timeout_seconds=0.2
        )
    )

    if trouble == "body":
        with pytest.raises(KeyError), TestClient(app):
            raise KeyError("the app's own run failed")
    else:
        with TestClient(app) as client:
            assert client.get("/ready").status_code == 200

    # Worker first, then the client, then the database: each one reached.
    assert worker.events[-1] == "stop"
    assert closed == ["client", "database"]


def test_story_1_6_not_ready_when_the_service_role_could_change_the_audit_trail(
    client: TestClient,
    trail_guard: MemoryTrailGuard,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert client.get("/ready").status_code == 200

    trail_guard.writable = True
    with caplog.at_level(logging.WARNING):
        response = client.get("/ready")

    assert response.status_code == 502
    assert ErrorBody.model_validate(response.json()).error.code.value == (
        "upstream_unavailable"
    )
    # Its own log code: this is a wrong database role, not a missing migration.
    assert "not ready: code=audit_trail_writable" in caplog.text
    assert client.get("/health").status_code == 200

    trail_guard.writable = False
    trail_guard.fail = True
    assert client.get("/ready").status_code == 502
    assert "secret-store-detail" not in caplog.text
