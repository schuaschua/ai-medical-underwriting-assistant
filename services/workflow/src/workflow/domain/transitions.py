"""Which status may follow which: the one table for cases, pages and the redaction stage.

A stage result can arrive late, twice or out of order. The store applies a
status change only from a status listed here, in the statement that makes the
change, so such a result changes nothing.
"""

from collections.abc import Mapping
from types import MappingProxyType

from contracts.enums import CaseStatus, PageStatus, StageStatus

_NONE: frozenset[PageStatus] = frozenset()

# A page moves forward only; `failed` can follow any status that is not final.
PAGE_TRANSITIONS: Mapping[PageStatus, frozenset[PageStatus]] = MappingProxyType(
    {
        PageStatus.UPLOADED: frozenset({PageStatus.CLASSIFIED, PageStatus.FAILED}),
        PageStatus.CLASSIFIED: frozenset(
            {
                PageStatus.AWAITING_CUSTOMER,
                PageStatus.AWAITING_TRIAGE,
                PageStatus.EXTRACTING,
                PageStatus.FAILED,
            }
        ),
        PageStatus.AWAITING_CUSTOMER: frozenset(
            {PageStatus.DISCARDED, PageStatus.AWAITING_TRIAGE, PageStatus.FAILED}
        ),
        PageStatus.AWAITING_TRIAGE: frozenset(
            {PageStatus.EXTRACTING, PageStatus.DENIED, PageStatus.FAILED}
        ),
        PageStatus.EXTRACTING: frozenset({PageStatus.EXTRACTED, PageStatus.FAILED}),
        # Final: nothing follows.
        PageStatus.EXTRACTED: _NONE,
        PageStatus.DISCARDED: _NONE,
        PageStatus.DENIED: _NONE,
        PageStatus.FAILED: _NONE,
    }
)

CASE_TRANSITIONS: Mapping[CaseStatus, frozenset[CaseStatus]] = MappingProxyType(
    {
        CaseStatus.RUNNING: frozenset(
            {CaseStatus.AWAITING_HUMAN, CaseStatus.COMPLETED, CaseStatus.FAILED}
        ),
        CaseStatus.AWAITING_HUMAN: frozenset(
            {CaseStatus.RUNNING, CaseStatus.COMPLETED, CaseStatus.FAILED}
        ),
        CaseStatus.COMPLETED: frozenset(),
        CaseStatus.FAILED: frozenset(),
    }
)

REDACTION_TRANSITIONS: Mapping[StageStatus, frozenset[StageStatus]] = MappingProxyType(
    {
        StageStatus.RUNNING: frozenset({StageStatus.DONE, StageStatus.FAILED}),
        StageStatus.DONE: frozenset(),
        StageStatus.FAILED: frozenset(),
    }
)

# A failed case takes no further result: nothing about it or its pages changes.
CASE_STATUSES_TAKING_RESULTS: frozenset[CaseStatus] = frozenset(CaseStatus) - {
    CaseStatus.FAILED
}


def _before[S](table: Mapping[S, frozenset[S]], target: S) -> frozenset[S]:
    return frozenset(source for source, targets in table.items() if target in targets)


def page_statuses_before(
    target: PageStatus, only_from: PageStatus | None = None
) -> frozenset[PageStatus]:
    """The page statuses from which `target` may be reached.

    With `only_from`, that status alone, and only if the table allows it.
    """
    before = _before(PAGE_TRANSITIONS, target)
    return before if only_from is None else before & {only_from}


def case_statuses_before(target: CaseStatus) -> frozenset[CaseStatus]:
    """The case statuses from which `target` may be reached."""
    return _before(CASE_TRANSITIONS, target)


def redaction_statuses_before(target: StageStatus) -> frozenset[StageStatus]:
    """The redaction statuses from which `target` may be reached."""
    return _before(REDACTION_TRANSITIONS, target)
