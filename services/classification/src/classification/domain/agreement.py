"""The agreement rule: what repeated runs of the model say together (AD-13)."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from contracts.enums import PageType
from contracts.models.classification import ClassifierOutput

# The order a tie is settled in: the order of the contracts' `PageType`.
_TIE_ORDER = {page_type: position for position, page_type in enumerate(PageType)}


@dataclass(frozen=True, slots=True)
class Agreement:
    page_type: PageType
    # The share of the runs that named `page_type`, from 0 to 1.
    confidence: float
    reason: str
    agreeing_runs: int


def agree(runs: Sequence[ClassifierOutput]) -> Agreement:
    """Reduce the runs of one page to one answer.

    The page type is the one most runs named, and the confidence is the share
    of the runs that named it. When two or more types are named equally
    often, the one that comes first in the contracts' `PageType` order wins;
    such a page has a confidence of a half at most. The reason is that of the
    first run, in the order given, that named the winning type.
    """
    if not runs:
        raise ValueError("there is nothing to agree on without a run")
    counts = Counter(run.page_type for run in runs)
    most = max(counts.values())
    page_type = min(
        (page_type for page_type, count in counts.items() if count == most),
        key=_TIE_ORDER.__getitem__,
    )
    reason = next(run.reason for run in runs if run.page_type is page_type)
    return Agreement(
        page_type=page_type,
        confidence=most / len(runs),
        reason=reason,
        agreeing_runs=most,
    )
