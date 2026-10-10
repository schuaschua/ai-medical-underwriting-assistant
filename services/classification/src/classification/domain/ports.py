"""What a classification needs from the outside world; adapters provide it."""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from classification.domain.entities import (
    ClassificationKey,
    ClassifierAnswer,
    KeyRow,
    ListedPage,
    PageContent,
    StoredBlob,
)
from contracts.models.classification import Classification


class ModelUnavailable(Exception):
    """AD-16: the model gateway gave up after its retries."""


class ModelCallFailed(Exception):
    """The model refused the call, and would refuse it again.

    `reason` is a short code for the log, never a message of the model's
    endpoint: such a message could hold what was sent to it.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PageModel(Protocol):
    """The shared chat deployment, asked what one page is (AD-13, AD-16)."""

    async def classify(self, page: PageContent) -> str:
        """Run the model once on the page; return its answer as it gave it.

        The answer is not looked at here: the caller parses it, and an answer
        that is not what was asked for is its to refuse. Raises
        `ModelUnavailable` when the model could not be had, and
        `ModelCallFailed` when it refused the call.
        """
        ...


class ClassifierNotReady(Exception):
    """Document Intelligence has no classifier of the configured id, or does not let the service in.

    Both pass: the classifier is trained, a new role assignment is honoured.
    The same command sent again then classifies the page, so nothing is
    stored for this one. `reason` is a short code for the log.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PageClassifier(Protocol):
    """The Document Intelligence custom classifier, asked what one document is (AD-13)."""

    async def classify(self, pdf: bytes) -> ClassifierAnswer:
        """Have the classifier read a one-page PDF; return what it answered.

        The answer is not judged here. Raises `ModelUnavailable` when the
        service could not be had, `ClassifierNotReady` when it holds no such
        classifier or does not let the service in, and `ModelCallFailed`
        when it refused the call otherwise or its analysis failed.
        """
        ...


class PageReader(Protocol):
    """The reads of `intake` (AD-3): only what redaction left is ever read."""

    async def page_ids_of_case(
        self, case_id: str, trace_context: Mapping[str, str]
    ) -> list[str] | None:
        """The ids of the case's pages; None if `intake` does not hold the case."""
        ...

    async def read_page(
        self, page_id: str, trace_context: Mapping[str, str]
    ) -> PageContent | None:
        """The page's text and thumbnail; None if `intake` does not hold the page."""
        ...

    async def read_page_file(
        self, case_id: str, page_id: str, trace_context: Mapping[str, str]
    ) -> bytes | None:
        """The page as a one-page PDF; None if `intake` does not hold the page."""
        ...


class ClassificationRepository(Protocol):
    """The key row of a classification and what a finished one stores (AD-6)."""

    async def find(self, key: ClassificationKey) -> KeyRow | None:
        """The key row of an earlier command with this key, if there is one."""
        ...

    async def begin(
        self, classification_id: str, key: ClassificationKey, started_at: datetime
    ) -> KeyRow | None:
        """Insert the key row as running, before any work.

        Returns None when this call inserted it; otherwise the row that was
        there already, and nothing is changed.
        """
        ...

    async def finish(
        self,
        classification_id: str,
        result_json: str,
        classification: Classification | None,
    ) -> str:
        """Store the result, and the classification of a done one, in one statement.

        Nothing is written unless the key row is still running. Returns the
        result the row holds afterwards: this one, or the one stored before it.
        """
        ...

    async def release(self, classification_id: str) -> None:
        """Remove the key row if it is still running, so the command can be run again.

        A row that holds a result is left as it is.
        """
        ...

    async def of_case(self, case_id: str) -> list[Classification]:
        """The case's stored classifications, oldest first; failed ones have none."""
        ...


# --- Training the Document Intelligence classifier (story 4.2) ------------------


class TrainingFailed(Exception):
    """The training job could not read its pages or have the classifier built.

    `reason` is a short code for the log, never a message of a service.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TrainingPages(Protocol):
    """The `classifier-training` container, which `classification` owns (AD-4)."""

    def container_url(self) -> str:
        """The container's address, which Document Intelligence reads with its own identity."""
        ...

    async def contents(self) -> tuple[list[ListedPage], list[StoredBlob]]:
        """The list of prepared pages, and every blob the container holds beside it.

        The list is empty when the container has none. A blob the list
        names comes with the MD5 of its content.
        """
        ...


class ClassifierBuilder(Protocol):
    """Document Intelligence, asked to build the classifier of the configured id."""

    async def exists(self) -> bool:
        """Whether the classifier is there already."""
        ...

    async def build(self, container_url: str, prefixes: Mapping[str, str]) -> None:
        """Build the classifier from the container and wait until it is built.

        `prefixes` names, for each document type, the folder of the
        container that holds its pages. A build of this classifier that is
        under way already is waited for, not started again. Raises
        `TrainingFailed` when the build is refused, fails or cannot be followed.
        """
        ...
