"""The reranker of row `r4`: what Cohere Rerank is shown, and what its answer must be (spine AD-11).

Row `r4` is row `r3` with one more step: the best of the fused candidates
are sent to the reranker deployment with the query, in one call, and it
scores how relevant each one is, as a number from 0 to 1. The order of the
answer is by that score. Nothing here calls a model: one function makes a
candidate the document the reranker reads, the other reads the answer. An
answer that does not score exactly the candidates that were sent is no
answer at all, and the search fails: the fused order is never answered in
its place, since that would be row `r3`'s answer under row `r4`'s name.
"""

import json
import math
from typing import Any

from retrieval.domain.entities import IndexedChunk

# The fields of the answer: one result per document, named by its place in
# the request (counted from 0), with the score the reranker gave it.
RESULTS_FIELD = "results"
INDEX_FIELD = "index"
SCORE_FIELD = "relevance_score"


class RerankAnswerInvalid(Exception):
    """The reranker's answer does not score the candidates it was given. `reason` is a short code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def rerank_document(chunk: IndexedChunk) -> str:
    """One candidate as the reranker reads it: the impairment its rule belongs to, then its text.

    Both as the chunk table holds them. The reranker takes plain texts and
    answers by their place in the request, so a candidate's id is not sent.
    """
    return f"{chunk.impairment}\n{chunk.text}"


def scores_in_order(answer: str, candidates: int) -> list[float]:
    """The score the reranker gave each candidate, in the order they were sent; `RerankAnswerInvalid` otherwise.

    The answer must hold one result for every candidate and no other, each
    naming its candidate by its place in the request, with a score that is
    a number from 0 to 1. What else the answer holds is not looked at.
    """
    if not answer.strip():
        raise RerankAnswerInvalid("rerank_empty")
    try:
        parsed: Any = json.loads(answer)
    except ValueError:
        raise RerankAnswerInvalid("rerank_not_json") from None
    if not isinstance(parsed, dict) or RESULTS_FIELD not in parsed:
        raise RerankAnswerInvalid("rerank_not_the_object")
    results = parsed[RESULTS_FIELD]
    if not isinstance(results, list):
        raise RerankAnswerInvalid("rerank_not_a_list")
    scores: dict[int, float] = {}
    for result in results:
        if not isinstance(result, dict) or not {INDEX_FIELD, SCORE_FIELD} <= set(
            result
        ):
            raise RerankAnswerInvalid("rerank_entry_not_the_object")
        index, value = result[INDEX_FIELD], result[SCORE_FIELD]
        if type(index) is not int:
            raise RerankAnswerInvalid("rerank_index_not_a_whole_number")
        if not 0 <= index < candidates:
            raise RerankAnswerInvalid("rerank_candidate_not_given")
        if index in scores:
            raise RerankAnswerInvalid("rerank_candidate_twice")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RerankAnswerInvalid("rerank_score_not_a_number")
        # Only a float can be no number; a whole number of any size is
        # compared as it is (`math.isfinite` would raise on a huge one).
        if isinstance(value, float) and not math.isfinite(value):
            raise RerankAnswerInvalid("rerank_score_not_a_number")
        if not 0 <= value <= 1:
            raise RerankAnswerInvalid("rerank_score_out_of_range")
        scores[index] = float(value)
    if len(scores) != candidates:
        raise RerankAnswerInvalid("rerank_candidate_left_out")
    return [scores[index] for index in range(candidates)]
