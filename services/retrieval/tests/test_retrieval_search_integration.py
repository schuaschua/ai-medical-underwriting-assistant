"""Stories 2.3 and 3.2, against a real PostgreSQL with pgvector.

Run `docker compose up --detach --wait` first. No test here calls Azure or a
model. The chunks and their vectors are made by hand, so that the order each
side must answer is known: a vector is 1 along a few named dimensions, and
the query's vector is whatever the test says the model answered. The same
search over the project's manual is tested beside the stand-ins (`packages/`
tests, story 2.3).
"""

import asyncio
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from retrieval_fakes import (
    CHAT,
    EMBEDDING,
    RULE_A,
    RULE_B,
    RULE_C,
    MemorySchemaRevision,
    axis,
    chunk_record,
    fixed_record,
)
from sqlalchemy import func, literal, select, text
from sqlalchemy.exc import DBAPIError

from contracts.enums import ChunkSet
from contracts.errors import ErrorBody, ErrorCode
from contracts.models.retrieval import RuleText, SearchResponse
from retrieval.adapters.db import SqlChunkRepository, build_database
from retrieval.adapters.http.app import create_app
from retrieval.adapters.http.routes import Dependencies
from retrieval.adapters.index import (
    SqlChunkIndex,
    read_only_transaction,
)
from retrieval.domain.entities import ChunkRecord, IngestRun
from retrieval.domain.search import (
    NAMED_RULE_ID,
    SearchOptions,
    SearchPorts,
    rule_ids_named_in,
)
from retrieval.settings import Settings

pytestmark = pytest.mark.integration

RULE_D = "UW-BB-002"
RULE_E = "UW-CC-001"
SMART = ChunkSet.SMART
EVERY_ROW = "SELECT chunk_id, xmin::text FROM retrieval.chunk ORDER BY chunk_id"

# Five `smart` chunks, and four `fixed` chunks that no search of `r2` or `r3`
# may find. The words and the vectors are chosen so that the two sides
# disagree. The `fixed` chunks are runs of the same text: the first holds two
# definitions, and the second begins inside its overlap with the first.
CHUNKS = [
    chunk_record(
        RULE_A,
        "Glycated haemoglobin below seven per cent. Probable rating: debit 25.",
        axis(1),
        references=[RULE_B],
    ),
    chunk_record(
        RULE_B,
        "Glycated haemoglobin from seven to eight per cent. Probable rating: "
        f"debit 50. If the kidneys are affected, see rule {RULE_C}.",
        axis(1, 2),
        references=[RULE_C],
    ),
    chunk_record(
        RULE_C,
        "Albumin in the urine. Probable rating: debit 75.",
        axis(2),
        impairment="Kidney disease",
        manual_page=31,
    ),
    chunk_record(
        RULE_D,
        "Serum urate above the range, with tophi. Probable rating: decline.",
        axis(3),
        impairment="Gout",
        manual_page=40,
    ),
    # Its words are found nowhere in a query about sugar; its vector is.
    chunk_record(
        RULE_E,
        "Zzyzx quorvel. Probable rating: postpone.",
        axis(1, 4),
        impairment="Unnamed",
        manual_page=50,
    ),
    fixed_record(
        1,
        f"2.4 Probable rating Rule {RULE_A}: Glycated haemoglobin below seven per "
        f"cent. Probable rating: debit 25. Rule {RULE_B}: Glycated haemoglobin from "
        "seven to eight",
        axis(1),
        [RULE_A, RULE_B],
    ),
    fixed_record(
        2,
        f"Rule {RULE_B}: Glycated haemoglobin from seven to eight per cent. Probable "
        f"rating: debit 50. If the kidneys are affected, see rule {RULE_C}.",
        axis(1, 2),
        [RULE_B],
        references=[RULE_C],
    ),
    fixed_record(
        3,
        "What does not change the rating. Serum urate above the range, with tophi.",
        axis(3),
        impairment="Gout",
        manual_page=40,
    ),
    # As near the query as the second, and after it by its id.
    fixed_record(4, "Worked examples.", axis(1, 2), manual_page=41),
]


def ids(chunks: Sequence[Any]) -> list[str]:
    return [chunk.chunk_id for chunk in chunks]


def smart(*rule_ids: str) -> list[str]:
    return [f"smart-{rule_id}" for rule_id in rule_ids]


