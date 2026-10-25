"""Small helpers shared by the browser tests."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx


def eventually(check: Callable[[], Any], *, timeout: float = 6.0) -> Any:
    """The first truthy result of ``check``, retried until the timeout (for what the server
    saves a moment after the page acted); fails the test with the last result otherwise."""
    deadline = time.monotonic() + timeout
    while True:
        result = check()
        if result or time.monotonic() > deadline:
            assert result, f"never became true; last result: {result!r}"
            return result
        time.sleep(0.1)


def notes(api: httpx.Client, *, archived: bool = True) -> list[dict]:
    return api.get("/notes", params={"include_archived": archived}).json()["notes"]


def add_note(api: httpx.Client, **fields: Any) -> dict:
    response = api.post("/notes", json=fields)
    assert response.status_code == 201, response.text
    return response.json()


def in_more(page: Any, selector: str) -> None:
    """Click something in the More sheet (Stats, Avatar, Theme), opening it first."""
    if page.locator("#more-sheet").is_hidden():
        page.click("#more-open")
    page.click(selector)
    page.keyboard.press("Escape")  # the switches leave the sheet open


def pick_repository(page: Any, value: str) -> None:
    """Choose the coding repo in the conversation's ⋯ menu."""
    menu = page.locator("details.chat-menu")
    if menu.get_attribute("open") is None:
        menu.locator("summary").click()
    page.select_option("#repository", value)


def in_chat_menu(page: Any, selector: str) -> None:
    """Click something in the open conversation's ⋯ menu, then close the menu."""
    menu = page.locator("details.chat-menu")
    if menu.get_attribute("open") is None:
        menu.locator("summary").click()
    page.click(selector)
    page.evaluate("() => document.querySelector('details.chat-menu').open = false")
