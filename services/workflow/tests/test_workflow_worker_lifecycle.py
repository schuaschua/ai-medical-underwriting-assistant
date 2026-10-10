"""Story 1.6: when the lifecycle worker starts and how the service shuts down."""

import asyncio
import logging
from dataclasses import dataclass, field

import pytest
from workflow_fakes import MemorySchemaRevision

from workflow.adapters.http.app import (
    start_worker_when_ready,
)

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
