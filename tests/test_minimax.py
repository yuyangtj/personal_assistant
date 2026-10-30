from __future__ import annotations

import json
import time

import httpx
import pytest

from app.integrations.chat import ChatCompletion, ChatMessage, ProviderError
from app.integrations.openai_compat import MINIMAX, OpenAICompatibleChatClient
from app.persistence.models import TaskModel


def _task() -> TaskModel:
    return TaskModel(
        id="minimax-test",
        original_request="Say hello briefly",
        current_goal="Say hello briefly",
        status="planning",
        required_capabilities=[],
        source_context={},
    )


def _analysis() -> dict:
    return {
        "goal": "Say hello briefly",
        "required_capabilities": ["conversation"],
        "constraints": ["Use one sentence"],
        "risk_signals": [],
        "confidence": 0.96,
    }


def test_minimax_chat_client_normalizes_thinking_and_reads_usage() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "MiniMax-M3",
                "choices": [
                    {
                        "message": {
                            "content": (
                                "<think>private reasoning</think>\n"
                                '{"reply":"Hello!","emotion":"Warm","action":null}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 27, "completion_tokens": 11},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    client = OpenAICompatibleChatClient(
        MINIMAX,
        api_key="sk-test",
        model="MiniMax-M3",
        transport=httpx.MockTransport(handler),
    )

    completion = client.complete([ChatMessage("user", "Hi")], max_tokens=80)

    assert completion == ChatCompletion(
        '{"reply":"Hello!","emotion":"Warm","action":null}',
        "MiniMax-M3",
        27,
        11,
        "minimax",
    )
    assert seen["path"] == "/v1/chat/completions"
    assert seen["authorization"] == "Bearer sk-test"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["messages"] == [{"role": "user", "content": "Hi"}]
    assert body["max_completion_tokens"] == 80
    assert body["reasoning_split"] is True
    client.close()


def test_minimax_chat_error_is_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "base_resp": {
                    "status_code": 1004,
                    "status_msg": "bad key sk-minimax-secret",
                }
            },
        )

    client = OpenAICompatibleChatClient(
        MINIMAX,
        api_key="sk-minimax-secret",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderError) as captured:
        client.complete([ChatMessage("user", "Hi")])

    assert str(captured.value) == "MiniMax returned a provider error"
    assert "sk-minimax-secret" not in str(captured.value)
    client.close()


class _Trickle(httpx.SyncByteStream):
    """A server that keeps sending a byte now and then and never finishes."""

    def __iter__(self):
        while True:
            time.sleep(0.02)
            yield b" "


def test_a_reply_that_never_finishes_fails_at_the_total_deadline() -> None:
    client = OpenAICompatibleChatClient(
        MINIMAX,
        api_key="k",
        timeout_seconds=0.2,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=_Trickle())),
    )

    started = time.monotonic()
    with pytest.raises(ProviderError, match="ReadTimeout"):
        client.complete([ChatMessage("user", "hi")])

    assert time.monotonic() - started < 2


def test_a_complete_reply_is_read_as_before() -> None:
    body = {"choices": [{"message": {"content": "Hello"}}], "usage": {"prompt_tokens": 3}}
    client = OpenAICompatibleChatClient(
        MINIMAX,
        api_key="k",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
    )

    assert client.complete([ChatMessage("user", "hi")]).text == "Hello"
