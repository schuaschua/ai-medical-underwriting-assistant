"""What the upload needs from the outside world; adapters provide it."""

from typing import Protocol

from intake.domain.entities import Case, Document


class OriginalStore(Protocol):
    """The `originals` container (AD-21). It has no read: nothing serves an original."""

    async def put(self, blob_name: str, content: bytes) -> None:
        """Store a new original; fail rather than overwrite an existing one."""
        ...

    async def delete(self, blob_name: str) -> None:
        """Remove an original that no record points to.

        A write of that blob that is still under way is waited for first, so
        the blob is gone when this returns.
        """
        ...


class DuplicateUpload(Exception):
    """The upload's idempotency key is already recorded: another call got there first."""


class CaseRepository(Protocol):
    async def add(self, case: Case, document: Document) -> None:
        """Insert both rows, or neither.

        Raises `DuplicateUpload`, with neither row inserted, if a document
        with the same idempotency key exists.
        """
        ...

    async def find_by_idempotency_key(self, idempotency_key: str) -> Document | None:
        """The document an earlier upload with this key recorded, if there is one."""
        ...

    async def document_exists(self, document_id: str) -> bool:
        """Whether the document's row is in the database."""
        ...
