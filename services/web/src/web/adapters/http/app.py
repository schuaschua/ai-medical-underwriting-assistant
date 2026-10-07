"""The app factory: one FastAPI app serving `/api/*` and the SPA from one origin."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from web.adapters.dapr import ServiceClient, build_http_client
from web.adapters.http.api import SERVICES_STATE, TRIAGE_STATE
from web.adapters.http.api import router as api_router
from web.adapters.http.errors import API_ROUTES_STATE, install_error_handlers
from web.adapters.http.middleware import (
    SecurityHeadersMiddleware,
    UnhandledErrorMiddleware,
)
from web.adapters.http.spa import build_spa_router
from web.adapters.http.triage import TriageReader
from web.adapters.telemetry import configure_telemetry, instrument_app
from web.settings import Settings, get_settings


def create_app(
    settings: Settings | None = None,
    *,
    sidecar: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build the app. Tests pass their own settings; the server uses the environment.

    `sidecar` stands in for the Dapr sidecar in tests; without it, calls to
    other services go to the real one on loopback (AD-3).
    """
    if settings is None:
        settings = get_settings()
    telemetry_on = configure_telemetry(settings)
    services = ServiceClient(build_http_client(settings, sidecar), settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await services.aclose()

    # The interactive API pages load scripts from another site, which the
    # content security policy forbids, so they are off.
    app = FastAPI(
        title="web", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    setattr(app.state, SERVICES_STATE, services)
    setattr(
        app.state,
        TRIAGE_STATE,
        TriageReader(
            services,
            max_concurrent_reads=settings.triage_max_concurrent_reads,
            # The whole queue is answered within the deadline of one call
            # to another service, so the browser hears the server's answer.
            deadline_seconds=settings.lifecycle_timeout_seconds,
        ),
    )
    install_error_handlers(app)
    # The middleware added last runs first: headers wrap the error fallback.
    # AD-19: no CORS middleware is added, so no cross-origin call is allowed.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(api_router)
    # Taken before the SPA is added: these are the API's routes and no others.
    setattr(app.state, API_ROUTES_STATE, list(app.router.routes))
    # Last: its catch-all route would otherwise shadow the API.
    app.include_router(build_spa_router(settings.spa_dir))
    if telemetry_on:
        instrument_app(app)
    return app
