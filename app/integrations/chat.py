from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

#: Longest a model API may take to accept a connection.
CONNECT_SECONDS = 5.0


class ProviderError(RuntimeError):
    """A sanitized provider failure that is safe to use for failover decisions."""


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ChatCompletion:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider: str | None = None


class ChatClient(Protocol):
    provider: str
    model: str

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 700,
    ) -> ChatCompletion: ...

    def close(self) -> None: ...


def post_within(
    client: httpx.Client, path: str, *, json: Any, deadline_seconds: float, **kwargs: Any
) -> httpx.Response:
    """POST and read the whole response, giving up after ``deadline_seconds`` in total.

    httpx's timeout limits each connect and each read, so a server that keeps sending a
    trickle of bytes can hold a request open for as long as it likes (a chat reply once
    hung for over 17 minutes). This caps the whole exchange; going over raises
    ``httpx.ReadTimeout`` like any other timeout.
    """
    deadline = time.monotonic() + deadline_seconds
    body = bytearray()
    with client.stream("POST", path, json=json, **kwargs) as response:
        for chunk in response.iter_bytes():
            body += chunk
            if time.monotonic() > deadline:
                raise httpx.ReadTimeout(
                    f"no complete response within {deadline_seconds:g} s", request=response.request
                )
    # Already decoded, so no headers: they would make httpx decode the body again.
    return httpx.Response(response.status_code, content=bytes(body), request=response.request)
