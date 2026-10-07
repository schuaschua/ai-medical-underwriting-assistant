"""The case orchestration: sequencing and gate routing only (spine AD-2, AD-5, AD-7).

Orchestrator code is replayed from its history, so it must be deterministic:
no clock, no random value, no I/O. It names the next activity, and it calls
the gate's pure rule on values an activity returned. Every activity is one
step of work that may run more than once.

A case whose page waits for a person stays alive here, waiting for that
person's decision as an external event (AD-5): no polling and no timer. So a
case may be in flight for as long as a person takes, and a replay of its
history against other code would not match.

Versioning. Story 2.4 changed what this body yields: every page that reaches
`extracting` now gets an extraction activity, the waits and the extractions
run side by side, and the orchestration ends only when the case is final. It
was changed in place, under the same name, because nothing is deployed (the
Azure environment is torn down while the stories are built) and the local
emulator keeps its state in memory: no case can be waiting on the old body.
That will not hold once a deployed case can be waiting for a person. From
then on a change that adds, removes or reorders what is yielded, or renames
an event, must be made as a new version of the orchestration: a new name
beside `CASE_LIFECYCLE`, registered with the worker together with the old
one, which keeps running the cases it started; new cases are started under
the new name. The only other way is to deploy when no case waits.
"""

from collections.abc import Generator
from datetime import timedelta
from typing import Any

from durabletask import task

from contracts.decisions import STATUSES_AWAITING_A_DECISION
from contracts.enums import CaseStatus, PageStatus, StopAfter
from workflow.domain.case_status import (
    FINAL_PAGE_STATUSES,
    case_status_after_gate,
    case_status_following,
)
from workflow.domain.decisions import status_a_decision_leaves
from workflow.domain.gate import Route, is_unit_number, route_page
from workflow.settings import Settings

# The names the engine keeps in its history: changing one orphans running cases.
CASE_LIFECYCLE = "case_lifecycle"
CONFIRM_CASE_STARTED = "confirm_case_started"
REDACT_DOCUMENT = "redact_document"
CLASSIFY_PAGE = "classify_page"
ROUTE_PAGE = "route_page"
EXTRACT_FACTS = "extract_facts"
SETTLE_CASE_AFTER_GATE = "settle_case_after_gate"
MARK_CASE_FAILED = "mark_case_failed"

# What an activity answers with. An error that no retry can mend is an
# answer, not a raised failure: the engine retries every failure (AD-6).
OUTCOME = "outcome"
OK = "ok"
REFUSED = "refused"
# The case status an activity left the case in, and the pages a done redaction made.
CASE_STATUS = "case_status"
PAGE_IDS = "page_ids"
FAILED = "failed"
# What a done classification hands on for the gate: small values only (AD-6).
CLASSIFICATION_ID = "classification_id"
IS_MEDICAL = "is_medical"
CONFIDENCE = "confidence"
# AD-7: the gate's threshold as the confirm step answered it. It is kept in
# the case's history with that answer, so a replay routes with the same value
# whatever the setting has become since.
GATE_THRESHOLD = "gate_threshold"
# The route the trail holds for a page, as the route activity answers it.
ROUTE = "route"
# The status of each page, by page id, as the settle after the gate read them.
PAGE_STATUSES = "page_statuses"

# AD-5: the external event that tells a case of one stored decision. Its
# payload is the decision. The name carries the status the page had for it,
# so the keep of a page and its later accept or deny are told apart, and a
# decision told twice wakes nothing a second time.
_DECISION_EVENT = "decision"
# The statuses after which nothing more happens to a case.
_ENDED = frozenset({CaseStatus.FAILED.value, CaseStatus.COMPLETED.value})


# The statuses the lifecycle knows what to do with once the gate is behind a
# page: it is extracted, it waits for a person, or nothing more happens to it.
_STATUSES_WITH_A_NEXT_STEP = (
    frozenset({PageStatus.EXTRACTING})
    | STATUSES_AWAITING_A_DECISION
    | FINAL_PAGE_STATUSES
)


def decision_event(page_id: str, awaited: PageStatus) -> str:
    """The name of the event for a decision about a page that had the status `awaited`."""
    return f"{_DECISION_EVENT}.{awaited.value}.{page_id}"


CaseLifecycle = task.Orchestrator[dict[str, Any], dict[str, str]]


def _stop_after(started: dict[str, Any]) -> StopAfter | None:
    """Where the case was told to stop, as the start stored it; anything else is "nowhere"."""
    try:
        return StopAfter(str(started.get("stop_after")))
    except ValueError:
        return None


