"""The app factory: one FastAPI app serving `/api/*` and the SPA from one origin."""

from fastapi import FastAPI

from web.adapters.http.api import router as api_router
from web.adapters.http.errors import install_error_handlers
from web.adapters.http.middleware import (
    SecurityHeadersMiddleware,
    UnhandledErrorMiddleware,
)
from web.adapters.http.spa import build_spa_router
from web.adapters.telemetry import configure_telemetry, instrument_app
from web.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings; the server uses the environment."""
    if settings is None:
        settings = get_settings()
    telemetry_on = configure_telemetry(settings)

    # The interactive API pages load scripts from another site, which the
    # content security policy forbids, so they are off.
    app = FastAPI(title="web", docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)
    # The middleware added last runs first: headers wrap the error fallback.
    # AD-19: no CORS middleware is added, so no cross-origin call is allowed.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(api_router)
    # Last: its catch-all route would otherwise shadow the API.
    app.include_router(build_spa_router(settings.spa_dir))
    if telemetry_on:
        instrument_app(app)
    return app