def store(settings: Settings, records: Sequence[ChunkRecord]) -> None:
    """Store hand-made chunks as an ingestion would: one run per chunk set."""

    async def scenario() -> None:
        database = build_database(settings)
        try:
            repository = SqlChunkRepository(database)
            for chunk_set in ChunkSet:
                of_set = [r for r in records if r.chunk.chunk_set is chunk_set]
                if not of_set:
                    continue
                await repository.apply(
                    chunk_set,
                    planned_from=await repository.stored(chunk_set),
                    write=of_set,
                    move={},
                    remove=[],
                    run=IngestRun("a" * 64, "digest-1", CHAT, EMBEDDING, len(of_set)),
                )
        finally:
            await database.dispose()

    asyncio.run(scenario())


def rows(settings: Settings) -> list[Any]:
    with psycopg.connect(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=settings.database_user,
    ) as connection:
        return connection.execute(EVERY_ROW).fetchall()


@pytest.fixture
def indexed_database(migrated_database: Settings) -> Settings:
    store(migrated_database, CHUNKS)
    return migrated_database


class Index:
    """The real adapter on a test's database, called from plain test code."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _run(self, call: Any) -> Any:
        async def scenario() -> Any:
            database = build_database(self._settings)
            try:
                return await call(SqlChunkIndex(database))
            finally:
                await database.dispose()

        return asyncio.run(scenario())

    def nearest(
        self, vector: Sequence[float], limit: int = 50, chunk_set: ChunkSet = SMART
    ) -> list[str]:
        async def read(index: SqlChunkIndex) -> Any:
            async with index.snapshot() as view:
                return await view.nearest(chunk_set, vector, limit)

        return ids(self._run(read))

    def matching(
        self,
        query: str,
        named: Sequence[str] = (),
        limit: int = 50,
        chunk_set: ChunkSet = SMART,
    ) -> list[str]:
        async def read(index: SqlChunkIndex) -> Any:
            async with index.snapshot() as view:
                return await view.matching(chunk_set, query, named, limit)

        return ids(self._run(read))

    def column(self, statement: Any) -> list[Any]:
        """The values a statement answers, read in the adapter's own read-only transaction."""

        async def scenario() -> list[Any]:
            database = build_database(self._settings)
            try:
                async with read_only_transaction(database) as connection:
                    return list((await connection.execute(statement)).scalars())
            finally:
                await database.dispose()

        return asyncio.run(scenario())

    def defining(self, rule_id: str, chunk_set: ChunkSet = SMART) -> Any:
        return self._run(lambda index: index.defining(chunk_set, rule_id))


@pytest.fixture
def index(indexed_database: Settings) -> Index:
    return Index(indexed_database)


# --- The full-text side ---------------------------------------------------------------------


def test_story_2_3_a_rule_id_in_a_query_is_found_whole_and_its_own_chunk_first(
    index: Index,
) -> None:
    # C's id is in C, which defines it, and in B, which refers to it; B is
    # the better match by words alone when the query also asks for sugar.
    query = f"glycated haemoglobin, and what does {RULE_C} say"

    assert index.matching(query, named=[RULE_C])[:2] == smart(RULE_C, RULE_B)
    # The id alone finds the two chunks that print it, and no chunk that
    # merely shares `UW`, `BB` or `001` with it.
    assert index.matching(RULE_C, named=[RULE_C]) == smart(RULE_C, RULE_B)
    # Written in small letters it is the same id.
    assert index.matching(RULE_C.lower(), named=[RULE_C]) == smart(RULE_C, RULE_B)
    assert index.matching("UW") == []
    assert index.matching("001") == []
    # An id of the right form that the manual does not print finds nothing.
    assert index.matching("UW-QQ-999", named=["UW-QQ-999"]) == []


def test_story_2_3_nothing_in_a_query_is_read_as_an_operator_or_as_sql(
    index: Index, indexed_database: Settings
) -> None:
    before = rows(indexed_database)

    for query in ("urate & | ! ( ) <-> :*", "urate'); DROP TABLE retrieval.chunk; --"):
        assert index.matching(query) == smart(RULE_D)
    assert rows(indexed_database) == before


BOUNDARY_CASES = [
    "what does UW-DM-001, UW-DM-001. (UW-HT-002) say",
    "UW-DM-0011",
]


def test_story_2_3_python_and_sql_agree_on_what_a_rule_id_in_a_query_is(
    index: Index,
) -> None:
    for query in BOUNDARY_CASES:
        # The pattern the full-text search rewrites a query with, run by PostgreSQL.
        by_sql = index.column(
            select(
                func.array_to_string(
                    func.regexp_matches(literal(query), NAMED_RULE_ID, "g"), "-"
                )
            )
        )
        in_sql = tuple(dict.fromkeys(f"UW-{found.upper()}" for found in by_sql))

        assert in_sql == rule_ids_named_in(query)


