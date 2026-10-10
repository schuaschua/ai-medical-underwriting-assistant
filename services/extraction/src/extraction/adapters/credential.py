"""The Entra credential every Azure call of the service signs in with."""

from functools import lru_cache
from typing import TYPE_CHECKING

from extraction.settings import Settings

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential


def azure_credential(settings: Settings) -> "TokenCredential":
    """The service identity in Azure, the developer's own sign-in elsewhere."""
    return _credential(settings.azure_client_id)


@lru_cache(maxsize=2)
def _credential(client_id: str | None) -> "TokenCredential":
    # One credential per process, so its token cache is shared by every caller.
    # Imported here so a local run against the emulators does not load the library.
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

    if client_id is not None:
        return ManagedIdentityCredential(client_id=client_id)
    return DefaultAzureCredential()
