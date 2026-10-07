"""Reciprocal rank fusion: one ranked list out of several (spine AD-11, row `r3`).

A chunk's score is the sum, over the lists that hold it, of
`1 / (k + its rank in that list)`, ranks counted from 1. Only ranks count,
never the lists' own scores: a cosine distance and a full-text rank are not
on one scale, and the fusion does not pretend they are. A chunk that only
one list holds still gets that list's share.
"""

from collections.abc import Sequence
from dataclasses import dataclass

# The constant of the method's authors (Cormack, Clarke and Buettcher, 2009),
# and what every common implementation uses: large enough that the first few
# ranks of a list count about alike.
RRF_K = 60


@dataclass(frozen=True, slots=True)
class Fused:
    chunk_id: str
    # Larger is better. At most `lists / (k + 1)`: 2/61 for two lists.
    score: float


def reciprocal_rank_fusion(*ranked_lists: Sequence[str], k: int = RRF_K) -> list[Fused]:
    """Fuse lists of chunk ids, each best first, into one list, best first.

    Chunks with the same score come in the order of their `chunk_id`, so the
    same lists always give the same order. An id that a list holds twice
    counts at its first place only.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, chunk_id in enumerate(dict.fromkeys(ranked), start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return [
        Fused(chunk_id, score)
        for chunk_id, score in sorted(
            scores.items(), key=lambda item: (-item[1], item[0])
        )
    ]
