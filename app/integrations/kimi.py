from __future__ import annotations

from dataclasses import dataclass

import httpx

DEFAULT_KIMI_BASE_URL = "https://api.kimi.com/coding/v1"
DEFAULT_KIMI_MODEL = "kimi-for-coding-highspeed"


class KimiError(RuntimeError):
    """Kimi request failed. Messages never include the API key or response bodies."""


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


class KimiChatClient:
    """Minimal OpenAI-compatible chat client for the Kimi API."""

    provider = "kimi"

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
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
            transport=transport,
        )

    def complete(self, messages: list[ChatMessage], *, max_tokens: int = 700) -> ChatCompletion:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
        }
        try:
            response = self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as error:
            raise KimiError(f"Kimi request failed: {type(error).__name__}") from error
        if response.status_code != 200:
            raise KimiError(f"Kimi returned HTTP {response.status_code}")
        try:
            body = response.json()
            text = body["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise KimiError("Kimi returned an unexpected response shape") from error
        usage = body.get("usage") or {}
        return ChatCompletion(
            text=text,
            model=str(body.get("model") or self.model),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )

    def close(self) -> None:
        self._client.close()