def _gate_route(answer: dict[str, Any], threshold: float) -> Route | None:
    """The gate's route for one classified page, or None if the answer lacks what the gate needs."""
    is_medical = answer.get(IS_MEDICAL)
    confidence = answer.get(CONFIDENCE)
    if (
        not isinstance(answer.get(CLASSIFICATION_ID), str)
        or not isinstance(is_medical, bool)
        or not is_unit_number(confidence)
    ):
        return None
    return route_page(
        is_medical=is_medical, confidence=float(confidence), threshold=threshold
    )


def _stored_route(answer: dict[str, Any]) -> Route | None:
    """The route a route activity says the trail holds for its page, if it names one."""
    try:
        return Route(str(answer.get(ROUTE)))
    except ValueError:
        return None


def _settled_pages(
    answer: dict[str, Any], page_ids: list[str]
) -> dict[str, PageStatus] | None:
    """The status the settle read for each of the case's pages; None if one is missing or unknown."""
    answered = answer.get(PAGE_STATUSES)
    if not isinstance(answered, dict):
        return None
    try:
        return {page_id: PageStatus(str(answered[page_id])) for page_id in page_ids}
    except (KeyError, ValueError):
        return None


def activity_retry_policy(settings: Settings) -> task.RetryPolicy:
    """AD-6: a failed activity is tried again, each wait longer than the last."""
    return task.RetryPolicy(
        first_retry_interval=timedelta(seconds=settings.activity_first_retry_seconds),
        max_number_of_attempts=settings.activity_max_attempts,
        backoff_coefficient=settings.activity_backoff_coefficient,
    )


def stage_retry_policy(settings: Settings) -> task.RetryPolicy:
    """AD-6: a stage command that failed, or was answered `in_progress`, is sent again.

    The waits grow to a limit, and the attempts together outlast the stage's
    own deadline: a command repeated while the stage still works on it is
    answered with that work's result in the end.
    """
    return task.RetryPolicy(
        first_retry_interval=timedelta(seconds=settings.activity_first_retry_seconds),
        max_number_of_attempts=settings.stage_max_attempts,
        backoff_coefficient=settings.activity_backoff_coefficient,
        max_retry_interval=timedelta(
            seconds=max(
                settings.stage_max_retry_seconds, settings.activity_first_retry_seconds
            )
        ),
    )


