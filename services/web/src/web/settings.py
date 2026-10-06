"""The one settings object of the `web` service (coding-style rule 12)."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The Dapr app id; also the service name telemetry is reported under.
APP_ID = "web"
# The probe target. It is kept out of traces: the platform calls it every few seconds.
HEALTH_PATH = "/api/health"

# services/web/spa/dist in a checkout; the container image sets WEB_SPA_DIR.
_DEFAULT_SPA_DIR = Path(__file__).resolve().parents[2] / "spa" / "dist"


class Settings(BaseSettings):
    """Read once from environment variables prefixed `WEB_`."""

    model_config = SettingsConfigDict(env_prefix="WEB_", extra="ignore", frozen=True)

    # Loopback by default; the container image sets WEB_HOST to listen on all interfaces.
    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 8000
    # Folder holding the built SPA (index.html and its assets).
    spa_dir: Path = _DEFAULT_SPA_DIR

    # Telemetry is exported only when a connection string is set. It is an address,
    # not a credential, but it is still kept out of logs and reprs.
    applicationinsights_connection_string: SecretStr | None = None
    # Share of requests traced (azure.md rule 16).
    otel_sampling_ratio: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0
    # Client id of the service's user-assigned identity; unset on a developer machine.
    azure_client_id: str | None = None

    @field_validator(
        "applicationinsights_connection_string", "azure_client_id", mode="before"
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # A variable that is present but empty or blank means "not configured".
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(text, str) and not text.strip():
            return None
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build the settings once per process."""
    return Settings()
