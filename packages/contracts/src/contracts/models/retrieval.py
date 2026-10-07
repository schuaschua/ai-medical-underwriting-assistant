"""Payloads of the operations `retrieval` owns (AD-11, AD-12)."""

from typing import Annotated

from pydantic import Field, StringConstraints

from contracts.base import ContractModel, Milliseconds, NonEmptyStr, PageNumber, Score
from contracts.enums import ChunkSet, RetrieverConfig
from contracts.rules import RuleId

DEFAULT_TOP_K = 5
MAX_TOP_K = 50
# A query is a sentence or two about one fact: this is room for a paragraph.
# A longer one is refused, not cut, before it is sent to a model.
MAX_QUERY_CHARS = 2000

Rank = Annotated[int, Field(ge=1)]


class SearchRequest(ContractModel):
    """Request of `POST /searches`; the result is not stored."""

    # Not blank, kept as given, and no longer than `MAX_QUERY_CHARS`.
    query: Annotated[str, StringConstraints(pattern=r"\S", max_length=MAX_QUERY_CHARS)]
    retriever_config: RetrieverConfig
    top_k: Annotated[int, Field(ge=1, le=MAX_TOP_K)] = DEFAULT_TOP_K


class SearchItem(ContractModel):
    chunk_id: NonEmptyStr
    # Only the rules the chunk defines, never rules it refers to (AD-12).
    rule_ids: list[RuleId]
    rank: Rank
    score: Score
    text: str
    manual_page: PageNumber
    impairment: NonEmptyStr


class SearchResponse(ContractModel):
    retriever_config: RetrieverConfig
    latency_ms: Milliseconds
    items: list[SearchItem]


class RuleReadQuery(ContractModel):
    """Query of `GET /rules/{rule_id}`; without the parameter the `smart` chunk is returned."""

    retriever_config: RetrieverConfig | None = None


class RuleText(ContractModel):
    """Response of `GET /rules/{rule_id}`."""

    rule_id: RuleId
    chunk_id: NonEmptyStr
    chunk_set: ChunkSet
    text: str
    manual_page: PageNumber
    impairment: NonEmptyStr
    # The rules the text refers to and does not define, in order of first
    # mention (AD-12). AD-15: a rule read may be followed to these.
    reference_rule_ids: list[RuleId]
