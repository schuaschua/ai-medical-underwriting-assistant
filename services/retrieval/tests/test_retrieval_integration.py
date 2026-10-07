"""Story 2.2, against a real PostgreSQL and the blob emulator.

Run `docker compose up --detach --wait` first. No test here calls Azure or a
model. The job runs as it really runs (`retrieval.ingest.main`), with a
transport where Document Intelligence would be and one where the Foundry
deployments would be, over a made-up manual. The same job over the project's
manual, with the stand-ins, is tested beside the stand-ins (`packages/`
tests, story 2.2).
"""

import asyncio
import hashlib
import json
import logging
import secrets
import socket
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx2
import psycopg
import pytest
from azure.core.exceptions import ResourceNotFoundError
from pydantic import SecretStr
from retrieval_fakes import (
    CHAT,
    EMBEDDING,
    PDF,
    RULE_A,
    RULE_B,
    RULE_C,
    analyze_result,
    completion,
    context_answer,
    definition,
    embedding_answer,
    manual,
    options,
    vector_for,
)

from contracts.enums import ChunkSet
from retrieval import ingest as ingest_job
from retrieval.adapters.blob import (
    build_blob_service,
    upload_local_manual,
)
from retrieval.adapters.db import (
    SqlChunkRepository,
    build_database,
)
from retrieval.adapters.migrations import bundled_head
from retrieval.domain.chunker import cut_chunks
from retrieval.domain.entities import (
    ChunkRecord,
    IngestRun,
    ParsedLayout,
)
from retrieval.domain.ingest import fingerprint
from retrieval.domain.ports import IndexChanged
from retrieval.ingest import Transports
from retrieval.settings import Settings

pytestmark = pytest.mark.integration

EMULATOR = "UseDevelopmentStorage=true"
# Every stored chunk, whole: its columns, when it was written and its row version.
ALL_CHUNKS = (
    "SELECT chunk_id, chunk_set, rule_ids, reference_rule_ids, section_id, "
    "impairment, manual_page, text, context_line, content_hash, chat_deployment, "
    "embedding_deployment, embedding::text, ingested_at, xmin::text "
    "FROM retrieval.chunk ORDER BY chunk_id"
)


def connect(settings: Settings, **options: Any) -> psycopg.Connection[Any]:
    return psycopg.connect(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=settings.database_user,
        **options,
    )


def query(settings: Settings, statement: str, *parameters: object) -> list[Any]:
    with connect(settings) as connection:
        return connection.execute(statement, parameters).fetchall()


def chunks(settings: Settings) -> dict[str, tuple[Any, ...]]:
    """Every stored chunk, whole, by its id."""
    return {row[0]: row for row in query(settings, ALL_CHUNKS)}


# --- Migrations and readiness -----------------------------------------------------------


def test_story_2_2_the_migration_keeps_everything_in_schema_retrieval(
    migrated_database: Settings,
) -> None:
    tables = query(
        migrated_database,
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
        "ORDER BY table_name",
    )

    # One schema, its own version table (AD-4), and no foreign key at all.
    assert tables == [
        ("retrieval", "alembic_version"),
        ("retrieval", "chunk"),
        ("retrieval", "ingest_run"),
    ]
    assert query(
        migrated_database, "SELECT version_num FROM retrieval.alembic_version"
    ) == [(bundled_head(),)]
    assert query(
        migrated_database,
        "SELECT count(*) FROM information_schema.table_constraints "
        "WHERE table_schema = 'retrieval' AND constraint_type = 'FOREIGN KEY'",
    ) == [(0,)]
    # The migration brought the `vector` extension with it.
    assert query(
        migrated_database, "SELECT count(*) FROM pg_extension WHERE extname = 'vector'"
    ) == [(1,)]