# --- The search, whole ------------------------------------------------------------------------


class Model:
    """Stands where the gateway would be: answers the vector a test gives it."""

    def __init__(self, vector: Sequence[float]) -> None:
        self.vector = list(vector)
        self.embedded: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.embedded.append(list(texts))
        return [self.vector for _ in texts]


@pytest.fixture
def model() -> Model:
    return Model(axis(1))


@contextmanager
def service_on(
    settings: Settings, model: Model, **options: Any
) -> Iterator[TestClient]:
    """The service's app on the real index of a test's database, with the model stub.

    The engine is closed when the test is done with it, however it ends.
    """
    database = build_database(settings)
    values: dict[str, Any] = {
        "candidate_depth": settings.search_candidate_depth,
        "deadline_seconds": settings.search_deadline_seconds,
        "embedding_deployment": settings.embedding_deployment,
        **options,
    }
    dependencies = Dependencies(
        search=SearchPorts(model=model, index=SqlChunkIndex(database)),
        schema_revision=MemorySchemaRevision("0001"),
        head_revision="0001",
        options=SearchOptions(**values),
    )
    app = create_app(settings, dependencies=dependencies)
    with TestClient(app, raise_server_exceptions=False) as client:
        try:
            yield client
        finally:
            # On the client's own event loop, which opened the connections.
            assert client.portal is not None
            client.portal.call(database.dispose)


@pytest.fixture
def service(indexed_database: Settings, model: Model) -> Iterator[TestClient]:
    with service_on(indexed_database, model) as client:
        yield client


def search(client: TestClient, query: str, **body: Any) -> SearchResponse:
    response = client.post(
        "/searches", json={"query": query, "retriever_config": "r3", **body}
    )
    assert response.status_code == 200, response.text
    return SearchResponse.model_validate(response.json())


def test_story_2_3_the_two_sides_are_fused_by_rank_into_one_order(
    service: TestClient, model: Model
) -> None:
    # The vector side: A, B, E, C, D. The full-text side: B, then A (see above).
    result = search(service, "glycated haemoglobin was eight per cent")

    assert [item.chunk_id for item in result.items] == smart(
        RULE_A, RULE_B, RULE_E, RULE_C, RULE_D
    )
    assert [item.score for item in result.items] == pytest.approx(
        # A and B are first on one side and second on the other: equal
        # scores, in the order of their ids. The rest is the vector side's.
        [1 / 61 + 1 / 62, 1 / 62 + 1 / 61, 1 / 63, 1 / 64, 1 / 65]
    )
    assert [item.rank for item in result.items] == [1, 2, 3, 4, 5]
    assert all(0 < item.score <= 1 for item in result.items)
    assert result.retriever_config.value == "r3"
    first = result.items[0]
    assert (first.rule_ids, first.manual_page, first.impairment) == (
        [RULE_A],
        12,
        "Raised blood sugar",
    )
    assert first.text.startswith(f"Rule {RULE_A}: Glycated haemoglobin below seven")
    # No `fixed` chunk in an answer of `r3`.
    assert not [item for item in result.items if item.chunk_id.startswith("fixed-")]
    assert model.embedded == [["glycated haemoglobin was eight per cent"]]


def test_story_2_3_a_chunk_only_the_full_text_side_finds_is_returned(
    migrated_database: Settings,
) -> None:
    # Five chunks. With `top_k` 2 each side hands the fusion four (twice
    # `top_k`, the setting being lower): the chunk whose words match is the
    # farthest by its vector, and outside the vector side's four.
    store(
        migrated_database,
        [
            chunk_record(RULE_A, "Near one.", axis(1)),
            chunk_record(RULE_B, "Near two.", axis(1, 2)),
            chunk_record("UW-ZZ-001", "Near three.", axis(1, 2, 5)),
            chunk_record("UW-ZZ-002", "Near four.", axis(1, 2, 5, 6)),
            chunk_record(RULE_C, "Serum urate above the range.", axis(3)),
        ],
    )
    with service_on(migrated_database, Model(axis(1)), candidate_depth=2) as client:
        result = search(client, "urate", top_k=2)

    # C is first on the full-text side and absent from the vector side's
    # four; A is first on the vector side. Equal scores, A before C by id.
    assert [item.chunk_id for item in result.items] == smart(RULE_A, RULE_C)
    assert [item.score for item in result.items] == pytest.approx([1 / 61, 1 / 61])


