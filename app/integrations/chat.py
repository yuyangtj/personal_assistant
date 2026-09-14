from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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