def test_story_2_2_the_table_is_ready_for_hybrid_search_and_has_no_approximate_index(
    migrated_database: Settings,
) -> None:
    columns = dict(
        query(
            migrated_database,
            "SELECT a.attname, format_type(a.atttypid, a.atttypmod) "
            "FROM pg_attribute a WHERE a.attrelid = 'retrieval.chunk'::regclass "
            "AND a.attnum > 0",
        )
    )
    indexes = dict(
        query(
            migrated_database,
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'retrieval' "
            "AND tablename = 'chunk'",
        )
    )

    # AD-12: 3,072 dimensions, and a stored full-text column built by the migration.
    assert columns["embedding"] == "vector(3072)"
    assert columns["text_search"] == "tsvector"
    assert query(
        migrated_database,
        "SELECT is_generated FROM information_schema.columns WHERE "
        "table_schema = 'retrieval' AND column_name = 'text_search'",
    ) == [("ALWAYS",)]
    assert "USING gin (text_search)" in indexes["ix_chunk_text_search"]
    # Search is exact: no index of any kind on the vectors.
    assert not any("embedding" in definition for definition in indexes.values())
    assert not any("hnsw" in d or "ivfflat" in d for d in indexes.values())


# --- The repository -------------------------------------------------------------------------


@contextmanager
def a_repository(
    settings: Settings,
) -> Iterator[tuple[SqlChunkRepository, asyncio.Runner]]:
    with asyncio.Runner() as runner:
        database = build_database(settings)
        try:
            yield SqlChunkRepository(database), runner
        finally:
            runner.run(database.dispose())


def records(parsed: ParsedLayout | None = None) -> list[ChunkRecord]:
    return [
        ChunkRecord(
            chunk=chunk,
            context_line=f"Where {chunk.rule_id} sits.",
            embedding=tuple(vector_for(chunk.text)),
            content_hash=fingerprint(chunk, options()),
            chat_deployment=CHAT,
            embedding_deployment=EMBEDDING,
        )
        for chunk in cut_chunks(parsed or manual())
    ]


def run_note(manual_sha256: str = "a" * 64, chunk_count: int = 3) -> IngestRun:
    return IngestRun(
        manual_sha256=manual_sha256,
        prompt_digest="digest-1",
        chat_deployment=CHAT,
        embedding_deployment=EMBEDDING,
        chunk_count=chunk_count,
    )


async def store(
    repository: SqlChunkRepository,
    chunk_set: ChunkSet = ChunkSet.SMART,
    *,
    write: Sequence[ChunkRecord] = (),
    move: Mapping[str, int] | None = None,
    remove: Sequence[str] = (),
    run: IngestRun | None = None,
) -> None:
    """One run's apply, planned from what is stored when it starts."""
    await repository.apply(
        chunk_set,
        planned_from=await repository.stored(chunk_set),
        write=write,
        move=move or {},
        remove=remove,
        run=run or run_note(),
    )


def test_story_2_2_a_run_is_one_transaction_and_touches_only_what_it_names(
    migrated_database: Settings,
) -> None:
    first, second, third = records()
    with a_repository(migrated_database) as (repository, runner):
        runner.run(store(repository, write=[first, second, third]))
        before = chunks(migrated_database)
        rewritten = replace(first, context_line="Another line.", content_hash="h2")

        runner.run(
            store(
                repository,
                write=[rewritten],
                move={second.chunk.chunk_id: 9},
                remove=[third.chunk.chunk_id],
                run=run_note("b" * 64, 2),
            )
        )
        after = chunks(migrated_database)
        # A run of another chunk set cannot move or remove these.
        runner.run(
            store(
                repository,
                ChunkSet.FIXED,
                move={second.chunk.chunk_id: 1},
                remove=[first.chunk.chunk_id],
                run=run_note("f" * 64, 0),
            )
        )
        assert runner.run(repository.stored(ChunkSet.FIXED)) == {}

        # A write the database refuses takes the whole run with it, the note
        # of what the set was built from included.
        broken = replace(third, embedding=(1.0, 2.0))
        with pytest.raises(Exception) as raised:
            runner.run(
                store(
                    repository,
                    write=[replace(rewritten, context_line="Never stored."), broken],
                    move={second.chunk.chunk_id: 77},
                    remove=[first.chunk.chunk_id],
                    run=run_note("c" * 64),
                )
            )
        noted = runner.run(repository.last_run(ChunkSet.SMART))

    assert sorted(after) == [f"smart-{RULE_A}", f"smart-{RULE_B}"]
    assert after[f"smart-{RULE_A}"][8:10] == ("Another line.", "h2")
    assert after[f"smart-{RULE_B}"][6] == 9
    # The moved chunk kept its text, context line and vector.
    assert after[f"smart-{RULE_B}"][7:13] == before[f"smart-{RULE_B}"][7:13]
    # Nothing of the refused run is there, and its values are in no message.
    assert chunks(migrated_database) == after
    assert noted == run_note("b" * 64, 2)
    assert "Never stored." not in str(raised.value)


