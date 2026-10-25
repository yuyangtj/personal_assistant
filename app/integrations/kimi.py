from __future__ import annotations

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

DEFAULT_KIMI_BASE_URL = "https://api.kimi.com/coding/v1"
DEFAULT_KIMI_MODEL = "kimi-for-coding-highspeed"


class KimiError(ProviderError):
    """Kimi request failed. Messages never include the API key or response bodies."""


class KimiChatClient:
    """Minimal OpenAI-compatible chat client for the Kimi API."""

    provider = "kimi"
    #: Takes ``tools`` and returns the model's calls (OpenAI-style tool calling).
    supports_tools = True

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_KIMI_BASE_URL,
        model: str = DEFAULT_KIMI_MODEL,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError("A Kimi API key is required")
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
            "max_tokens": max_tokens,
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
            raise KimiError(f"Kimi request failed: {type(error).__name__}") from error
        if response.status_code != 200:
            raise KimiError(f"Kimi returned HTTP {response.status_code}")
        try:
            body = response.json()
            message = body["choices"][0]["message"]
            text = message.get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise KimiError("Kimi returned an unexpected response shape") from error
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
