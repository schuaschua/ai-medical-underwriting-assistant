"""The reranker of row `r4`: what the chat model is shown, and what its answer must be (spine AD-11).

Row `r4` is row `r3` with one more step: the best of the fused candidates
are shown to the chat deployment with the query, and it says how relevant
each one is, as a number from 0 to 1. The order of the answer is by that
number. Nothing here calls a model: one function builds the message, the
other reads the answer. An answer that does not rate exactly the
candidates that were shown is no answer at all, and the search fails: the
fused order is never answered in its place, since that would be row `r3`'s
answer under row `r4`'s name.
"""

import json
import math
from collections.abc import Sequence
from typing import Any

from retrieval.domain.entities import IndexedChunk

# The fields of the message the model is shown.
QUERY_FIELD = "query"
CANDIDATES_FIELD = "candidates"
# The fields of the answer the prompt asks for: one entry per candidate.
RANKING_FIELD = "ranking"
CHUNK_ID_FIELD = "chunk_id"
RELEVANCE_FIELD = "relevance"


class RerankAnswerInvalid(Exception):
    """The reranker's answer does not rate the candidates it was given. `reason` is a short code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def rerank_request(query: str, candidates: Sequence[IndexedChunk]) -> str:
    """The message the reranker is shown: the query and the candidates, as one JSON object.

    Both are data for the model to judge. They go in the user turn, never
    in the instructions (security rule 14). A candidate is its id, the
    impairment its rule belongs to and its text, as the chunk table holds
    them; the order is the fused order.
    """
    return json.dumps(
        {
            QUERY_FIELD: query,
            CANDIDATES_FIELD: [
                {
                    CHUNK_ID_FIELD: chunk.chunk_id,
                    "impairment": chunk.impairment,
                    "text": chunk.text,
                }
                for chunk in candidates
            ],
        },
        ensure_ascii=False,
    )


def relevance_by_chunk(answer: str, chunk_ids: Sequence[str]) -> dict[str, float]:
    """The relevance the reranker gave each candidate, by `chunk_id`; `RerankAnswerInvalid` otherwise.

    The answer must be the object that was asked for, and rate every
    candidate once and nothing else, each with a number from 0 to 1.
    """
    if not answer.strip():
        # A refusal, a content filter, or an answer cut off at the token
        # limit before its first character: the gateway gives all as empty.
        raise RerankAnswerInvalid("rerank_empty")
    try:
        parsed: Any = json.loads(answer)
    except ValueError:
        raise RerankAnswerInvalid("rerank_not_json") from None
    if not isinstance(parsed, dict) or set(parsed) != {RANKING_FIELD}:
        raise RerankAnswerInvalid("rerank_not_the_object")
    entries = parsed[RANKING_FIELD]
    if not isinstance(entries, list):
        raise RerankAnswerInvalid("rerank_not_a_list")
    relevance: dict[str, float] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            CHUNK_ID_FIELD,
            RELEVANCE_FIELD,
        }:
            raise RerankAnswerInvalid("rerank_entry_not_the_object")
        chunk_id, value = entry[CHUNK_ID_FIELD], entry[RELEVANCE_FIELD]
        if not isinstance(chunk_id, str):
            raise RerankAnswerInvalid("rerank_chunk_id_not_text")
        if chunk_id in relevance:
            raise RerankAnswerInvalid("rerank_candidate_twice")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RerankAnswerInvalid("rerank_relevance_not_a_number")
        # Only a float can be no number; a whole number of any size is
        # compared as it is (`math.isfinite` would raise on a huge one).
        if isinstance(value, float) and not math.isfinite(value):
            raise RerankAnswerInvalid("rerank_relevance_not_a_number")
        if not 0 <= value <= 1:
            raise RerankAnswerInvalid("rerank_relevance_out_of_range")
        relevance[chunk_id] = float(value)
    given = set(chunk_ids)
    if set(relevance) - given:
        raise RerankAnswerInvalid("rerank_candidate_not_given")
    if given - set(relevance):
        raise RerankAnswerInvalid("rerank_candidate_left_out")
    return relevance