def test_story_2_2_of_two_runs_at_once_the_second_writes_nothing(
    migrated_database: Settings,
) -> None:
    first, second, third = records()
    one = replace(first, context_line="The line of run one.")
    two = replace(first, context_line="The line of run two.")

    async def overlapping() -> list[Any]:
        database_one = build_database(migrated_database)
        database_two = build_database(migrated_database)
        try:
            run_one = SqlChunkRepository(database_one)
            run_two = SqlChunkRepository(database_two)
            await store(run_one, write=[first, second, third])
            # Both runs read the index before either writes, as two jobs
            # started together do.
            planned_one = await run_one.stored(ChunkSet.SMART)
            planned_two = await run_two.stored(ChunkSet.SMART)
            return list(
                await asyncio.gather(
                    run_one.apply(
                        ChunkSet.SMART,
                        planned_from=planned_one,
                        write=[one],
                        move={},
                        remove=[third.chunk.chunk_id],
                        run=run_note("1" * 64, 2),
                    ),
                    run_two.apply(
                        ChunkSet.SMART,
                        planned_from=planned_two,
                        write=[two, third],
                        move={second.chunk.chunk_id: 50},
                        remove=[],
                        run=run_note("2" * 64, 3),
                    ),
                    return_exceptions=True,
                )
            )
        finally:
            await database_one.dispose()
            await database_two.dispose()

    outcomes = asyncio.run(overlapping())

    # One of them got the lock and wrote; the other waited, found the index
    # changed under its plan, and wrote nothing: neither undid the other.
    refused = [outcome for outcome in outcomes if isinstance(outcome, IndexChanged)]
    assert len(refused) == 1
    assert [outcome for outcome in outcomes if outcome is not None] == refused
    stored = chunks(migrated_database)
    (noted,) = query(
        migrated_database, "SELECT manual_sha256 FROM retrieval.ingest_run"
    )
    if outcomes[1] is refused[0]:
        assert stored[f"smart-{RULE_A}"][8] == "The line of run one."
        assert sorted(stored) == [f"smart-{RULE_A}", f"smart-{RULE_B}"]
        assert stored[f"smart-{RULE_B}"][6] == 3
        assert noted == ("1" * 64,)
    else:
        assert stored[f"smart-{RULE_A}"][8] == "The line of run two."
        assert len(stored) == 3 and stored[f"smart-{RULE_B}"][6] == 50
        assert noted == ("2" * 64,)


# --- The job, as it really runs ------------------------------------------------------------


