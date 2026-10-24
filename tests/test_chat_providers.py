from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.integrations.chat import ChatMessage
from app.integrations.deepseek import DeepSeekChatClient, DeepSeekError
from app.integrations.factory import chat_client_chain, provider_order


def test_deepseek_answers_without_thinking_by_default() -> None:
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [{"message": {"content": "hi", "reasoning_content": "hmm"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1},
            },
        )

    client = DeepSeekChatClient(api_key="k", transport=httpx.MockTransport(handler))
    completion = client.complete([ChatMessage("user", "hello")], max_tokens=50)

    assert (completion.text, completion.provider, completion.output_tokens) == ("hi", "deepseek", 1)
    assert sent == [
        {
            "model": "deepseek-flash",
            "max_tokens": 50,
            "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": "hello"}],
        }
    ]
    failing = DeepSeekChatClient(
        api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(402))
    )
    with pytest.raises(DeepSeekError, match="402"):
        failing.complete([ChatMessage("user", "hello")])


def test_deepseek_is_a_chat_provider_with_kimi_as_its_fallback() -> None:
    assert provider_order("deepseek", "auto") == ("deepseek", "kimi")
    chain = chat_client_chain(
        Settings(conversation_model_provider="deepseek", deepseek_api_key="d", kimi_api_key="k")
    )
    assert chain is not None and chain.provider.startswith("deepseek")
    # The manager model still only knows Kimi and MiniMax.
    with pytest.raises(ValueError):
        provider_order("deepseek", None, supported=frozenset({"kimi", "minimax"}))


def test_gemini_tool_calls_keep_their_signatures_for_the_next_request() -> None:
    from app.integrations.chat import wire_messages
    from app.integrations.gemini import GeminiChatClient

    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "extra_content": {"google": {"thought_signature": "m"}},
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "remember", "arguments": '{"text":"x"}'},
                                    "extra_content": {"google": {"thought_signature": "s"}},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    client = GeminiChatClient(api_key="g", transport=httpx.MockTransport(handler))
    tools = [{"type": "function", "function": {"name": "remember", "parameters": {}}}]
    completion = client.complete([ChatMessage("user", "remember x")], tools=tools)

    assert sent[0]["reasoning_effort"] == "low" and sent[0]["tools"] == tools
    (call,) = completion.tool_calls
    assert (call.name, call.arguments) == ("remember", {"text": "x"})
    wired = wire_messages(
        [
            ChatMessage("assistant", "", tool_calls=completion.tool_calls, extra=completion.extra),
            ChatMessage("tool", "ok", tool_call_id=call.id),
        ]
    )
    assert wired[0]["extra_content"] == {"google": {"thought_signature": "m"}}
    assert wired[0]["tool_calls"][0]["extra_content"] == {"google": {"thought_signature": "s"}}
    assert wired[1] == {"role": "tool", "content": "ok", "tool_call_id": "c1"}
