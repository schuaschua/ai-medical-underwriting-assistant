"""The training job: `python -m classification.train` (spine AD-13, story 4.2).

A one-off job on the `classification` image and identity, started by hand
or by the pipeline, never by a deploy. It calls no other service of ours: it
lists the labelled training pages in the `classifier-training` container
and has Document Intelligence build the custom classifier of the configured
id from that container, which the service reads with its own identity.

It is idempotent: when the classifier exists it trains nothing, and when a
build of it is under way already it waits for that one. It ends with
status 0 when the classifier is there, and with 1 otherwise, after one log
line that names the counts, or the error code and the reason. It refuses to
train unless the container holds exactly the pages its list names, each
with the content the list vouches for, and five or more of every page type.

The pages are redacted before they come into the container (see
`infra/bootstrap/README.md`): this job reads no page and redacts nothing.

Before the process ends its telemetry is sent.
"""

import asyncio
import logging
import sys
import time

import httpx2

from classification.adapters.blob import BlobTrainingPages, build_blob_service
from classification.adapters.classifier import build_classifier
from classification.adapters.telemetry import (
    code_locations,
    configure_logging,
    configure_telemetry,
    shutdown_telemetry,
)
from classification.domain.ports import TrainingFailed
from classification.domain.training import (
    TrainingRefused,
    TrainReport,
    train_classifier,
)
from classification.settings import Settings, get_settings
from contracts.errors import ErrorCode

logger = logging.getLogger(__name__)

OK, FAILED = 0, 1


def missing_settings(settings: Settings) -> list[str]:
    """The variables the job cannot run without and was not given."""
    needed = {
        "CLASSIFICATION_DOC_INTELLIGENCE_ENDPOINT": settings.doc_intelligence_endpoint,
        "CLASSIFICATION_DOC_INTELLIGENCE_CLASSIFIER_ID": (
            settings.doc_intelligence_classifier_id
        ),
        "CLASSIFICATION_BLOB_ACCOUNT_URL": settings.blob_account_url
        or settings.blob_connection_string,
    }
    return [name for name, value in needed.items() if value is None]


async def run(
    settings: Settings, transport: httpx2.AsyncBaseTransport | None = None
) -> TrainReport:
    """One run of the job, under its one deadline."""
    classifier = build_classifier(
        settings, transport, poll_seconds=settings.training_poll_seconds
    )
    try:
        async with asyncio.timeout(settings.training_deadline_seconds):
            return await train_classifier(
                pages=BlobTrainingPages(
                    build_blob_service(settings), settings.training_container
                ),
                builder=classifier,
            )
    finally:
        await classifier.aclose()


def main(
    settings: Settings | None = None,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> int:
    """Run the job once and say how it ended; the return value is the exit status.

    Tests pass a transport that stands in for Document Intelligence.
    """
    configure_logging()
    started = time.monotonic()
    telemetry_on = False
    try:
        try:
            if settings is None:
                settings = get_settings()
            missing = missing_settings(settings)
            if not missing:
                telemetry_on = configure_telemetry(settings)
        except Exception as error:  # noqa: BLE001 - a job that cannot be set up says so in its one line, like any other failure
            # The type only: a settings error's message repeats the values
            # it refused, and one of them may be a connection string.
            logger.error(
                "training failed: code=%s reason=setup_%s",
                ErrorCode.INTERNAL_ERROR.value,
                type(error).__qualname__,
            )
            return FAILED
        if missing:
            # Said before anything is built: the service runs without
            # these, the job does not.
            logger.error(
                "training failed: code=%s reason=not_configured missing=%s",
                ErrorCode.INTERNAL_ERROR.value,
                ",".join(missing),
            )
            return FAILED
        return _run_and_report(settings, transport, started)
    finally:
        if telemetry_on:
            # The process ends after this: what the run's spans and log
            # lines hold is sent first.
            shutdown_telemetry()


def _run_and_report(
    settings: Settings, transport: httpx2.AsyncBaseTransport | None, started: float
) -> int:
    classifier = settings.doc_intelligence_classifier_id
    try:
        report = asyncio.run(run(settings, transport))
    except TrainingRefused as error:
        # security rule 31: codes, a page type or a blob's name, and counts.
        logger.error(
            "training failed: code=%s reason=%s subject=%s pages=%d "
            "classifier_id=%s seconds=%.1f",
            ErrorCode.VALIDATION_FAILED.value,
            error.reason,
            error.subject,
            error.pages,
            classifier,
            time.monotonic() - started,
        )
        return FAILED
    except TrainingFailed as error:
        logger.error(
            "training failed: code=%s reason=%s classifier_id=%s seconds=%.1f",
            ErrorCode.STAGE_FAILED.value,
            error.reason,
            classifier,
            time.monotonic() - started,
        )
        return FAILED
    except TimeoutError:
        logger.error(
            "training failed: code=%s reason=deadline classifier_id=%s seconds=%.1f",
            ErrorCode.STAGE_TIMEOUT.value,
            classifier,
            time.monotonic() - started,
        )
        return FAILED
    except Exception as error:  # noqa: BLE001 - whatever went wrong, the job says so and ends non-zero
        # The error's type and where it was raised, never its message, which
        # can hold an address.
        logger.error(
            "training failed: code=%s reason=%s at=%s classifier_id=%s seconds=%.1f",
            ErrorCode.INTERNAL_ERROR.value,
            type(error).__qualname__,
            " <- ".join(reversed(code_locations(error))),
            classifier,
            time.monotonic() - started,
        )
        return FAILED
    logger.info(
        "training done: classifier_id=%s trained=%s pages=%d page_types=%d "
        "seconds=%.1f",
        classifier,
        "yes" if report.trained else "no",
        report.pages,
        report.page_types,
        time.monotonic() - started,
    )
    return OK


if __name__ == "__main__":
    sys.exit(main())
