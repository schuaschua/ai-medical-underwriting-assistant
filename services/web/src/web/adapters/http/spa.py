"""Serves the built SPA, and its entry page for every client-side route (AD-19)."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from contracts.errors import DomainError, ErrorCode
from web.adapters.http.errors import NOT_FOUND_MESSAGE, is_api_path

INDEX_FILE = "index.html"
# The build puts every file it names by content hash in this folder.
ASSETS_PREFIX = "assets/"
# The entry page is re-checked on every visit so a new deploy is picked up...
INDEX_CACHE_CONTROL = "no-cache"
# ...while a hashed file never changes, so the browser may keep it for a year.
ASSETS_CACHE_CONTROL = "public, max-age=31536000, immutable"


def _not_found() -> DomainError:
    return DomainError(ErrorCode.NOT_FOUND, NOT_FOUND_MESSAGE)


def build_spa_router(spa_dir: Path) -> APIRouter:
    """Build the catch-all router for one SPA folder. Include it after the API router."""
    root = spa_dir.resolve()
    router = APIRouter()

    @router.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if is_api_path(f"/{path}"):
            # An unknown `/api` path is an API error, never the SPA's HTML.
            raise _not_found()
        try:
            candidate = (root / path).resolve()
            # A path that climbs out of the SPA folder is never served.
            is_file = candidate.is_relative_to(root) and candidate.is_file()
        except (OSError, ValueError):
            # A path the file system cannot even look up: a null byte, a name too long.
            raise _not_found() from None
        if is_file and candidate.name != INDEX_FILE:
            if path.startswith(ASSETS_PREFIX):
                return FileResponse(
                    candidate, headers={"Cache-Control": ASSETS_CACHE_CONTROL}
                )
            return FileResponse(candidate)
        if path.startswith(ASSETS_PREFIX):
            # A missing file, not a client route: a script must not get HTML back.
            raise _not_found()
        return _index(root)

    return router


def _index(root: Path) -> FileResponse:
    index = root / INDEX_FILE
    if not index.is_file():
        # The SPA has not been built into this folder.
        raise _not_found()
    return FileResponse(index, headers={"Cache-Control": INDEX_CACHE_CONTROL})
