"""In-memory stand-ins for `intake`'s stores, with switches that make them misbehave.

Unit tests use them in place of PostgreSQL and Blob Storage (coding-style
rule 23). They live with the tests, on pytest's `pythonpath`, so the service
package and its image hold no test code.
"""

import asyncio
from dataclasses import dataclass, field

from intake.domain.entities import Case, Document
from intake.domain.ports import DuplicateUpload


class StoreDown(Exception):
    """What a failing stand-in raises. Its message must never reach a log or a body."""

    def __init__(self) -> None:
        super().__init__("secret-store-detail host=10.0.0.1")


async def _never() -> None:
    await asyncio.Event().wait()


@dataclass
class MemoryOriginalStore:
    blobs: dict[str, bytes] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    fail_put: bool = False
    fail_delete: bool = False
    # Never answers, like a storage call that hangs.
    hang_put: bool = False
    # Set once a call has started, so a test knows when to interrupt it.
    entered: asyncio.Event = field(default_factory=asyncio.Event)

    async def put(self, blob_name: str, content: bytes) -> None:
        self.entered.set()
        if self.fail_put:
            raise StoreDown
        if self.hang_put:
            await _never()
        self.blobs[blob_name] = content

    async def delete(self, blob_name: str) -> None:
        if self.fail_delete:
            raise StoreDown
        self.deleted.append(blob_name)
        self.blobs.pop(blob_name, None)


@dataclass
class MemoryCaseRepository:
    cases: list[Case] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    # The insert fails and nothing is stored.
    fail: bool = False
    # The rows are stored, then the call fails: a commit whose answer was lost.
    commit_then_fail: bool = False
    # The call never answers, before or after the rows are stored.
    hang: bool = False
    commit_then_hang: bool = False
    # The database cannot be asked whether a row exists.
    fail_exists: bool = False
    # The database cannot be asked for an earlier upload with the same key.
    fail_find: bool = False
    # One entry per lookup, first to last; "ok" or nothing means it answers.
    find_script: list[str] = field(default_factory=list)
    finds: int = 0
    # An upload with the same key is recorded by another call, just before
    # this one's insert: the race the unique rule settles.
    racing: tuple[Case, Document] | None = None
    entered: asyncio.Event = field(default_factory=asyncio.Event)

    async def add(self, case: Case, document: Document) -> None:
        self.entered.set()
        if self.fail:
            raise StoreDown
        if self.hang:
            await _never()
        if self.racing is not None:
            self.cases.append(self.racing[0])
            self.documents.append(self.racing[1])
            self.racing = None
        if document.idempotency_key is not None and any(
            item.idempotency_key == document.idempotency_key for item in self.documents
        ):
            # As the database's unique rule does: neither row is inserted.
            raise DuplicateUpload
        self.cases.append(case)
        self.documents.append(document)
        if self.commit_then_fail:
            raise StoreDown
        if self.commit_then_hang:
            await _never()

    async def find_by_idempotency_key(self, idempotency_key: str) -> Document | None:
        self.finds += 1
        # What this lookup does, if the test scripted it: "fail", "empty" or "hang".
        scripted = self.find_script.pop(0) if self.find_script else None
        if self.fail_find or scripted == "fail":
            raise StoreDown
        if scripted == "empty":
            return None
        if scripted == "hang":
            await _never()
        for item in self.documents:
            if item.idempotency_key == idempotency_key:
                return item
        return None

    async def document_exists(self, document_id: str) -> bool:
        if self.fail_exists:
            raise StoreDown
        return any(item.document_id == document_id for item in self.documents)


@dataclass
class MemorySchemaRevision:
    revision: str | None
    fail: bool = False

    async def current(self) -> str | None:
        if self.fail:
            raise StoreDown
        return self.revision
