"""The retrieval ladder: what each `retriever_config` is, and which rows this build answers (spine AD-11).

One table for every row. A row that is not built is still named here, so that
asking for it is refused as "not available" and not as unknown. Building a row
is a change to its line and one more branch in the search; nothing else
knows the rows.

A row on Azure AI Search (`r5`) is built and still not available everywhere:
it needs a search service, and a service that was told of none refuses it
the same way, while the pgvector rows answer as before.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from contracts.enums import ChunkSet, RetrieverConfig
from contracts.errors import DomainError, ErrorCode


class SearchMethod(StrEnum):
    """How a row finds its chunks."""

    VECTOR = "vector"
    # Vector and full-text search, fused with reciprocal rank fusion.
    HYBRID = "hybrid"
    HYBRID_RERANKED = "hybrid_reranked"
    AI_SEARCH_HYBRID = "ai_search_hybrid"
    AI_SEARCH_AGENTIC = "ai_search_agentic"


# The methods that ask Azure AI Search, not pgvector.
_ON_THE_SEARCH_SERVICE = frozenset(
    {SearchMethod.AI_SEARCH_HYBRID, SearchMethod.AI_SEARCH_AGENTIC}
)


@dataclass(frozen=True, slots=True)
class RetrieverRow:
    config: RetrieverConfig
    chunk_set: ChunkSet
    method: SearchMethod
    # Whether this build can search with the row.
    built: bool = False

    @property
    def needs_search_service(self) -> bool:
        """Whether the row's store is Azure AI Search: it then needs a search endpoint."""
        return self.method in _ON_THE_SEARCH_SERVICE


_ROWS = (
    RetrieverRow(RetrieverConfig.R1, ChunkSet.FIXED, SearchMethod.VECTOR, built=True),
    RetrieverRow(RetrieverConfig.R2, ChunkSet.SMART, SearchMethod.VECTOR, built=True),
    RetrieverRow(RetrieverConfig.R3, ChunkSet.SMART, SearchMethod.HYBRID, built=True),
    RetrieverRow(RetrieverConfig.R4, ChunkSet.SMART, SearchMethod.HYBRID_RERANKED),
    RetrieverRow(
        RetrieverConfig.R5, ChunkSet.SMART, SearchMethod.AI_SEARCH_HYBRID, built=True
    ),
    RetrieverRow(RetrieverConfig.R6, ChunkSet.SMART, SearchMethod.AI_SEARCH_AGENTIC),
)
ROWS: Mapping[RetrieverConfig, RetrieverRow] = MappingProxyType(
    {row.config: row for row in _ROWS}
)
# AD-11: the rows this build can search with, a search service given.
BUILT_ROWS: frozenset[RetrieverConfig] = frozenset(
    row.config for row in _ROWS if row.built
)


def available_rows(search_service: bool) -> frozenset[RetrieverConfig]:
    """The rows a service answers: the built ones, less those on a search service it was not given.

    `workflow` and `verdict` each name the rows a case may run with, as a
    setting; a test outside `services/` holds the three lists equal, with
    and without a search service.
    """
    return frozenset(
        row.config
        for row in _ROWS
        if row.built and (search_service or not row.needs_search_service)
    )


def rows_in_words(configs: frozenset[RetrieverConfig]) -> str:
    """The rows as a message names them: `Rows r1, r2 and r3`, or `Only row r3`."""
    *others, last = sorted(config.value for config in configs)
    return f"Rows {', '.join(others)} and {last}" if others else f"Only row {last}"


def row_not_available_message(available: frozenset[RetrieverConfig]) -> str:
    """What a caller is told who asked for a row this service does not answer."""
    return (
        "That retrieval row is not available here. "
        f"{rows_in_words(available)} can be used for now."
    )


CHUNK_SET_NOT_INGESTED_MESSAGE = (
    "That retrieval row is not available: the manual has not been ingested for it yet."
)


def row_to_search(
    config: RetrieverConfig, search_service: bool = False
) -> RetrieverRow:
    """The row a search runs with; `retriever_not_available` when this service does not answer it.

    That is a row that is not built yet, and a row on Azure AI Search when
    the service has no search endpoint (`search_service` false).
    """
    row = ROWS[config]
    available = available_rows(search_service)
    if row.config not in available:
        raise DomainError(
            ErrorCode.RETRIEVER_NOT_AVAILABLE, row_not_available_message(available)
        )
    return row


def chunk_set_to_read(config: RetrieverConfig | None) -> ChunkSet:
    """The chunk set a rule is read from: the row's, or `smart` when no row is named.

    A rule read needs the row's chunks, not its search: every row answers,
    built or not, and the ingestion writes both chunk sets. For `r5` that
    is the `smart` chunk the chunk table holds, which the search service's
    index holds a copy of.
    """
    return ChunkSet.SMART if config is None else ROWS[config].chunk_set
