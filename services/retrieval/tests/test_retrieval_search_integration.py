"""Story 2.3, against a real PostgreSQL with pgvector.

Run `docker compose up --detach --wait` first. No test here calls Azure or a
model. The chunks and their vectors are made by hand, so that the order each
side must answer is known: a vector is 1 along a few named dimensions, and
the query's vector is whatever the test says the model answered. The same
search over the project's manual is tested beside the stand-ins (`packages/`
tests, story 2.3).
"""

import asyncio
import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from retrieval_fakes import (
    CHAT,
    EMBEDDING,
    RULE_A,
    RULE_B,
    RULE_C,
    MemorySchemaRevision,
    axis,
    chunk_record,
)
from sqlalchemy import Text, cast, func, literal, select, text
from sqlalchemy.exc import DBAPIError

from contracts.enums import ChunkSet
from contracts.errors import ErrorBody, ErrorCode
from contracts.models.retrieval import RuleText, SearchResponse
from retrieval.adapters import index as index_adapter
from retrieval.adapters.db import SqlChunkRepository, build_database
from retrieval.adapters.http.app import create_app
from retrieval.adapters.http.routes import Dependencies
from retrieval.adapters.index import (
    SqlChunkIndex,
    any_word_of,
    defining_statement,
    matching_statement,
    nearest_statement,
    read_only_transaction,
)
from retrieval.domain.entities import ChunkRecord, IngestRun
from retrieval.domain.ports import IndexUnavailable
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

# Five `smart` chunks and one `fixed` chunk that no search of `r3` may find.
# The words and the vectors are chosen so that the two sides disagree.
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
    chunk_record(
        RULE_A,
        "Glycated haemoglobin below seven per cent, cut by size.",
        axis(1),
        chunk_set=ChunkSet.FIXED,
    ),
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


# --- The vector side ------------------------------------------------------------------------


def test_story_2_3_the_vector_side_is_exact_cosine_nearest_neighbour_over_the_smart_chunks(
    index: Index,
) -> None:
    # Cosine with (1, 0, ...): A is 1, B and E are 1/sqrt(2), C and D are 0.
    # Equally near chunks come in the order of their ids.
    assert index.nearest(axis(1)) == smart(RULE_A, RULE_B, RULE_E, RULE_C, RULE_D)
    assert index.nearest(axis(2)) == smart(RULE_C, RULE_B, RULE_A, RULE_D, RULE_E)
    # Cosine, not distance: a longer vector of the same direction changes nothing.
    assert index.nearest([value * 7.5 for value in axis(1)]) == index.nearest(axis(1))
    assert index.nearest(axis(1), limit=2) == smart(RULE_A, RULE_B)
    # Only the chunk set asked for.
    assert index.nearest(axis(1), chunk_set=ChunkSet.FIXED) == [f"fixed-{RULE_A}"]


def test_story_2_3_the_vector_side_uses_no_approximate_index(
    indexed_database: Settings,
) -> None:
    with psycopg.connect(
        host=indexed_database.database_host,
        port=indexed_database.database_port,
        dbname=indexed_database.database_name,
        user=indexed_database.database_user,
    ) as connection:
        methods = connection.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'retrieval' "
            "AND tablename = 'chunk'"
        ).fetchall()

    # AD-12: exact search. Nothing of `hnsw` or `ivfflat` to read the vectors through.
    assert not [row for row in methods if "hnsw" in row[0] or "ivfflat" in row[0]]


# --- The full-text side ---------------------------------------------------------------------


def test_story_2_3_the_full_text_side_matches_a_chunk_that_holds_some_of_the_words(
    index: Index,
) -> None:
    # A sentence, not a keyword list: no chunk holds all of its words.
    found = index.matching(
        "The applicant's glycated haemoglobin was eight per cent last spring"
    )

    # B holds `eight` as well as the words A holds; the others hold none.
    assert found == smart(RULE_B, RULE_A)
    # Words are compared as the configuration stems them.
    assert index.matching("kidney") == smart(RULE_B)
    assert index.matching("urates") == smart(RULE_D)
    assert index.matching("glycated", limit=1, chunk_set=ChunkSet.FIXED) == [
        f"fixed-{RULE_A}"
    ]


def test_story_2_3_the_full_text_side_breaks_ties_by_chunk_id_and_keeps_to_its_limit(
    index: Index,
) -> None:
    # Every `smart` chunk holds these two words once. The rank is divided by
    # the chunk's length, so the shortest is first, and none is left out.
    every = index.matching("probable rating")

    assert sorted(every) == smart(RULE_A, RULE_B, RULE_C, RULE_D, RULE_E)
    assert index.matching("probable rating") == every
    assert index.matching("probable rating", limit=2) == every[:2]


