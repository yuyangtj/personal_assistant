from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from app.integrations.chat import (
    ChatClient,
    ChatCompletion,
    ChatMessage,
    ProviderError,
)


class ProviderChainError(ProviderError):
    """All configured providers failed without exposing provider response details."""


def _chain_label(clients: Sequence[ChatClient], attribute: str) -> str:
    return "->".join(str(getattr(client, attribute)) for client in clients)


class FallbackChatClient:
    """Tries chat providers in preference order for retryable provider failures."""

    def __init__(self, clients: Sequence[ChatClient]):
        if len(clients) < 2:
            raise ValueError("A fallback chat chain requires at least two clients")
        self.clients = tuple(clients)
        self.provider = _chain_label(self.clients, "provider")
        self.model = _chain_label(self.clients, "model")
        self.supports_tools = all(getattr(c, "supports_tools", False) for c in self.clients)
        self.streams = any(getattr(c, "streams", False) for c in self.clients)

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 700,
        tools: list[dict[str, Any]] | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> ChatCompletion:
        extra: dict[str, Any] = {"tools": tools} if tools else {}
        for client in self.clients:
            streamed = {"on_text": on_text} if on_text and getattr(client, "streams", False) else {}
            try:
                completion = client.complete(messages, max_tokens=max_tokens, **extra, **streamed)
                if completion.text.strip() or completion.tool_calls:
                    return completion
            except ProviderError:
                continue
        raise ProviderChainError(f"Chat provider chain exhausted: {self.provider}")

    def close(self) -> None:
        for client in self.clients:
            client.close()
