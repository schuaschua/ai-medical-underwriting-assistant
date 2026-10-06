"""Helpers of the integration tests: the local PostgreSQL, seen as one role or another."""

from typing import Any

import psycopg

from workflow.settings import Settings


def connect(settings: Settings, **options: Any) -> psycopg.Connection[Any]:
    """A connection of the test's own, as the role the settings name."""
    return psycopg.connect(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=settings.database_user,
        **options,
    )


def as_service(settings: Settings) -> Settings:
    """The settings the service itself runs with: signed in as its own role."""
    if settings.database_service_role is None:
        raise ValueError("the settings name no service role")
    return settings.model_copy(
        update={
            "database_user": settings.database_service_role,
            "database_service_role": None,
        }
    )