def test_story_2_3_of_two_chunks_with_the_same_query_words_the_shorter_is_ranked_first(
    migrated_database: Settings,
) -> None:
    words = "Serum urate above the range."
    padding = " Nothing more is said of it here, in so many further words." * 4
    store(
        migrated_database,
        [
            # By its id the long chunk would come first: only the rank puts it second.
            chunk_record("UW-ZZ-001", words + padding, axis(1)),
            chunk_record("UW-ZZ-002", words, axis(1)),
        ],
    )

    # Each holds `serum`, `urate` and `range` once. The rank is divided by
    # 1 + the logarithm of the chunk's length, so length alone decides.
    assert Index(migrated_database).matching("serum urate range") == smart(
        "UW-ZZ-002", "UW-ZZ-001"
    )


def test_story_2_3_chunks_that_match_equally_well_come_in_the_order_of_their_ids(
    migrated_database: Settings,
) -> None:
    same = "Serum urate above the range."
    store(
        migrated_database,
        [
            chunk_record(rule, same, axis(1))
            for rule in ("UW-ZZ-003", "UW-ZZ-001", "UW-ZZ-002")
        ],
    )
    index = Index(migrated_database)

    assert index.matching("urate") == smart("UW-ZZ-001", "UW-ZZ-002", "UW-ZZ-003")
    assert index.nearest(axis(1)) == smart("UW-ZZ-001", "UW-ZZ-002", "UW-ZZ-003")


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


def test_story_2_3_a_query_of_stop_words_matches_nothing_on_the_full_text_side(
    index: Index,
) -> None:
    for query in ("the and of it", "a", "?!", "   .   "):
        assert index.matching(query) == []


@pytest.mark.parametrize(
    "query",
    [
        "urate & | ! ( ) <-> :*",
        "urate' OR '1'='1",
        "urate'); DROP TABLE retrieval.chunk; --",
        "urate \\ %s $1 :query",
    ],
)
def test_story_2_3_nothing_in_a_query_is_read_as_an_operator_or_as_sql(
    index: Index, indexed_database: Settings, query: str
) -> None:
    before = rows(indexed_database)

    assert index.matching(query) == smart(RULE_D)
    assert rows(indexed_database) == before


def test_story_2_3_only_the_operator_between_two_words_is_made_or(index: Index) -> None:
    def printed(query: str) -> str:
        (text,) = index.column(select(cast(any_word_of(literal(query)), Text)))
        return str(text)

    # An address is one word to the parser, and its `&` is part of the word.
    assert printed("x.com/a?b&c and urate") == (
        "'x.com/a?b&c' | 'x.com' | '/a?b&c' | 'urat'"
    )
    assert printed("urate") == "'urat'"
    # And such a query still finds what its other words say.
    assert index.matching("x.com/a?b&c and urate") == smart(RULE_D)


BOUNDARY_CASES = [
    "UW-DM-001",
    "uw-dm-001 and Uw-Ht-002",
    "what does UW-DM-001, UW-DM-001. (UW-HT-002) say",
    "UW-DM-001-UW-HT-002",
    "XUW-DM-001",
    "UW-DM-0011",
    "UW-DM-001x",
    "_UW-DM-001",
    "UW-DM-001_",
    "9UW-DM-001",
    "UW-D-001 UW-ABCDE-001 UW-DM-01",
    "UW_DM_001 UWDM001",
    "\u00e9UW-DM-001 UW-DM-001\u00e9",
    "UW-D\u017fM-001 \u212aUW-DM-001",
    "rule:UW-ABCD-123;uw-ab-000\tUW-AB-999\n",
    "HbA1c from 7.0 to below 8.0 %",
]


@pytest.mark.parametrize("query", BOUNDARY_CASES)
def test_story_2_3_python_and_sql_agree_on_what_a_rule_id_in_a_query_is(
    index: Index, query: str
) -> None:
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


def test_story_2_3_the_statements_carry_every_value_as_a_bound_parameter() -> None:
    secret = "SECRET-QUERY'; DROP TABLE chunk; --"  # noqa: S105 - a made-up query, not a credential
    statements = [
        nearest_statement(SMART, axis(1), 50),
        matching_statement(SMART, secret, ["UW-AA-001"], 50),
        defining_statement(SMART, "UW-AA-001"),
    ]

    for statement in statements:
        compiled = statement.compile()
        # security rule 21: the text of the statement holds no value.
        assert "SECRET" not in str(compiled)
        assert "UW-AA-001" not in str(compiled)
        assert "smart" not in str(compiled).replace("smart-", "")
    assert secret in statements[1].compile().params.values()
    # The same text-search configuration as the stored column (story 2.2).
    assert "'english'::regconfig" in str(statements[1].compile())


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


