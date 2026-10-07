"""Rule recall: how often a row's search finds the right rule (spine AD-17, NFR7).

Measured apart from the agent. For every expected fact that meets a rule, one
search with the query the contracts' query builder makes from the fact's
statement, the same for every row. A hit is one of that fact's expected rule
ids among the rule ids of the answered items. An agent that writes better
queries therefore cannot make a weak retriever look good.
"""

import asyncio
import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from bakeoff.answer_key import AnswerKeyEntry
from bakeoff.client import WebClient, WebError
from contracts.enums import RetrieverConfig
from contracts.errors import ErrorCode
from contracts.models.retrieval import SearchRequest, SearchResponse
from contracts.models.web import FailedSearch
from contracts.query import build_fact_query

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FactSearch:
    """The one search of one expected fact, the same for every row."""

    case_key: str
    # The position of the fact among its case's expected facts, from 1.
    fact_number: int
    query: str
    rule_ids: frozenset[str]


def fact_searches(entries: Iterable[AnswerKeyEntry]) -> list[FactSearch]:
    """The searches of a case set: one per expected fact that meets a rule.

    A fact that meets no rule is not searched for and is in no denominator.
    """
    return [
        FactSearch(
            case_key=entry.case_key,
            fact_number=number,
            # The fixed query: the builder's, from the statement, and nothing else.
            query=build_fact_query(fact.statement),
            rule_ids=frozenset(fact.rule_ids),
        )
        for entry in entries
        for number, fact in enumerate(entry.expected_facts, start=1)
        if fact.rule_ids
    ]


def is_hit(search: FactSearch, response: SearchResponse, top_k: int) -> bool:
    """Whether an expected rule of the fact is among the first `top_k` answered items."""
    found = {rule_id for item in response.items[:top_k] for rule_id in item.rule_ids}
    return not search.rule_ids.isdisjoint(found)


def percentile(values: Sequence[int], share: float) -> int | None:
    """The value at or below which `share` of the values lie (nearest rank); None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(share * len(ordered)), 1) - 1]


@dataclass
class RowRecall:
    """What the searches of one row gave."""

    retriever_config: RetrieverConfig
    # False when the row answered "not available": it is not measured.
    available: bool = True
    hits: int = 0
    searches: int = 0
    # `retrieval`'s own `latency_ms` of every search that was answered.
    latencies: list[int] = field(default_factory=list)
    failed: list[FailedSearch] = field(default_factory=list)

    def count(self, search: FactSearch, response: SearchResponse, top_k: int) -> None:
        """Count an answered search."""
        self.searches += 1
        self.hits += is_hit(search, response, top_k)
        self.latencies.append(response.latency_ms)

    def count_failure(self, search: FactSearch, code: ErrorCode | None) -> None:
        """Count a search that failed: a miss, and listed."""
        self.searches += 1
        self.failed.append(
            FailedSearch(
                retriever_config=self.retriever_config,
                case_key=search.case_key,
                fact_number=search.fact_number,
                error_code=code,
            )
        )


async def measure_row(
    client: WebClient,
    row: RetrieverConfig,
    searches: Sequence[FactSearch],
    top_k: int,
    limit: asyncio.Semaphore,
) -> RowRecall:
    """Send every search to one row and count the answers."""
    recall = RowRecall(row)

    async def one(search: FactSearch) -> None:
        request = SearchRequest(query=search.query, retriever_config=row, top_k=top_k)
        async with limit:
            if not recall.available:
                return
            try:
                response = await client.search(request)
            except WebError as error:
                if error.code is ErrorCode.RETRIEVER_NOT_AVAILABLE:
                    recall.available = False
                    return
                logger.warning(
                    "search failed: row=%s case=%s fact=%d %s",
                    row.value,
                    search.case_key,
                    search.fact_number,
                    error,
                )
                recall.count_failure(search, error.code)
                return
        recall.count(search, response, top_k)

    if searches:
        # The first search alone: a row that is not available says so here,
        # and is not sent the rest.
        await one(searches[0])
        await asyncio.gather(*(one(search) for search in searches[1:]))
    if not recall.available:
        logger.info("row not available, not measured: row=%s", row.value)
        return RowRecall(row, available=False)
    return recall


async def answers(
    client: WebClient, row: RetrieverConfig, query: str, top_k: int
) -> bool:
    """Whether a row can be searched with, for a case set with no fact to score it on.

    The answer is not scored: only "not available" is told from anything else.
    """
    try:
        await client.search(
            SearchRequest(query=query, retriever_config=row, top_k=top_k)
        )
    except WebError as error:
        return error.code is not ErrorCode.RETRIEVER_NOT_AVAILABLE
    return True
