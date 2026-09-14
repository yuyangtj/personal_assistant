from __future__ import annotations

import pytest

from app.capabilities import CapabilityRegistry
from app.integrations.chat import ChatCompletion, ChatMessage, ProviderError
from app.integrations.fallback import (
    FallbackChatClient,
    FallbackManagerModelClient,
    ProviderChainError,
)
from app.manager.model import ModelInvocation, ValidatedManagerModelAdapter
from app.persistence.models import TaskModel


class StubChatClient:
    def __init__(self, provider: str, result: ChatCompletion | Exception):
        self.provider = provider
        self.model = f"{provider}-chat"
        self.result = result
        self.calls = 0
        self.closed = False

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 700,
    ) -> ChatCompletion:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def close(self) -> None:
        self.closed = True


class StubManagerClient:
    def __init__(self, provider: str, result: ModelInvocation | Exception):
        self.provider = provider
        self.model = f"{provider}-manager"
        self.result = result
        self.calls = 0
        self.closed = False

    def generate(self, request, *, timeout_seconds: float) -> ModelInvocation:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def close(self) -> None:
        self.closed = True


def _analysis() -> dict[str, object]:
    return {
        "goal": "Answer the user",
        "required_capabilities": ["conversation"],
        "constraints": [],
        "risk_signals": [],
        "confidence": 0.95,
    }


def _task() -> TaskModel:
    return TaskModel(
        id="fallback-task",
        original_request="Hello",
        current_goal="Hello",
        status="planning",
        required_capabilities=[],
        source_context={},
    )


def test_chat_chain_uses_fallback_and_preserves_actual_provider() -> None:
    primary = StubChatClient("kimi", ProviderError("sanitized failure"))
    fallback = StubChatClient(
        "minimax",
        ChatCompletion("Hello", "MiniMax-M3", 12, 4, "minimax"),
    )
    chain = FallbackChatClient([primary, fallback])

    result = chain.complete([ChatMessage("user", "Hi")])

    assert result.provider == "minimax"
    assert primary.calls == fallback.calls == 1
    chain.close()
    assert primary.closed is True
    assert fallback.closed is True


def test_chat_chain_does_not_hide_programming_errors() -> None:
    chain = FallbackChatClient(
        [
            StubChatClient("kimi", ValueError("bug")),
            StubChatClient("minimax", ChatCompletion("unused", "model")),
        ]
    )

    with pytest.raises(ValueError, match="bug"):
        chain.complete([ChatMessage("user", "Hi")])


def test_chat_chain_reports_sanitized_exhaustion() -> None:
    chain = FallbackChatClient(
        [
            StubChatClient("kimi", ProviderError("secret one")),
            StubChatClient("minimax", ProviderError("secret two")),
        ]
    )

    with pytest.raises(ProviderChainError) as captured:
        chain.complete([ChatMessage("user", "Hi")])

    assert str(captured.value) == "Chat provider chain exhausted: kimi->minimax"
    assert "secret" not in str(captured.value)


def test_manager_chain_uses_fallback_and_records_actual_provider() -> None:
    primary = StubManagerClient("kimi", ProviderError("sanitized failure"))
    fallback = StubManagerClient(
        "minimax",
        ModelInvocation(
            raw_output=_analysis(),
            input_tokens=22,
            output_tokens=8,
            provider="minimax",
            model="MiniMax-M3",
        ),
    )
    chain = FallbackManagerModelClient([primary, fallback])
    registry = CapabilityRegistry.from_directory("capabilities")

    result = ValidatedManagerModelAdapter(chain).analyze(_task(), registry.list())

    assert result.provider == "minimax"
    assert result.model == "MiniMax-M3"
    assert primary.calls == fallback.calls == 1
    chain.close()
    assert primary.closed is True
    assert fallback.closed is True
