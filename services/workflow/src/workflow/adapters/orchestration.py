"""The case orchestration: sequencing only (spine AD-2, AD-5).

Orchestrator code is replayed from its history, so it must be deterministic:
no clock, no random value, no I/O. It only names the next activity. Every
activity is one step of work that may run more than once.
"""

from collections.abc import Generator
from datetime import timedelta
from typing import Any

from durabletask import task

from workflow.settings import Settings

# The names the engine keeps in its history: changing one orphans running cases.
CASE_LIFECYCLE = "case_lifecycle"
CONFIRM_CASE_STARTED = "confirm_case_started"
REDACT_DOCUMENT = "redact_document"
CLASSIFY_PAGE = "classify_page"
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

CaseLifecycle = task.Orchestrator[dict[str, Any], dict[str, str]]


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
    they are retried like every other activity.
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
            confirmed: dict[str, str] | None = yield context.call_activity(
                CONFIRM_CASE_STARTED, input=case_id, retry_policy=retry_policy
            )
        except task.TaskFailedError:
            # Every retry failed.
            confirmed = None
        if confirmed is None or confirmed.get(OUTCOME) != OK:
            # The case cannot go on. It is marked failed, with its one
            # case-level `stage.failed` event (AD-8), instead of being left
            # `running` for ever.
            yield context.call_activity(
                MARK_CASE_FAILED, input=about, retry_policy=retry_policy
            )
            return {"case_id": case_id, CASE_STATUS: FAILED}

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
        classifying: list[task.Task[dict[str, str]]] = [
            context.call_activity(
                CLASSIFY_PAGE,
                input={
                    **about,
                    "page_id": page_id,
                    "contender": started.get("classifier_contender"),
                },
                retry_policy=stage_policy,
            )
            for page_id in redacted.get(PAGE_IDS, [])
        ]
        # A redaction that is done and names no page leaves nothing to
        # classify and nothing that would ever move the case on.
        classified: list[dict[str, str]] | None = None
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
        # Every page is `classified` and the case is `running`. The lifecycle
        # ends here for now: the gate routes each page from this point (story 1.9).
        return {"case_id": case_id, CASE_STATUS: redacted[CASE_STATUS]}

    return case_lifecycle