@dataclass
class Azure:
    """Stands where Document Intelligence and the Foundry deployments would be."""

    parsed: ParsedLayout = field(default_factory=manual)
    layout_status: int = 202
    analysis: str = "succeeded"
    model_status: int = 200
    context: Any = field(default_factory=context_answer)
    dimensions: int | None = None
    chat_calls: int = 0
    embedding_calls: int = 0
    layout_calls: int = 0
    submitted: list[bytes] = field(default_factory=list)

    def layout(self, request: httpx2.Request) -> httpx2.Response:
        self.layout_calls += 1
        if request.method == "POST":
            self.submitted.append(request.content)
            if self.layout_status != 202:
                return httpx2.Response(self.layout_status, json={})
            return httpx2.Response(
                202,
                headers={
                    "operation-location": "http://127.0.0.1:5102/documentintelligence"
                    "/documentModels/prebuilt-layout/analyzeResults/r1"
                },
            )
        body: dict[str, Any] = {"status": self.analysis}
        if self.analysis == "succeeded":
            body["analyzeResult"] = analyze_result(self.parsed)
        return httpx2.Response(200, json=body)

    def model(self, request: httpx2.Request) -> httpx2.Response:
        embedding = request.url.path.endswith("/embeddings")
        if embedding:
            self.embedding_calls += 1
        else:
            self.chat_calls += 1
        if self.model_status != 200:
            return httpx2.Response(
                self.model_status, headers={"retry-after": "0"}, json={}
            )
        if embedding:
            texts = json.loads(request.content)["input"]
            return httpx2.Response(200, json=embedding_answer(texts, self.dimensions))
        return httpx2.Response(200, json=completion(self.context))

    @property
    def model_calls(self) -> int:
        return self.chat_calls + self.embedding_calls

    def transports(self) -> Transports:
        return Transports(
            layout=httpx2.MockTransport(self.layout),
            model=httpx2.MockTransport(self.model),
        )


@pytest.fixture
def job_settings(migrated_database: Settings, tmp_path: Path) -> Iterator[Settings]:
    """The job's settings: this test's database, and a manual container of its own."""
    if not blob_emulator_listening():
        pytest.fail(
            "Integration tests need the blob emulator: run "
            "`docker compose up --detach --wait` first.",
            pytrace=False,
        )
    settings = migrated_database.model_copy(
        update={
            "blob_connection_string": SecretStr(EMULATOR),
            "manual_container": f"manual-test-{secrets.token_hex(6)}",
            "layout_poll_seconds": 0.01,
            # The made-up manual has three rules: losing one is a third of them.
            "ingest_max_removed_share": 0.5,
        }
    )
    upload(settings, tmp_path, PDF)
    try:
        yield settings
    finally:
        with suppress(ResourceNotFoundError):
            build_blob_service(settings).delete_container(settings.manual_container)


def blob_emulator_listening() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 10000), timeout=1):
            return True
    except OSError:
        return False


def upload(settings: Settings, folder: Path, content: bytes) -> None:
    """Put a manual of these bytes into the test's container, in place of the one there."""
    pdf = folder / "manual.pdf"
    pdf.write_bytes(content)
    upload_local_manual(settings, pdf)


CHANGED_PDF = PDF + b" in another edition"


def run_job(settings: Settings, azure: Azure) -> int:
    return ingest_job.main(settings, azure.transports())


