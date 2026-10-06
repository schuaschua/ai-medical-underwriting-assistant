"""Blob Storage adapter: the `originals` container (AD-21)."""

import asyncio

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient, ContentSettings
from opentelemetry import trace

from contracts.operations import PDF
from intake.adapters.credential import azure_credential
from intake.settings import APP_ID, Settings

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
            "Set INTAKE_BLOB_ACCOUNT_URL, or INTAKE_BLOB_CONNECTION_STRING for the "
            "local emulator."
        )
    return BlobServiceClient(
        settings.blob_account_url, credential=azure_credential(settings)
    )


class BlobOriginalStore:
    """Writes originals. It has no read method: nothing serves an original (AD-21)."""

    def __init__(self, service: BlobServiceClient, container: str) -> None:
        self._container = service.get_container_client(container)
        # Writes still running on a worker thread, by blob name.
        self._writing: dict[str, asyncio.Future[None]] = {}

    async def put(self, blob_name: str, content: bytes) -> None:
        """Store a new original; an existing blob of that name is an error."""
        with tracer.start_as_current_span("intake.blob.put_original"):
            # The SDK's client blocks, so it runs on a worker thread. A thread
            # cannot be cancelled: if the caller is, the write goes on, and
            # `delete` waits for it.
            write = asyncio.ensure_future(
                asyncio.to_thread(self._put, blob_name, content)
            )
            self._writing[blob_name] = write
            write.add_done_callback(lambda _: self._writing.pop(blob_name, None))
            await asyncio.shield(write)

    async def delete(self, blob_name: str) -> None:
        """Remove an original; one that is already gone is not an error."""
        with tracer.start_as_current_span("intake.blob.delete_original"):
            write = self._writing.get(blob_name)
            if write is not None:
                # Deleting first would let the write land afterwards.
                await asyncio.wait({write})
                if not write.cancelled():
                    # Its failure is the writer's to report, not this call's.
                    write.exception()
            await asyncio.to_thread(self._delete, blob_name)

    def _put(self, blob_name: str, content: bytes) -> None:
        self._container.upload_blob(
            blob_name,
            content,
            overwrite=False,
            content_settings=ContentSettings(content_type=PDF),
        )

    def _delete(self, blob_name: str) -> None:
        try:
            self._container.delete_blob(blob_name)
        except ResourceNotFoundError:
            return


def ensure_local_containers(settings: Settings) -> list[str]:
    """Create the service's containers in the local emulator; return their names.

    In Azure the `foundation` stack owns the containers, so this refuses to
    run unless the emulator's connection string is configured.
    """
    if settings.blob_connection_string is None:
        raise ValueError(
            "Containers are created only in the local emulator "
            "(INTAKE_BLOB_CONNECTION_STRING is not set)."
        )
    service = build_blob_service(settings)
    names = [settings.originals_container, settings.cases_container]
    for name in names:
        try:
            service.create_container(name)
        except ResourceExistsError:
            continue
    return names
