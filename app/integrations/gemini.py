"""Gemini through its OpenAI-compatible API: https://ai.google.dev/gemini-api/docs/openai"""

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

DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"


class GeminiError(ProviderError):
    """Gemini request failed. Messages never include the API key or response bodies."""


class GeminiChatClient:
    provider = "gemini"
    #: Takes ``tools`` and returns the model's calls (OpenAI-style tool calling).
    supports_tools = True

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_GEMINI_BASE_URL,
        model: str = DEFAULT_GEMINI_MODEL,
        reasoning_effort: str | None = "low",
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError("A Gemini API key is required")
        self.model = model
        #: Gemini 3 always thinks; "low" keeps chat replies quick.
        self.reasoning_effort = reasoning_effort
        self._deadline_seconds = timeout_seconds
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
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
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": wire_messages(messages),
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
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
            raise GeminiError(f"Gemini request failed: {type(error).__name__}") from error
        if response.status_code != 200:
            raise GeminiError(f"Gemini returned HTTP {response.status_code}")
        try:
            body = response.json()
            message = body["choices"][0]["message"]
            text = message.get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise GeminiError("Gemini returned an unexpected response shape") from error
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
