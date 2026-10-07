"""The service's database role on a developer machine.

In Azure the role is the one mapped to the service identity, and an operator
creates it (infra/bootstrap/README.md). Locally the PostgreSQL container has
only its superuser, which no grant can hold back, so the service gets a role
of its own here: the append-only rule on the agent's step log (AD-15) is then
enforced by the local database exactly as it is in Azure.
"""

import psycopg
from psycopg import sql

from verdict.settings import Settings


def ensure_local_service_role(settings: Settings) -> str:
    """Create the service's login role in the local PostgreSQL if it is missing.

    Refuses to run against Azure, and without a role name to create.
    """
    if settings.database_entra_auth:
        raise ValueError(
            "The service role is created here only for the local PostgreSQL "
            "(VERDICT_DATABASE_ENTRA_AUTH is set)."
        )
    role = settings.database_service_role
    if role is None:
        raise ValueError("Set VERDICT_DATABASE_SERVICE_ROLE to the role to create.")
    with psycopg.connect(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=settings.database_user,
        connect_timeout=settings.database_connect_timeout_seconds,
        autocommit=True,
    ) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
        ).fetchone()
        if exists is None:
            # No password: the container trusts every local connection, as it
            # does for its own user (compose.yaml). The name is quoted as an
            # identifier, never pasted in as text.
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role))
            )
    return role
