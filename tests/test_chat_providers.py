from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.integrations.chat import ChatMessage, ProviderError
from app.integrations.factory import chat_client_chain, provider_order
from app.integrations.openai_compat import DEEPSEEK, GEMINI, OpenAICompatibleChatClient


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

    client = OpenAICompatibleChatClient(
        DEEPSEEK, api_key="k", transport=httpx.MockTransport(handler)
    )
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
    failing = OpenAICompatibleChatClient(
        DEEPSEEK, api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(402))
    )
    with pytest.raises(ProviderError, match="402"):
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

    client = OpenAICompatibleChatClient(GEMINI, api_key="g", transport=httpx.MockTransport(handler))
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


def test_each_provider_is_built_from_its_preset_with_the_settings_overrides() -> None:
    from app.integrations.factory import build_chat_client

    sent: dict[str, dict] = {}

    def capture(provider: str):
        def handler(request: httpx.Request) -> httpx.Response:
            sent[provider] = json.loads(request.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        return httpx.MockTransport(handler)

    settings = Settings(
        kimi_api_key="k",
        deepseek_api_key="d",
        minimax_api_key="m",
        gemini_api_key="g",
        deepseek_thinking=True,
        gemini_reasoning_effort="",
    )
    for provider in ("kimi", "deepseek", "minimax", "gemini"):
        client = build_chat_client(settings, provider, primary=False)
        assert client is not None and client.provider == provider
        client._client._transport = capture(provider)  # type: ignore[attr-defined]
        assert client.complete([ChatMessage("user", "hi")], max_tokens=9).text == "ok"

    assert sent["kimi"]["max_tokens"] == 9
    assert sent["deepseek"]["thinking"] == {"type": "enabled"}
    assert sent["minimax"]["max_completion_tokens"] == 9
    assert sent["minimax"]["reasoning_split"] is True
    assert "reasoning_effort" not in sent["gemini"]  # turned off in the settings
    streams = {p: build_chat_client(settings, p, primary=False).streams for p in sent}
    assert streams == {"kimi": True, "deepseek": True, "minimax": False, "gemini": False}
    assert build_chat_client(Settings(), "kimi", primary=False) is None
