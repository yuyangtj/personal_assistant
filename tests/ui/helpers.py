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
