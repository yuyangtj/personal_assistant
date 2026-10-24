"""DeepSeek's chat API (OpenAI-compatible): https://api-docs.deepseek.com/

``deepseek-flash`` is served by DeepSeek-V4.1-Flash. Thinking is on by default there;
chat replies turn it off, since the reply has to come within a few seconds.
"""

from __future__ import annotations

import httpx

from app.integrations.chat import (
    CONNECT_SECONDS,
    ChatCompletion,
    ChatMessage,
    ProviderError,
    post_within,
)

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"


class DeepSeekError(ProviderError):
    """DeepSeek request failed. Messages never include the API key or response bodies."""


class DeepSeekChatClient:
    provider = "deepseek"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
        model: str = DEFAULT_DEEPSEEK_MODEL,
        thinking: bool = False,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError("A DeepSeek API key is required")
        self.model = model
        self.thinking = thinking
        self._deadline_seconds = timeout_seconds
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout_seconds, connect=min(CONNECT_SECONDS, timeout_seconds)),
            transport=transport,
        )

    def complete(self, messages: list[ChatMessage], *, max_tokens: int = 700) -> ChatCompletion:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "thinking": {"type": "enabled" if self.thinking else "disabled"},
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
        }
        try:
            response = post_within(
                self._client,
                "/chat/completions",
                json=payload,
                deadline_seconds=self._deadline_seconds,
            )
        except httpx.HTTPError as error:
            raise DeepSeekError(f"DeepSeek request failed: {type(error).__name__}") from error
        if response.status_code != 200:
            raise DeepSeekError(f"DeepSeek returned HTTP {response.status_code}")
        try:
            body = response.json()
            text = body["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise DeepSeekError("DeepSeek returned an unexpected response shape") from error
        usage = body.get("usage") or {}
        return ChatCompletion(
            text=text,
            model=str(body.get("model") or self.model),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            provider=self.provider,
        )

    def close(self) -> None:
        self._client.close()
