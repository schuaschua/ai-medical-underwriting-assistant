"""The Alembic environment bundled with the service, its head revision, and the grants.

The migrations run as the role that owns schema `verdict`. The service runs
as another role, and each migration grants that role exactly the rights it
needs on the tables it creates: there are no default privileges, so a table
is never writable by accident. On `verdict.agent_step`, the agent's step
log, the service role gets SELECT and INSERT only (AD-15).
"""

from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

import sqlalchemy as sa
from alembic import context, op
from alembic.config import Config
from alembic.script import ScriptDirectory

from verdict.settings import SCHEMA, Settings, get_settings

# services/verdict/src/verdict/migrations: inside the package, so it ships in the image.
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
SETTINGS_ATTRIBUTE = "settings"

NO_SERVICE_ROLE_MESSAGE = (
    "Set VERDICT_DATABASE_SERVICE_ROLE to the database role the service runs "
    "as: the migrations grant it its rights."
)


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    """Limit comparison with the database to schema `verdict` (spine AD-4).

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


def migration_settings() -> Settings:
    """The settings of the migration run under way."""
    # Tests hand their own settings in; the command line uses the environment.
    settings: Settings = (
        context.config.attributes.get(SETTINGS_ATTRIBUTE) or get_settings()
    )
    return settings


def service_role() -> str:
    """The database role the service runs as; a migration run must name it."""
    role = migration_settings().database_service_role
    if role is None:
        raise RuntimeError(NO_SERVICE_ROLE_MESSAGE)
    return role


STEP_LOG_HAS_ROWS_MESSAGE = (
    "verdict.agent_step holds the agent's steps: this downgrade would remove "
    "them, and is refused."
)


def refuse_if_step_log_has_rows() -> None:
    """Stop a downgrade that would drop a step log with steps in it."""
    held = op.get_bind().execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM verdict.agent_step)")
    )
    if held.scalar_one():
        raise RuntimeError(STEP_LOG_HAS_ROWS_MESSAGE)


def _quoted(identifier: str) -> str:
    # A GRANT takes no bound parameters. The names are quoted as identifiers
    # by the database dialect, so no value is ever read as SQL (security rule 21).
    return str(op.get_bind().dialect.identifier_preparer.quote_identifier(identifier))


def grant_schema_usage() -> None:
    """Let the service role use the schema. It may not create anything in it."""
    op.execute(
        sa.text(f"GRANT USAGE ON SCHEMA {_quoted(SCHEMA)} TO {_quoted(service_role())}")
    )


def revoke_schema_usage() -> None:
    op.execute(
        sa.text(
            f"REVOKE USAGE ON SCHEMA {_quoted(SCHEMA)} FROM {_quoted(service_role())}"
        )
    )


def grant_on_table(table: str, privileges: Sequence[str]) -> None:
    """Grant the service role the named rights on one table of the schema, and no others."""
    allowed = {"SELECT", "INSERT", "UPDATE", "DELETE"}
    unknown = set(privileges) - allowed
    if unknown or not privileges:
        raise ValueError(f"privileges must be among {sorted(allowed)}")
    op.execute(
        sa.text(
            f"GRANT {', '.join(privileges)} ON TABLE "
            f"{_quoted(SCHEMA)}.{_quoted(table)} TO {_quoted(service_role())}"
        )
    )
