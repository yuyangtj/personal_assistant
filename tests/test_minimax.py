from __future__ import annotations

import json

import httpx
import pytest

from app.capabilities import CapabilityRegistry
from app.integrations.chat import ChatCompletion, ChatMessage
from app.integrations.minimax import MiniMaxChatClient, MiniMaxError, MiniMaxManagerModelClient
from app.manager.model import ValidatedManagerModelAdapter
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

    client = MiniMaxChatClient(
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

    client = MiniMaxChatClient(
        api_key="sk-minimax-secret",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(MiniMaxError) as captured:
        client.complete([ChatMessage("user", "Hi")])

    assert str(captured.value) == "MiniMax returned a provider error"
    assert "sk-minimax-secret" not in str(captured.value)
    client.close()


def test_minimax_manager_client_normalizes_thinking_and_fenced_json() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                "<think>private reasoning</think>\n"
                                f"```json\n{json.dumps(_analysis())}\n```"
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 42, "completion_tokens": 18},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    registry = CapabilityRegistry.from_directory("capabilities")
    client = MiniMaxManagerModelClient(
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )

    result = ValidatedManagerModelAdapter(client).analyze(_task(), registry.list())

    assert result.provider == "minimax"
    assert result.model == "MiniMax-M2.7-highspeed"
    assert result.analysis.required_capabilities == ("conversation",)
    assert result.usage.input_tokens == 42
    assert result.usage.output_tokens == 18
    assert seen["path"] == "/v1/chat/completions"
    assert seen["authorization"] == "Bearer sk-test"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["reasoning_split"] is True
    assert body["max_completion_tokens"] == 1_000
    assert "RESPONSE_JSON_SCHEMA" in body["messages"][1]["content"]
    client.close()


def test_minimax_provider_error_is_sanitized() -> None:
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

    client = MiniMaxManagerModelClient(
        api_key="sk-minimax-secret",
        transport=httpx.MockTransport(handler),
    )
    adapter = ValidatedManagerModelAdapter(client)

    with pytest.raises(MiniMaxError) as captured:
        client.generate(
            adapter._build_request(_task(), tuple()),
            timeout_seconds=5,
        )

    assert str(captured.value) == "MiniMax manager returned a provider error"
    assert "sk-minimax-secret" not in str(captured.value)
    client.close()
