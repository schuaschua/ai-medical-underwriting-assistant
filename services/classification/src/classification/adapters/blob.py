"""Blob Storage adapter: the `classifier-training` container (spine AD-4, story 4.2).

Only the training job uses it. It reads the one list that names the prepared
training pages and says what the container holds beside it; it reads
nothing of a case: the service has no role on the containers of `intake`.

The pages get there prepared: each was uploaded as a case and redacted by
the pipeline, and the tool that fetched the redacted files wrote the list
(`redacted-pages.json`) beside them, with the MD5 of every file. The job
trains only when the container holds exactly those files with that content,
so neither a folder of unredacted pages put there by mistake nor an
unredacted page under a prepared page's name trains anything.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient, ContainerClient, ContentSettings
from opentelemetry import trace
from pydantic import BaseModel, StringConstraints, ValidationError

from classification.adapters.credential import azure_credential
from classification.adapters.telemetry import adapter_span
from classification.domain.entities import ListedPage, StoredBlob
from classification.domain.ports import TrainingFailed
from classification.domain.training import ocr_file_of
from classification.settings import APP_ID, LOOPBACK_HOSTS, Settings

tracer = trace.get_tracer(APP_ID)

# The list of the prepared pages, written by the tool that prepared them.
MANIFEST_BLOB_NAME = "redacted-pages.json"
PDF_SUFFIX = ".pdf"
# The account of the blob emulator of compose.yaml.
EMULATOR_ACCOUNT = "devstoreaccount1"
_PDF = "application/pdf"
_JSON = "application/json"


class _ListedPage(BaseModel):
    """One entry of the list: the blob's name, its label and what vouches for it."""

    file: str
    page_type: str
    # The case the page was redacted as: without one the page did not pass
    # the pipeline.
    case_id: Annotated[str, StringConstraints(pattern=r"\S")]
    # The hex MD5 of the redacted file, as the tool wrote it.
    md5: Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F]{32}$")]
    # The hex MD5 of the page's layout result, where one was made.
    ocr_md5: Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F]{32}$")] | None = (
        None
    )


class _Manifest(BaseModel):
    pages: list[_ListedPage]


def content_md5(content: bytes) -> str:
    """The hex MD5 of a file: what Blob Storage keeps of a blob's content too.

    A check against a wrong file in the right place, not against an
    attacker: whoever can write the container can write its list.
    """
    return hashlib.md5(content, usedforsecurity=False).hexdigest()


def build_blob_service(settings: Settings) -> BlobServiceClient:
    """The storage client. Building it makes no network call."""
    if settings.blob_connection_string is not None:
        # The local emulator; Azure has key access off and takes the other branch.
        return BlobServiceClient.from_connection_string(
            settings.blob_connection_string.get_secret_value()
        )
    if settings.blob_account_url is None:
        raise ValueError(
            "Set CLASSIFICATION_BLOB_ACCOUNT_URL, or "
            "CLASSIFICATION_BLOB_CONNECTION_STRING for the local emulator."
        )
    return BlobServiceClient(
        settings.blob_account_url, credential=azure_credential(settings)
    )


def listed_of(manifest: bytes) -> list[ListedPage]:
    """The pages the list names, as it names them. A list in another shape is refused."""
    try:
        listed = _Manifest.model_validate_json(manifest)
    except ValidationError:
        # Not raised from the validation error: that one repeats the file.
        raise TrainingFailed("page_list_malformed") from None
    return [
        ListedPage(
            file=page.file,
            page_type=page.page_type,
            md5=page.md5.lower(),
            ocr_md5=page.ocr_md5.lower() if page.ocr_md5 else None,
        )
        for page in listed.pages
    ]


class BlobTrainingPages:
    """Says what the one container it was built for holds."""

    def __init__(self, service: BlobServiceClient, container: str) -> None:
        self._container: ContainerClient = service.get_container_client(container)

    def container_url(self) -> str:
        """The container's address, without a key or a signature."""
        return str(self._container.url)

    async def contents(self) -> tuple[list[ListedPage], list[StoredBlob]]:
        with adapter_span(tracer, "classification.blob.read_training_container"):
            # The SDK's client blocks, so it runs on a worker thread.
            return await asyncio.to_thread(self._contents)

    def _contents(self) -> tuple[list[ListedPage], list[StoredBlob]]:
        try:
            found = {
                str(blob.name): blob.content_settings.content_md5
                for blob in self._container.list_blobs()
            }
        except ResourceNotFoundError:
            raise TrainingFailed("training_container_missing") from None
        # No list: no page is vouched for, and the job says so.
        listed: list[ListedPage] = []
        if MANIFEST_BLOB_NAME in found:
            del found[MANIFEST_BLOB_NAME]
            listed = listed_of(self._read(MANIFEST_BLOB_NAME))
        named = {page.file for page in listed} | {
            ocr_file_of(page) for page in listed if page.ocr_md5
        }
        return listed, [
            StoredBlob(
                name=name,
                md5=self._md5_of(name, kept) if name in named else None,
            )
            for name, kept in sorted(found.items())
        ]

    def _read(self, name: str) -> bytes:
        try:
            return bytes(self._container.download_blob(name).readall())
        except ResourceNotFoundError:
            raise TrainingFailed("training_blob_gone") from None

    def _md5_of(self, name: str, kept: bytes | bytearray | None) -> str:
        """The MD5 of a blob's content: the one storage keeps for it, or else worked out from the blob."""
        if kept:
            return bytes(kept).hex()
        return content_md5(self._read(name))


def is_local_emulator(service: BlobServiceClient) -> bool:
    """Whether a storage client is that of the blob emulator on this machine."""
    host = urlsplit(str(service.url)).hostname or ""
    return host in LOOPBACK_HOSTS and service.account_name == EMULATOR_ACCOUNT


def upload_local_training_pages(settings: Settings, folder: Path) -> int:
    """Put a folder of prepared training pages into the local emulator's container; how many files.

    What the container held before is removed: it then holds this folder
    and nothing else. That is why this works on the blob emulator of this
    machine only, whatever account a connection string names. In Azure the
    `foundation` stack owns the container and an operator uploads the folder.
    """
    if settings.blob_connection_string is None:
        raise ValueError(
            "The training pages are uploaded this way only to the local emulator "
            "(CLASSIFICATION_BLOB_CONNECTION_STRING is not set)."
        )
    service = build_blob_service(settings)
    if not is_local_emulator(service):
        raise ValueError(
            "The training pages are uploaded this way only to the blob emulator "
            "on this machine: CLASSIFICATION_BLOB_CONNECTION_STRING names "
            "another account."
        )
    container = service.get_container_client(settings.training_container)
    try:
        container.create_container()
    except ResourceExistsError:
        for name in list(container.list_blob_names()):
            container.delete_blob(name)
    files = sorted(path for path in folder.rglob("*") if path.is_file())
    for path in files:
        content = path.read_bytes()
        container.upload_blob(
            path.relative_to(folder).as_posix(),
            content,
            overwrite=True,
            content_settings=ContentSettings(
                content_type=_PDF if path.suffix.lower() == PDF_SUFFIX else _JSON,
                # Kept by storage with the blob, as an upload tool sets it.
                content_md5=bytearray.fromhex(content_md5(content)),
            ),
        )
    return len(files)
