"""The retrieval ladder: what each `retriever_config` is, and which rows this build answers (spine AD-11).

One table for every row. A row that is not built is still named here, so that
asking for it is refused as "not available" and not as unknown. Building a row
is a change to its line and one more branch in the search; nothing else
knows the rows.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from contracts.enums import ChunkSet, RetrieverConfig
from contracts.errors import DomainError, ErrorCode

ROW_NOT_AVAILABLE_MESSAGE = (
    "That retrieval row is not available yet. Only row r3 can be used for now."
)
CHUNK_SET_NOT_AVAILABLE_MESSAGE = (
    "That retrieval row reads a set of chunks that is not available yet."
)


class SearchMethod(StrEnum):
    """How a row finds its chunks."""

    VECTOR = "vector"
    # Vector and full-text search, fused with reciprocal rank fusion.
    HYBRID = "hybrid"
    HYBRID_RERANKED = "hybrid_reranked"
    AI_SEARCH_HYBRID = "ai_search_hybrid"
    AI_SEARCH_AGENTIC = "ai_search_agentic"


@dataclass(frozen=True, slots=True)
class RetrieverRow:
    config: RetrieverConfig
    chunk_set: ChunkSet
    method: SearchMethod
    # Whether this build can search with the row.
    built: bool = False


_ROWS = (
    RetrieverRow(RetrieverConfig.R1, ChunkSet.FIXED, SearchMethod.VECTOR),
    RetrieverRow(RetrieverConfig.R2, ChunkSet.SMART, SearchMethod.VECTOR),
    RetrieverRow(RetrieverConfig.R3, ChunkSet.SMART, SearchMethod.HYBRID, built=True),
    RetrieverRow(RetrieverConfig.R4, ChunkSet.SMART, SearchMethod.HYBRID_RERANKED),
    RetrieverRow(RetrieverConfig.R5, ChunkSet.SMART, SearchMethod.AI_SEARCH_HYBRID),
    RetrieverRow(RetrieverConfig.R6, ChunkSet.SMART, SearchMethod.AI_SEARCH_AGENTIC),
)
ROWS: Mapping[RetrieverConfig, RetrieverRow] = MappingProxyType(
    {row.config: row for row in _ROWS}
)
# The chunk sets the ingestion writes so far: the `fixed` set comes with Epic 3.
INGESTED_CHUNK_SETS: frozenset[ChunkSet] = frozenset({ChunkSet.SMART})


def row_to_search(config: RetrieverConfig) -> RetrieverRow:
    """The row a search runs with; `retriever_not_available` when it is not built yet."""
    row = ROWS[config]
    if not row.built:
        raise DomainError(ErrorCode.RETRIEVER_NOT_AVAILABLE, ROW_NOT_AVAILABLE_MESSAGE)
    return row


def chunk_set_to_read(config: RetrieverConfig | None) -> ChunkSet:
    """The chunk set a rule is read from: the row's, or `smart` when no row is named.

    A rule read needs the row's chunks, not its search: every row on an
    ingested set answers, built or not.
    """
    chunk_set = ChunkSet.SMART if config is None else ROWS[config].chunk_set
    if chunk_set not in INGESTED_CHUNK_SETS:
        raise DomainError(
            ErrorCode.RETRIEVER_NOT_AVAILABLE, CHUNK_SET_NOT_AVAILABLE_MESSAGE
        )
    return chunk_set
