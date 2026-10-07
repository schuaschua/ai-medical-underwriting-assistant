"""Shared fixtures: a built-SPA stand-in on disk and an app wired to it."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web.adapters.http.app import create_app
from web.settings import Settings

INDEX_HTML = (
    '<!doctype html><html lang="en"><head><title>spa</title></head>'
    '<body><div id="root"></div><script type="module" src="/assets/app.js"></script>'
    "</body></html>"
)


@pytest.fixture
def index_html() -> str:
    return INDEX_HTML


@pytest.fixture
def spa_dir(tmp_path: Path) -> Path:
    root = tmp_path / "spa"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (root / "assets" / "app.js").write_text("export {};\n", encoding="utf-8")
    # Story 2.7: the PDF renderer's worker is built as a module file of its own.
    (root / "assets" / "pdf.worker.min.mjs").write_text("export {};\n", "utf-8")
    # A file beside the SPA folder that must never be served.
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")
    return root


@pytest.fixture
def scoreboards_dir(tmp_path: Path) -> Path:
    # Empty: the bake-off has not been run until a test writes a file here.
    folder = tmp_path / "scoreboards"
    folder.mkdir()
    return folder


@pytest.fixture
def settings(spa_dir: Path, scoreboards_dir: Path) -> Settings:
    return Settings(
        spa_dir=spa_dir,
        scoreboards_dir=scoreboards_dir,
        applicationinsights_connection_string=None,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
