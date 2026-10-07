"""What an extraction needs from the outside world; adapters provide it."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from contracts.models.extraction import Fact
from extraction.domain.entities import FactSetKey, KeyRow, ModelAnswer, PageReading


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


class FactModel(Protocol):
    """The shared chat deployment, asked for the medical facts of one page (AD-14, AD-16)."""

    async def extract(self, page_text: str) -> ModelAnswer:
        """Run the model once on the page's text; return its answer as it gave it, and why it stopped.

        The answer is not looked at here: the caller parses it, and an answer
        that is not what was asked for is its to refuse. Raises
        `ModelUnavailable` when the model could not be had, and
        `ModelCallFailed` when it refused the call.
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
    ) -> PageReading | None:
        """The page's number and stored text; None if `intake` does not hold the page."""
        ...


class FactRepository(Protocol):
    """The key row of a fact set and the facts a finished one stores (AD-6)."""

    async def find(self, key: FactSetKey) -> KeyRow | None:
        """The key row of an earlier command with this key, if there is one."""
        ...

    async def begin(
        self, fact_set_id: str, key: FactSetKey, started_at: datetime
    ) -> KeyRow | None:
        """Insert the key row as running, before any work.

        Returns None when this call inserted it; otherwise the row that was
        there already, and nothing is changed. Never None unless the row is
        this call's own: if the row in the way is released meanwhile, the
        insert is tried again.
        """
        ...

    async def take_over(self, row: KeyRow, started_at: datetime) -> bool:
        """Make a key row that was left running this call's own, as begun at `started_at`.

        Only if the row is still running and still as `row` says it was
        begun: of several calls that try at once, one is answered True. The
        row keeps its id.
        """
        ...

    async def finish(
        self, fact_set_id: str, result_json: str, facts: Sequence[Fact] | None
    ) -> str:
        """Store the result, and the facts of a done one, in one transaction.

        `facts` is None for a failed result, and may be empty for a done
        one: a page with nothing medical. Nothing is written unless the key
        row is still running. Returns the result the row holds afterwards:
        this one, or the one stored before it.
        """
        ...

    async def release(self, fact_set_id: str) -> None:
        """Remove the key row if it is still running, so the command can be run again.

        A row that holds a result is left as it is.
        """
        ...

    async def of_case(self, case_id: str) -> list[Fact]:
        """The case's stored facts: by page number, then in the order they were stored."""
        ...
