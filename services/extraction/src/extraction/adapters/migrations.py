"""The Alembic environment bundled with the service, and its head revision."""

from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from extraction.settings import SCHEMA, Settings

# services/extraction/src/extraction/migrations: inside the package, so it ships in the image.
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
SETTINGS_ATTRIBUTE = "settings"


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    """Limit comparison with the database to schema `extraction` (spine AD-4).

    The database holds one schema per service; the others are not this
    service's to describe, and are never offered for dropping.
    """
    if type_ == "schema":
        return name == SCHEMA
    return True


def alembic_config(settings: Settings | None = None) -> Config:
    """The Alembic configuration; `settings` picks the database, else the environment does."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    if settings is not None:
        config.attributes[SETTINGS_ATTRIBUTE] = settings
    return config


@lru_cache(maxsize=1)
def bundled_head() -> str:
    """The newest revision among the migrations shipped with this build."""
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:
        raise RuntimeError("the service ships no migration")
    return head
