from __future__ import annotations

import re
from typing import Any

import httpx

from app.integrations.chat import (
    CONNECT_SECONDS,
    ChatCompletion,
    ChatMessage,
    ProviderError,
    post_within,
    read_tool_calls,
    reasoning_extra,
    wire_messages,
)

DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.chat/v1"
DEFAULT_MINIMAX_MODEL = "MiniMax-M3"

_LEADING_THINK = re.compile(r"\A\s*<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


class MiniMaxError(ProviderError):
    """MiniMax request failed. Messages never include credentials or response bodies."""


class MiniMaxChatClient:
    """Minimal OpenAI-compatible chat client for conversational replies."""

    provider = "minimax"
    #: Takes ``tools`` and returns the model's calls (OpenAI-style tool calling).
    supports_tools = True

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_MINIMAX_BASE_URL,
        model: str = DEFAULT_MINIMAX_MODEL,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError("A MiniMax API key is required")
        self.model = model
        self._deadline_seconds = timeout_seconds
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            # Connecting normally takes well under a second; a host that doesn't answer
            # must not cost the full timeout for each of its addresses.
            timeout=httpx.Timeout(timeout_seconds, connect=min(CONNECT_SECONDS, timeout_seconds)),
            transport=transport,
        )

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 700,
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatCompletion:
        payload = {
            "model": self.model,
            "max_completion_tokens": max_tokens,
            "reasoning_split": True,
            "messages": wire_messages(messages),
        }
        if tools:
            payload["tools"] = tools
        try:
            response = post_within(
                self._client,
                "/chat/completions",
                json=payload,
                deadline_seconds=self._deadline_seconds,
            )
        except httpx.HTTPError as error:
            raise MiniMaxError(f"MiniMax request failed: {type(error).__name__}") from error
        if response.status_code != 200:
            raise MiniMaxError(f"MiniMax returned HTTP {response.status_code}")
        try:
            body = response.json()
            base_response = body.get("base_resp") or {}
            if base_response.get("status_code", 0) != 0:
                raise MiniMaxError("MiniMax returned a provider error")
            message = body["choices"][0]["message"]
            text = message.get("content") or ""
            if not isinstance(text, str):
                raise TypeError("chat content must be text")
            text = _LEADING_THINK.sub("", text, count=1)
        except MiniMaxError:
            raise
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise MiniMaxError("MiniMax returned an unexpected response shape") from error
        usage = body.get("usage") or {}
        return ChatCompletion(
            text=text,
            model=str(body.get("model") or self.model),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            provider=self.provider,
            tool_calls=read_tool_calls(message),
            extra=reasoning_extra(message),
        )

    def close(self) -> None:
        self._client.close()