def build_case_lifecycle(
    retry_policy: task.RetryPolicy, stage_policy: task.RetryPolicy | None = None
) -> CaseLifecycle:
    """Build the one orchestration of a case. Its instance id is the `case_id`.

    `stage_policy` is the retry policy of the stage commands; without one
    they are retried like every other activity. Nothing of the settings'
    values is built in: what a case's run depends on comes from the answers
    in its history.
    """
    stage_policy = stage_policy or retry_policy

    def case_lifecycle(
        context: task.OrchestrationContext, started: dict[str, Any]
    ) -> Generator[task.Task[Any], Any, dict[str, str]]:
        # `started` is the answer of the start: the case id and what the case
        # runs with. Ids and small values only (AD-6).
        case_id: str = started["case_id"]
        eval_run_id = started.get("eval_run_id")
        about = {"case_id": case_id, "eval_run_id": eval_run_id}
        try:
            confirmed: dict[str, Any] | None = yield context.call_activity(
                CONFIRM_CASE_STARTED, input=case_id, retry_policy=retry_policy
            )
        except task.TaskFailedError:
            # Every retry failed.
            confirmed = None
        if (
            confirmed is None
            or confirmed.get(OUTCOME) != OK
            # AD-7: without a threshold from the confirm step no page could
            # be routed; nothing is assumed in its place.
            or not is_unit_number(confirmed.get(GATE_THRESHOLD))
        ):
            # The case cannot go on. It is marked failed, with its one
            # case-level `stage.failed` event (AD-8), instead of being left
            # `running` for ever.
            yield context.call_activity(
                MARK_CASE_FAILED, input=about, retry_policy=retry_policy
            )
            return {"case_id": case_id, CASE_STATUS: FAILED}

        gate_threshold = float(confirmed[GATE_THRESHOLD])

        # AD-21: redaction is the first stage. Nothing else reads the document
        # before it is done.
        try:
            redacted: dict[str, Any] | None = yield context.call_activity(
                REDACT_DOCUMENT, input=about, retry_policy=stage_policy
            )
        except task.TaskFailedError:
            redacted = None
        if redacted is None or redacted.get(OUTCOME) != OK:
            # No result could be had or recorded, or `intake` does not hold
            # the case: as above, the case is marked failed.
            yield context.call_activity(
                MARK_CASE_FAILED, input=about, retry_policy=retry_policy
            )
            return {"case_id": case_id, CASE_STATUS: FAILED}
        if redacted[CASE_STATUS] == FAILED:
            # A failed redaction has failed the case already, in the recording
            # of its result (AD-8): the lifecycle ends, and the customer
            # uploads again.
            return {"case_id": case_id, CASE_STATUS: FAILED}

        # AD-13: every page is classified, each by a command of its own, all
        # at once, with the one contender the case was started with. Ids only
        # go in (AD-6): the stage reads the page from `intake` itself.
        page_ids: list[str] = list(redacted.get(PAGE_IDS, []))
        classifying: list[task.Task[dict[str, Any]]] = [
            context.call_activity(
                CLASSIFY_PAGE,
                input={
                    **about,
                    "page_id": page_id,
                    "contender": started.get("classifier_contender"),
                },
                retry_policy=stage_policy,
            )
            for page_id in page_ids
        ]
        # A redaction that is done and names no page leaves nothing to
        # classify and nothing that would ever move the case on.
        classified: list[dict[str, Any]] | None = None
        if classifying:
            try:
                # Waits for every page, also when one of them has failed.
                classified = yield task.when_all(classifying)
            except task.TaskFailedError:
                # Every retry of at least one page failed.
                classified = None
        if classified is None or any(
            answer.get(OUTCOME) != OK for answer in classified
        ):
            # There is no page; or a page's classification was refused, never
            # answered or could not be recorded: the case cannot go on.
            yield context.call_activity(
                MARK_CASE_FAILED, input=about, retry_policy=retry_policy
            )
            return {"case_id": case_id, CASE_STATUS: FAILED}
        if any(answer[CASE_STATUS] == FAILED for answer in classified):
            # A failed classification has failed its page and the case, in
            # the recording of its result (AD-8).
            return {"case_id": case_id, CASE_STATUS: FAILED}

        # AD-7: the gate. Every page is `classified`; each is routed by the
        # one rule, on what its classification answered, and the route is
        # recorded by an activity, with its status change and its event in
        # one transaction (AD-8).
        routes = [
            route
            for route in (_gate_route(answer, gate_threshold) for answer in classified)
            if route is not None
        ]
        if len(routes) != len(classified):
            # A classification answered without what the gate needs. No page
            # is routed on a guess: the case cannot go on.
            yield context.call_activity(
                MARK_CASE_FAILED, input=about, retry_policy=retry_policy
            )
            return {"case_id": case_id, CASE_STATUS: FAILED}
        routing: list[task.Task[dict[str, str]]] = [
            context.call_activity(
                ROUTE_PAGE,
                input={
                    **about,
                    "page_id": page_id,
                    CLASSIFICATION_ID: answer[CLASSIFICATION_ID],
                    ROUTE: route.value,
                    "threshold": gate_threshold,
                },
                retry_policy=retry_policy,
            )
            for page_id, answer, route in zip(page_ids, classified, routes, strict=True)
        ]
        try:
            routed: list[dict[str, str]] | None = yield task.when_all(routing)
        except task.TaskFailedError:
            routed = None
        if routed is None or any(answer.get(OUTCOME) != OK for answer in routed):
            # A route was refused or could not be recorded: as above.
            yield context.call_activity(
                MARK_CASE_FAILED, input=about, retry_policy=retry_policy
            )
            return {"case_id": case_id, CASE_STATUS: FAILED}
        if any(answer.get(CASE_STATUS) == FAILED for answer in routed):
            # The case failed while its pages were being routed.
            return {"case_id": case_id, CASE_STATUS: FAILED}
        # The case is settled on the routes the trail holds, as the route
        # activities answered them, not on what was worked out above: a route
        # recorded by an earlier run of an activity is the one that counts.
        stored = [
            route
            for route in (_stored_route(answer) for answer in routed)
            if route is not None
        ]
        if len(stored) != len(routed):
            yield context.call_activity(
                MARK_CASE_FAILED, input=about, retry_policy=retry_policy
            )
            return {"case_id": case_id, CASE_STATUS: FAILED}

        after_gate = case_status_after_gate(
            [route.page_status for route in stored], _stop_after(started)
        )
        if after_gate is CaseStatus.RUNNING:
            # Every page went on to extraction: nobody is waited for, and
            # the case stays `running` until its pages are extracted.
            page_statuses: dict[str, PageStatus] | None = {
                page_id: route.page_status
                for page_id, route in zip(page_ids, stored, strict=True)
            }
        else:
            # A page waits for a person, or the case was told to stop after
            # the gate. The case is given the status its stored pages give
            # it: the activity works that out in the database, by the same
            # rule, so a late run of it cannot undo a decision made before it.
            try:
                settled: dict[str, Any] | None = yield context.call_activity(
                    SETTLE_CASE_AFTER_GATE,
                    input={"case_id": case_id},
                    retry_policy=retry_policy,
                )
            except task.TaskFailedError:
                settled = None
            if (
                not isinstance(settled, dict)
                or settled.get(OUTCOME) != OK
                # An answer that does not say what the case is: as a failed step.
                or not isinstance(settled.get(CASE_STATUS), str)
            ):
                yield context.call_activity(
                    MARK_CASE_FAILED, input=about, retry_policy=retry_policy
                )
                return {"case_id": case_id, CASE_STATUS: FAILED}
            if after_gate is CaseStatus.COMPLETED or settled[CASE_STATUS] in _ENDED:
                # Told to stop after the gate; or the case has failed; or
                # every waiting page was decided already and none is left
                # in work.
                return {"case_id": case_id, CASE_STATUS: settled[CASE_STATUS]}

            # What each page is: the status the settle read for it, in the
            # transaction that settled the case, and later the one a
            # decision or an extraction leaves it in. Not its route: a page
            # decided before the settle ran is not waited for, whether or
            # not its event is still to come.
            page_statuses = _settled_pages(settled, page_ids)
        if page_statuses is None or any(
            status not in _STATUSES_WITH_A_NEXT_STEP
            for status in page_statuses.values()
        ):
            # The settle did not say what the pages are; or a page is in a
            # status that nothing here goes on from (`uploaded`,
            # `classified`): it would be skipped, and the lifecycle would
            # end with the case still `running`. As the other steps that
            # cannot go on: the case is marked failed.
            yield context.call_activity(
                MARK_CASE_FAILED, input=about, retry_policy=retry_policy
            )
            return {"case_id": case_id, CASE_STATUS: FAILED}

        # AD-5, AD-10, AD-14: from here on each page goes its own way, side
        # by side with the others. A page that is `extracting`, by the gate
        # or by an accept, gets one extraction command; a page that waits
        # for a person gets a wait for that person's decision, an external
        # event the decision operation raises once it has stored the
        # decision (and `workflow` itself raises again if that was lost). A
        # decision stored after the settle read the pages has its event
        # kept by the engine until its wait is made. Whatever follows a
        # decision for one page starts when that decision comes, not when
        # every page is decided. No polling and no timer.
        extracting: dict[str, task.Task[Any]] = {}
        waiting: dict[str, task.Task[Any]] = {}

        def go_on(page_id: str, status: PageStatus) -> None:
            """Start what a page in that status needs next; a final page needs nothing."""
            if status is PageStatus.EXTRACTING:
                # Ids only (AD-6): the stage reads the page from `intake` itself.
                extracting[page_id] = context.call_activity(
                    EXTRACT_FACTS,
                    input={**about, "page_id": page_id},
                    retry_policy=stage_policy,
                )
            elif status in STATUSES_AWAITING_A_DECISION:
                waiting[page_id] = context.wait_for_external_event(
                    decision_event(page_id, status)
                )

        for page_id in page_ids:
            go_on(page_id, page_statuses[page_id])
        while extracting or waiting:
            yield task.when_any([*extracting.values(), *waiting.values()])
            for page_id, extracted in list(extracting.items()):
                if not extracted.is_complete:
                    continue
                del extracting[page_id]
                # A failed task is one whose every retry failed.
                answer = None if extracted.is_failed else extracted.get_result()
                if not isinstance(answer, dict) or answer.get(OUTCOME) != OK:
                    # No result could be had or recorded, or the stage does
                    # not hold the page: the case cannot go on.
                    yield context.call_activity(
                        MARK_CASE_FAILED, input=about, retry_policy=retry_policy
                    )
                    return {"case_id": case_id, CASE_STATUS: FAILED}
                if answer.get(CASE_STATUS) == FAILED:
                    # A failed extraction has failed its page and the case,
                    # in the recording of its result (AD-8); or the case had
                    # failed before. The lifecycle ends here, also while
                    # other pages wait for a person: a failed case takes no
                    # decision, so nothing would ever wake those waits.
                    return {"case_id": case_id, CASE_STATUS: FAILED}
                # The recording moved the page to `extracted`, and the case
                # to `completed` if it was the last page in work.
                page_statuses[page_id] = PageStatus.EXTRACTED
            for page_id, decided in list(waiting.items()):
                if not decided.is_complete:
                    continue
                del waiting[page_id]
                awaited = page_statuses[page_id]
                left = status_a_decision_leaves(awaited, decided.get_result())
                # An event that carries no decision for that status changes
                # nothing: the page is waited for again.
                page_statuses[page_id] = left if left is not None else awaited
                # A kept page now waits for the underwriter; an accepted one
                # is extracted; a discarded or denied one is final.
                go_on(page_id, page_statuses[page_id])
        # Every page is final. The recordings of the decisions and of the
        # extraction results have kept the case status in step with the
        # pages (AD-5) and completed the case; this is the same rule, on the
        # same pages.
        return {
            "case_id": case_id,
            CASE_STATUS: case_status_following(page_statuses.values()).value,
        }

    return case_lifecycle