def test_story_2_3_a_chunk_only_the_vector_side_finds_is_returned(
    service: TestClient, model: Model
) -> None:
    # E's words are in no query; its vector is next to the query's.
    model.vector = list(axis(4))

    result = search(service, "serum urate above the range", top_k=2)

    # D by its words alone, E by its vector alone and first there.
    by_id = {item.chunk_id: item for item in result.items}
    assert set(by_id) == set(smart(RULE_D, RULE_E))
    assert by_id[f"smart-{RULE_E}"].score == pytest.approx(1 / 61)


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


def test_story_2_3_a_rule_id_as_the_query_ranks_that_rules_chunk_first(
    service: TestClient, model: Model
) -> None:
    # The vector of such a query says little: here it points at D, and C is
    # fourth on that side (A, B and C are equally far, in the order of their ids).
    model.vector = list(axis(3))

    result = search(service, RULE_C)

    # The full-text side: C, which defines the rule, then B, which refers to it.
    assert [item.chunk_id for item in result.items[:3]] == smart(RULE_C, RULE_B, RULE_D)
    assert [item.score for item in result.items[:3]] == pytest.approx(
        [1 / 61 + 1 / 64, 1 / 62 + 1 / 63, 1 / 61]
    )
    assert result.items[0].rule_ids == [RULE_C]


def test_story_2_3_the_same_query_on_the_same_index_gives_the_same_answer(
    service: TestClient, indexed_database: Settings
) -> None:
    before = rows(indexed_database)

    answers = [search(service, "glycated haemoglobin per cent rating") for _ in "123"]

    assert answers[0].items == answers[1].items == answers[2].items
    # A search only reads: not one row was written.
    assert rows(indexed_database) == before


@pytest.mark.parametrize(("top_k", "expected"), [(1, 1), (5, 5), (50, 5)])
def test_story_2_3_top_k_caps_what_the_index_answers(
    service: TestClient, top_k: int, expected: int
) -> None:
    result = search(service, "probable rating", top_k=top_k)

    assert len(result.items) == expected
    assert [item.rank for item in result.items] == list(range(1, expected + 1))


def test_story_2_3_a_query_of_stop_words_is_answered_by_the_vector_side_alone(
    service: TestClient,
) -> None:
    result = search(service, "the and of it")

    assert [item.chunk_id for item in result.items] == smart(
        RULE_A, RULE_B, RULE_E, RULE_C, RULE_D
    )
    assert [item.score for item in result.items] == pytest.approx(
        [1 / 61, 1 / 62, 1 / 63, 1 / 64, 1 / 65]
    )


def test_story_2_3_an_index_without_chunks_is_no_error(
    migrated_database: Settings, model: Model
) -> None:
    with service_on(migrated_database, model) as client:
        searched = client.post(
            "/searches", json={"query": "anything", "retriever_config": "r3"}
        )
        read = client.get(f"/rules/{RULE_A}")

    assert searched.status_code == 200
    assert SearchResponse.model_validate(searched.json()).items == []
    assert read.status_code == 404
    assert ErrorBody.model_validate(read.json()).error.code is ErrorCode.NOT_FOUND


def answered(response: Any) -> tuple[int, ErrorCode]:
    return response.status_code, ErrorBody.model_validate(response.json()).error.code


def test_story_2_3_a_database_that_cannot_be_reached_is_upstream_unavailable_without_detail(
    indexed_database: Settings, model: Model, caplog: pytest.LogCaptureFixture
) -> None:
    unreachable = indexed_database.model_copy(
        update={"database_port": 1, "database_connect_timeout_seconds": 1}
    )

    with caplog.at_level(logging.INFO), service_on(unreachable, model) as client:
        searched = client.post(
            "/searches", json={"query": "SECRET-QUERY", "retriever_config": "r3"}
        )
        read = client.get(f"/rules/{RULE_A}")

    for response in (searched, read):
        assert answered(response) == (502, ErrorCode.UPSTREAM_UNAVAILABLE)
        assert "127.0.0.1" not in response.text and "SECRET" not in response.text
    # The error's type only (security rule 31).
    assert "index read failed: type=OperationalError" in caplog.text
    assert "SECRET" not in caplog.text and "127.0.0.1" not in caplog.text


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