def answered(response: Any) -> tuple[int, ErrorCode]:
    return response.status_code, ErrorBody.model_validate(response.json()).error.code


@contextmanager
def table_locked(settings: Settings) -> Iterator[None]:
    """Hold the chunk table so that no statement can read it, as a slow statement would be."""
    with psycopg.connect(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=settings.database_user,
    ) as connection:
        connection.execute("LOCK TABLE retrieval.chunk IN ACCESS EXCLUSIVE MODE")
        try:
            yield
        finally:
            connection.rollback()


def test_story_2_3_a_search_the_database_holds_up_ends_at_its_own_deadline(
    indexed_database: Settings, model: Model
) -> None:
    with (
        service_on(indexed_database, model, deadline_seconds=0.3) as client,
        table_locked(indexed_database),
    ):
        held_up = client.post(
            "/searches", json={"query": "urate", "retriever_config": "r3"}
        )
    with service_on(indexed_database, model) as client:
        afterwards = search(client, "urate")

    # Long before the statement's own limit of 30 seconds.
    assert answered(held_up) == (502, ErrorCode.UPSTREAM_UNAVAILABLE)
    assert len(afterwards.items) == 5


def test_story_2_3_both_reads_of_a_search_see_one_index_and_cannot_write(
    indexed_database: Settings,
) -> None:
    async def scenario() -> tuple[list[str], list[str], list[str], str]:
        database = build_database(indexed_database)
        try:
            async with SqlChunkIndex(database).snapshot() as view:
                connection = view._connection
                settings_seen = [
                    (await connection.exec_driver_sql(f"SHOW {name}")).scalar_one()
                    for name in ("transaction_isolation", "transaction_read_only")
                ]
                first = ids(await view.nearest(SMART, axis(1), 50))
                # An ingestion that ends between the two reads: another
                # connection removes a chunk and commits.
                await asyncio.to_thread(remove_chunk, indexed_database, RULE_A)
                second = ids(await view.matching(SMART, "glycated haemoglobin", (), 50))
            async with SqlChunkIndex(database).snapshot() as view:
                later = ids(await view.matching(SMART, "glycated haemoglobin", (), 50))
            try:
                async with read_only_transaction(database) as connection:
                    await connection.execute(text("DELETE FROM retrieval.chunk"))
                refused = "not refused"
            except DBAPIError as error:
                refused = type(error.orig).__qualname__
            return settings_seen, second, later, refused if first else ""
        finally:
            await database.dispose()

    settings_seen, second, later, refused = asyncio.run(scenario())

    assert settings_seen == ["repeatable read", "on"]
    # The second read still sees the chunk the first read saw; a later
    # search sees the index as it is now.
    assert second == smart(RULE_A, RULE_B)
    assert later == smart(RULE_B)
    # The server itself refuses a write there, and nothing was removed by it.
    assert refused == "ReadOnlySqlTransaction"
    assert len(rows(indexed_database)) == len(CHUNKS) - 1


# --- The baseline rows (story 3.2) ----------------------------------------------------------


