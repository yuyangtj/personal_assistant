from __future__ import annotations

from collections.abc import Sequence

from app.integrations.chat import (
    ChatClient,
    ChatCompletion,
    ChatMessage,
    ProviderError,
)
from app.manager.model.contracts import (
    ManagerModelClient,
    ManagerModelRequest,
    ModelInvocation,
)


class ProviderChainError(ProviderError):
    """All configured providers failed without exposing provider response details."""


def _chain_label(clients: Sequence[ChatClient | ManagerModelClient], attribute: str) -> str:
    return "->".join(str(getattr(client, attribute)) for client in clients)


class FallbackChatClient:
    """Tries chat providers in preference order for retryable provider failures."""

    def __init__(self, clients: Sequence[ChatClient]):
        if len(clients) < 2:
            raise ValueError("A fallback chat chain requires at least two clients")
        self.clients = tuple(clients)
        self.provider = _chain_label(self.clients, "provider")
        self.model = _chain_label(self.clients, "model")

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 700,
    ) -> ChatCompletion:
        for client in self.clients:
            try:
                completion = client.complete(messages, max_tokens=max_tokens)
                if completion.text.strip():
                    return completion
            except ProviderError:
                continue
        raise ProviderChainError(f"Chat provider chain exhausted: {self.provider}")

    def close(self) -> None:
        for client in self.clients:
            client.close()


class FallbackManagerModelClient:
    """Tries manager-model providers in preference order for provider failures."""

    def __init__(self, clients: Sequence[ManagerModelClient]):
        if len(clients) < 2:
            raise ValueError("A fallback manager chain requires at least two clients")
        self.clients = tuple(clients)
        self.provider = _chain_label(self.clients, "provider")
        self.model = _chain_label(self.clients, "model")

    def generate(
        self,
        request: ManagerModelRequest,
        *,
        timeout_seconds: float,
    ) -> ModelInvocation:
        for client in self.clients:
            try:
                return client.generate(request, timeout_seconds=timeout_seconds)
            except ProviderError:
                continue
        raise ProviderChainError(f"Manager provider chain exhausted: {self.provider}")

    def close(self) -> None:
        for client in self.clients:
            close = getattr(client, "close", None)
            if close is not None:
                close()