def test_story_2_2_the_job_ingests_the_manual_from_its_container(
    job_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    azure = Azure()

    with caplog.at_level(logging.INFO):
        status = run_job(job_settings, azure)

    assert status == 0
    stored = chunks(job_settings)
    # One `smart` chunk per rule the manual defines.
    assert sorted(stored) == [f"smart-{RULE_A}", f"smart-{RULE_B}", f"smart-{RULE_C}"]
    assert {row[1] for row in stored.values()} == {"smart"}
    # The manual's bytes, as they are in the container, went to the layout model.
    (submitted,) = azure.submitted
    assert (
        json.loads(submitted)["base64Source"] == "JVBERi0xLjcgYSBtYWRlLXVwIG1hbnVhbA=="
    )
    # One chat call per chunk; one small embedding call before them, and
    # the embeddings in one batch after them.
    assert azure.chat_calls == 3
    assert azure.embedding_calls == 2
    # The index says which manual it was built from, and with what.
    assert query(
        job_settings,
        "SELECT chunk_set, manual_sha256, chat_deployment, embedding_deployment, "
        "chunk_count FROM retrieval.ingest_run",
    ) == [("smart", hashlib.sha256(PDF).hexdigest(), CHAT, EMBEDDING, 3)]
    assert (
        "ingestion done: pages=4 chunks=3 written=3 moved=0 removed=0 unchanged=0"
        in caplog.text
    )
    # Ids, counts and timings: never text of the manual or of an answer.
    assert "SECRET" not in caplog.text
    assert "Raised blood sugar" not in caplog.text


def test_story_2_2_a_second_run_of_the_job_changes_nothing_and_calls_no_model(
    job_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    azure = Azure()
    assert run_job(job_settings, azure) == 0
    before = chunks(job_settings)
    calls = azure.model_calls

    with caplog.at_level(logging.INFO):
        status = run_job(job_settings, azure)

    assert status == 0
    # Same ids, text and vectors, and not one row written again (`xmin`).
    assert chunks(job_settings) == before
    assert azure.model_calls == calls
    assert "written=0 moved=0 removed=0 unchanged=3 skipped=yes" in caplog.text
    # The manual is the one of the last run: it was not sent to Document
    # Intelligence a second time.
    assert len(azure.submitted) == 1
    assert "manual unchanged since the last run: manual_sha256=" in caplog.text


def test_story_2_2_a_changed_manual_is_brought_in_by_the_next_run(
    job_settings: Settings, tmp_path: Path
) -> None:
    azure = Azure()
    assert run_job(job_settings, azure) == 0
    before = chunks(job_settings)
    calls = (azure.chat_calls, azure.embedding_calls)
    upload(job_settings, tmp_path, CHANGED_PDF)
    azure.parsed = manual(first=definition(RULE_A, "A new band."), with_c=False)

    assert run_job(job_settings, azure) == 0

    after = chunks(job_settings)
    assert sorted(after) == [f"smart-{RULE_A}", f"smart-{RULE_B}"]
    assert after[f"smart-{RULE_A}"][7] == f"Rule {RULE_A}: A new band."
    assert after[f"smart-{RULE_A}"][12] != before[f"smart-{RULE_A}"][12]
    # The rest is untouched, down to the row version.
    assert after[f"smart-{RULE_B}"] == before[f"smart-{RULE_B}"]
    assert (azure.chat_calls, azure.embedding_calls) == (calls[0] + 1, calls[1] + 2)
    assert len(azure.submitted) == 2
    assert query(
        job_settings, "SELECT manual_sha256, chunk_count FROM retrieval.ingest_run"
    ) == [(hashlib.sha256(CHANGED_PDF).hexdigest(), 2)]


@pytest.mark.parametrize(
    ("change", "code", "reason"),
    [
        # Document Intelligence answers an error.
        ({"layout_status": 500}, "upstream_unavailable", "layout_submit_status_500"),
        # The chat or the embedding model fails after its retries.
        ({"model_status": 429}, "model_unavailable", "model_unavailable"),
    ],
)
def test_story_2_2_a_failed_run_ends_non_zero_with_a_code_and_leaves_the_index_whole(
    job_settings: Settings,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    change: dict[str, Any],
    code: str,
    reason: str,
) -> None:
    good = Azure()
    assert run_job(job_settings, good) == 0
    before = chunks(job_settings)
    noted = query(job_settings, "SELECT manual_sha256 FROM retrieval.ingest_run")
    upload(job_settings, tmp_path, CHANGED_PDF)
    # The manual changed, so the failing run has work to do.
    bad = Azure(
        parsed=manual(first=definition(RULE_A, "A new band."), with_c=False), **change
    )
    fast = job_settings.model_copy(update={"model_retry_seconds": 0.001})

    with caplog.at_level(logging.INFO):
        status = run_job(fast, bad)

    assert status == 1
    assert f"ingestion failed: code={code} reason={reason}" in caplog.text
    # The previous index is whole: nothing rewritten, nothing removed, and
    # it still says it was built from the manual before.
    assert chunks(job_settings) == before
    assert query(job_settings, "SELECT manual_sha256 FROM retrieval.ingest_run") == (
        noted
    )
    assert "SECRET" not in caplog.text