def test_story_3_2_rows_r1_and_r2_search_by_vector_alone_each_over_its_own_chunk_set(
    service: TestClient, model: Model
) -> None:
    # The only chunks that hold the query's one word are the farthest from
    # the query's vector, which is A's.
    r1 = search(service, "urate", retriever_config="r1", top_k=3)
    r2 = search(service, "urate", retriever_config="r2")
    r3 = search(service, "urate", retriever_config="r3")
    defined_twice = service.get(f"/rules/{RULE_B}", params={"retriever_config": "r1"})
    defined_once = service.get(f"/rules/{RULE_A}", params={"retriever_config": "r1"})
    by_smart_row = service.get(f"/rules/{RULE_B}", params={"retriever_config": "r2"})
    undefined = service.get(f"/rules/{RULE_D}", params={"retriever_config": "r1"})
    not_built = [
        service.post("/searches", json={"query": "urate", "retriever_config": row})
        for row in ("r4", "r5", "r6")
    ]

    # `r1`: the `fixed` chunks by cosine similarity, nearest first, and the
    # two that are equally near in the order of their ids.
    assert [item.chunk_id for item in r1.items] == [
        "fixed-0001",
        "fixed-0002",
        "fixed-0004",
    ]
    assert [item.rank for item in r1.items] == [1, 2, 3]
    # The score is the similarity on 0 to 1: 1 for the same direction, and
    # (1 + cos 45 degrees) / 2 for the two that share one of two dimensions.
    assert [item.score for item in r1.items] == pytest.approx(
        [1.0, (1 + 0.5**0.5) / 2, (1 + 0.5**0.5) / 2], abs=1e-6
    )
    # The common shape: a chunk names the rules it defines, none or several.
    assert [item.rule_ids for item in r1.items] == [[RULE_A, RULE_B], [RULE_B], []]
    assert (r1.items[2].manual_page, r1.items[0].impairment) == (
        41,
        "Raised blood sugar",
    )
    # `r2`: the same over the `smart` chunks. The chunk whose words match is
    # last, at a right angle to the query; `r3` puts it first for its words.
    assert [item.chunk_id for item in r2.items] == smart(
        RULE_A, RULE_B, RULE_E, RULE_C, RULE_D
    )
    assert [item.score for item in r2.items] == pytest.approx(
        [1.0, (1 + 0.5**0.5) / 2, (1 + 0.5**0.5) / 2, 0.5, 0.5], abs=1e-6
    )
    assert r3.items[0].chunk_id == f"smart-{RULE_D}"
    assert (r1.retriever_config.value, r2.retriever_config.value) == ("r1", "r2")
    for result in (r1, r2, r3):
        assert all(0 <= item.score <= 1 for item in result.items)
    # The query was embedded once per search, the same way for every row.
    assert model.embedded == [["urate"]] * 3

    # A rule read on `r1` answers the `fixed` chunk that holds the rule's
    # marker; of two that hold it, the later one, where the definition goes on.
    twice = RuleText.model_validate(defined_twice.json())
    assert (twice.chunk_id, twice.chunk_set) == ("fixed-0002", ChunkSet.FIXED)
    assert twice.reference_rule_ids == [RULE_C]
    once = RuleText.model_validate(defined_once.json())
    assert (once.rule_id, once.chunk_id) == (RULE_A, "fixed-0001")
    assert f"Rule {RULE_B}:" in once.text
    # A row on the `smart` set answers the `smart` chunk, as before.
    assert by_smart_row.json()["chunk_id"] == f"smart-{RULE_B}"
    # A rule no `fixed` chunk defines is not found; the rows not built yet
    # are refused as not available.
    assert answered(undefined) == (404, ErrorCode.NOT_FOUND)
    assert [answered(response) for response in not_built] == [
        (409, ErrorCode.RETRIEVER_NOT_AVAILABLE)
    ] * 3


def remove_chunk(settings: Settings, rule_id: str) -> None:
    with psycopg.connect(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=settings.database_user,
        autocommit=True,
    ) as connection:
        connection.execute(
            "DELETE FROM retrieval.chunk WHERE chunk_id = %s", (f"smart-{rule_id}",)
        )


# --- The rule read ----------------------------------------------------------------------------


def test_story_2_3_a_rule_is_read_from_the_chunk_that_defines_it(
    service: TestClient, index: Index
) -> None:
    response = service.get(f"/rules/{RULE_B}")

    assert response.status_code == 200
    rule = RuleText.model_validate(response.json())
    assert (rule.rule_id, rule.chunk_id, rule.chunk_set) == (
        RULE_B,
        f"smart-{RULE_B}",
        ChunkSet.SMART,
    )
    assert rule.text.startswith(f"Rule {RULE_B}: Glycated haemoglobin from seven")
    assert (rule.manual_page, rule.impairment) == (12, "Raised blood sugar")
    assert rule.reference_rule_ids == [RULE_C]
    # C is defined by its own chunk, never by B, which only refers to it.
    assert index.defining(RULE_C).chunk_id == f"smart-{RULE_C}"
    # The `fixed` chunk of the same rule is another chunk set's.
    assert service.get(f"/rules/{RULE_A}").json()["chunk_id"] == f"smart-{RULE_A}"
    assert index.defining(RULE_A, ChunkSet.FIXED).chunk_id == "fixed-0001"
    assert index.defining("UW-QQ-999") is None


def test_story_2_3_the_service_built_from_its_settings_searches_the_real_index(
    indexed_database: Settings,
) -> None:
    # The app as the server builds it, with no model configured: a rule is
    # read, and a search says the model is not available.
    settings = indexed_database.model_copy(update={"model_endpoint": None})
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        read = client.get(f"/rules/{RULE_D}")
        searched = client.post(
            "/searches", json={"query": "urate", "retriever_config": "r3"}
        )

    assert read.status_code == 200
    assert read.json()["impairment"] == "Gout"
    assert searched.status_code == 503
