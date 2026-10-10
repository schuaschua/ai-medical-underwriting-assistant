"""Alembic environment of `intake`: one schema, its own version table (AD-4)."""

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema

from intake.adapters.db import (
    VERSION_TABLE,
    database_url,
    entra_token_for,
    metadata,
    use_entra_token,
)
from intake.adapters.migrations import SETTINGS_ATTRIBUTE, include_name
from intake.settings import SCHEMA, Settings, get_settings


def run_migrations() -> None:
    # Tests hand their own settings in; the command line uses the environment.
    settings: Settings = (
        context.config.attributes.get(SETTINGS_ATTRIBUTE) or get_settings()
    )
    engine = create_engine(database_url(settings), poolclass=NullPool)
    if settings.database_entra_auth:
        use_entra_token(engine, entra_token_for(settings))
    try:
        with engine.connect() as connection:
            # The version table lives in the schema, so the schema comes first.
            connection.execute(CreateSchema(SCHEMA, if_not_exists=True))
            connection.commit()
            context.configure(
                connection=connection,
                target_metadata=metadata,
                version_table=VERSION_TABLE,
                version_table_schema=SCHEMA,
                include_schemas=True,
                include_name=include_name,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise SystemExit(
        "intake migrations run against a database; --sql mode is not set up."
    )
run_migrations()