def test_story_2_3_a_statement_that_runs_too_long_is_ended_by_the_server(
    indexed_database: Settings, model: Model, caplog: pytest.LogCaptureFixture
) -> None:
    short = indexed_database.model_copy(
        update={"database_statement_timeout_seconds": 1}
    )

    # The statement itself is ended by the server after its limit.
    async def slow_statement() -> None:
        database = build_database(short)
        try:
            async with SqlChunkIndex(database).snapshot() as view:
                await view._connection.exec_driver_sql("SELECT pg_sleep(30)")
        finally:
            await database.dispose()

    with pytest.raises(IndexUnavailable) as ended:
        asyncio.run(slow_statement())
    assert ended.value.reason == "QueryCanceled"

    # And a search or a rule read that meets such a statement says so, plainly.
    with (
        caplog.at_level(logging.INFO),
        service_on(short, model, deadline_seconds=20.0) as client,
        table_locked(short),
    ):
        searched = client.post(
            "/searches", json={"query": "SECRET urate", "retriever_config": "r3"}
        )
        read = client.get(f"/rules/{RULE_A}")

    for response in (searched, read):
        assert answered(response) == (502, ErrorCode.UPSTREAM_UNAVAILABLE)
        assert "items" not in response.json()
    assert "index read failed: type=QueryCanceled" in caplog.text
    assert "SECRET" not in caplog.text and "pg_sleep" not in caplog.text


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


def test_story_2_3_a_search_is_refused_when_the_last_ingest_run_used_another_deployment(
    indexed_database: Settings, model: Model, caplog: pytest.LogCaptureFixture
) -> None:
    other = indexed_database.model_copy(
        update={"embedding_deployment": "another-embedding"}
    )

    with caplog.at_level(logging.INFO), service_on(other, model) as client:
        refused = client.post(
            "/searches", json={"query": "urate", "retriever_config": "r3"}
        )
        read = client.get(f"/rules/{RULE_D}")
        # A new ingest run with the service's deployment: the very next
        # search reads the changed record and is answered.
        with psycopg.connect(
            host=other.database_host,
            port=other.database_port,
            dbname=other.database_name,
            user=other.database_user,
            autocommit=True,
        ) as connection:
            connection.execute(
                "UPDATE retrieval.ingest_run SET embedding_deployment = %s "
                "WHERE chunk_set = 'smart'",
                ("another-embedding",),
            )
        again = search(client, "urate")

    assert answered(refused) == (503, ErrorCode.MODEL_UNAVAILABLE)
    assert "another embedding model" in refused.json()["error"]["message"]
    assert (
        "search refused: embedding deployment differs: "
        f"configured=another-embedding index={EMBEDDING}"
    ) in caplog.text
    assert read.status_code == 200
    assert again.items[0].rule_ids == [RULE_D]


def test_story_2_3_each_database_read_has_a_span_that_holds_no_text(
    service: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(index_adapter, "tracer", provider.get_tracer("test"))

    search(service, "SECRET-QUERY glycated haemoglobin")
    service.get(f"/rules/{RULE_A}")

    spans = {
        span.name: dict(span.attributes or {}) for span in exporter.get_finished_spans()
    }
    assert spans == {
        "retrieval.db.read_ingest_run": {},
        "retrieval.db.vector_search": {"retrieval.chunks.count": 5},
        "retrieval.db.full_text_search": {"retrieval.chunks.count": 2},
        "retrieval.db.read_rule": {"retrieval.chunks.count": 1},
    }


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
    assert index.defining(RULE_A, ChunkSet.FIXED).chunk_id == f"fixed-{RULE_A}"
    assert index.defining("UW-QQ-999") is None


def test_story_2_3_rule_reads_by_row_and_their_refusals(service: TestClient) -> None:
    plain = service.get(f"/rules/{RULE_A}").json()

    def read(rule_id: str, **params: str) -> tuple[int, Any]:
        response = service.get(f"/rules/{rule_id}", params=params)
        body = response.json()
        return response.status_code, body.get("error", {}).get("code", body)

    assert read(RULE_A, retriever_config="r3") == (200, plain)
    assert read(RULE_A, retriever_config="r5") == (200, plain)
    # The `fixed` set is stored here by hand, and still not served: no
    # ingestion writes it yet.
    assert read(RULE_A, retriever_config="r1") == (409, "retriever_not_available")
    assert read(RULE_A, retriever_config="r9") == (422, "validation_failed")
    assert read("UW-QQ-999") == (404, "not_found")
    assert read("uw-aa-001") == (422, "validation_failed")


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
