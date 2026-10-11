from __future__ import annotations

import json

import httpx

from app.integrations.chat import (
    CONNECT_SECONDS,
    ChatCompletion,
    ChatMessage,
    ProviderError,
    post_within,
)
from app.manager.model.contracts import ManagerModelRequest, ModelInvocation

DEFAULT_KIMI_BASE_URL = "https://api.kimi.com/coding/v1"
DEFAULT_KIMI_MODEL = "kimi-for-coding-highspeed"


class KimiError(ProviderError):
    """Kimi request failed. Messages never include the API key or response bodies."""


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
        self._deadline_seconds = timeout_seconds
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            # Connecting normally takes well under a second; a host that doesn't answer
            # must not cost the full timeout for each of its addresses.
            timeout=httpx.Timeout(timeout_seconds, connect=min(CONNECT_SECONDS, timeout_seconds)),
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
            text = body["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise KimiError("Kimi returned an unexpected response shape") from error
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


class KimiManagerModelClient:
    """Kimi implementation of the provider-neutral manager-analysis contract."""

    provider = "kimi"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_KIMI_BASE_URL,
        model: str = DEFAULT_KIMI_MODEL,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError("A Kimi API key is required")
        self.model = model
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    def generate(
        self,
        request: ManagerModelRequest,
        *,
        timeout_seconds: float,
    ) -> ModelInvocation:
        schema = json.dumps(request.response_schema, sort_keys=True, separators=(",", ":"))
        payload = {
            "model": self.model,
            "max_tokens": 1_000,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"{request.user_prompt}\n"
                        "RESPONSE_JSON_SCHEMA\n"
                        f"{schema}\n"
                        "END_RESPONSE_JSON_SCHEMA"
                    ),
                },
            ],
        }
        try:
            response = post_within(
                self._client,
                "/chat/completions",
                json=payload,
                deadline_seconds=timeout_seconds,
                timeout=timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise KimiError(f"Kimi manager request failed: {type(error).__name__}") from error
        if response.status_code != 200:
            raise KimiError(f"Kimi manager returned HTTP {response.status_code}")
        try:
            body = response.json()
            raw_output = body["choices"][0]["message"].get("content")
            if not isinstance(raw_output, (str, dict)):
                raise TypeError("manager content must be JSON text or an object")
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise KimiError("Kimi manager returned an unexpected response shape") from error
        usage = body.get("usage") or {}
        return ModelInvocation(
            raw_output=raw_output,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            provider=self.provider,
            model=str(body.get("model") or self.model),
        )

    def close(self) -> None:
        self._client.close()
