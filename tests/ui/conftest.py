"""Browser tests of the console: the real page, in Chromium, against a real server.

Each test gets its own server with an empty database. They are marked ``ui`` and left out
of the normal run; CI runs them in a separate job (``uv run pytest -m ui``).

Locally, ``uv run playwright install chromium`` once, or set UI_BROWSER_CHANNEL=chrome to
use the Chrome that is already installed.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from playwright.sync_api import Browser, Page, Playwright, sync_playwright

from app.config import Settings
from app.main import create_app


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/ui" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.ui)


@pytest.fixture
def live_server(tmp_path: Path) -> Iterator[str]:
    """The app on a free port with an empty database; yields its address."""
    app = create_app(
        Settings(
            database_url=f"sqlite+pysqlite:///{tmp_path / 'ui.sqlite3'}", auto_create_schema=True
        )
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("the test server did not start")
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)
    app.state.database.dispose()


@pytest.fixture(scope="session")
def playwright_instance() -> Iterator[Playwright]:
    with sync_playwright() as playwright:
        yield playwright


@pytest.fixture(scope="session")
def browser(playwright_instance: Playwright) -> Iterator[Browser]:
    channel = os.getenv("UI_BROWSER_CHANNEL") or None
    browser = playwright_instance.chromium.launch(channel=channel)
    yield browser
    browser.close()


@pytest.fixture
def page(browser: Browser, live_server: str) -> Iterator[Page]:
    """A desktop page; a script error on it fails the test."""
    context = browser.new_context(
        viewport={"width": 1280, "height": 800}, accept_downloads=True, color_scheme="dark"
    )
    page = context.new_page()
    page.set_default_timeout(8000)
    script_errors: list[str] = []
    page.on("pageerror", lambda error: script_errors.append(str(error)))
    yield page
    context.close()
    assert not script_errors, f"the page raised: {script_errors}"


@pytest.fixture
def phone(browser: Browser, live_server: str) -> Iterator[Page]:
    """A phone-sized page with touch."""
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, color_scheme="dark"
    )
    page = context.new_page()
    page.set_default_timeout(8000)
    yield page
    context.close()


@pytest.fixture
def api(live_server: str) -> Iterator[httpx.Client]:
    """The server's own API, to set a test up and to check what the page did."""
    with httpx.Client(base_url=live_server, timeout=15) as client:
        yield client
