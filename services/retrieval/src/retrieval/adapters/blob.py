"""Blob Storage adapter: the `manual` container, which holds the manual PDF (AD-4)."""

import asyncio
from pathlib import Path

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient, ContentSettings
from opentelemetry import trace

from contracts.operations import PDF
from retrieval.adapters.credential import azure_credential
from retrieval.adapters.telemetry import adapter_span
from retrieval.domain.ports import ManualMissing
from retrieval.settings import APP_ID, Settings

tracer = trace.get_tracer(APP_ID)


def build_blob_service(settings: Settings) -> BlobServiceClient:
    """The storage client. Building it makes no network call."""
    if settings.blob_connection_string is not None:
        # The local emulator; Azure has key access off and takes the other branch.
        return BlobServiceClient.from_connection_string(
            settings.blob_connection_string.get_secret_value()
        )
    if settings.blob_account_url is None:
        raise ValueError(
            "Set RETRIEVAL_BLOB_ACCOUNT_URL, or RETRIEVAL_BLOB_CONNECTION_STRING for "
            "the local emulator."
        )
    return BlobServiceClient(
        settings.blob_account_url, credential=azure_credential(settings)
    )


class BlobManualStore:
    """Reads the one manual. It is built for that one blob and can name no other."""

    def __init__(
        self, service: BlobServiceClient, container: str, blob_name: str
    ) -> None:
        self._blob = service.get_blob_client(container, blob_name)

    async def read(self) -> bytes:
        with adapter_span(tracer, "retrieval.blob.read_manual"):
            # The SDK's client blocks, so it runs on a worker thread.
            return await asyncio.to_thread(self._read)

    def _read(self) -> bytes:
        try:
            return bytes(self._blob.download_blob().readall())
        except ResourceNotFoundError:
            # The blob or its container: either way the manual is not there.
            raise ManualMissing from None


def upload_local_manual(settings: Settings, pdf: Path) -> str:
    """Put a manual PDF into the local emulator's `manual` container; return the blob's name.

    In Azure the `foundation` stack owns the container and an operator
    uploads the manual, so this refuses to run unless the emulator's
    connection string is configured.
    """
    if settings.blob_connection_string is None:
        raise ValueError(
            "The manual is uploaded this way only to the local emulator "
            "(RETRIEVAL_BLOB_CONNECTION_STRING is not set)."
        )
    service = build_blob_service(settings)
    try:
        service.create_container(settings.manual_container)
    except ResourceExistsError:
        pass
    service.get_blob_client(
        settings.manual_container, settings.manual_blob_name
    ).upload_blob(
        pdf.read_bytes(),
        overwrite=True,
        content_settings=ContentSettings(content_type=PDF),
    )
    return settings.manual_blob_name
