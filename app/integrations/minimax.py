from __future__ import annotations

import json
import re

import httpx

from app.integrations.chat import ChatCompletion, ChatMessage, ProviderError
from app.manager.model.contracts import ManagerModelRequest, ModelInvocation

DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.chat/v1"
DEFAULT_MINIMAX_MODEL = "MiniMax-M2.7-highspeed"

_LEADING_THINK = re.compile(r"\A\s*<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


class MiniMaxError(ProviderError):
    """MiniMax request failed. Messages never include credentials or response bodies."""


class MiniMaxChatClient:
    """Minimal OpenAI-compatible chat client for conversational replies."""

    provider = "minimax"

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
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
            transport=transport,
        )

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 700,
    ) -> ChatCompletion:
        payload = {
            "model": self.model,
            "max_completion_tokens": max_tokens,
            "reasoning_split": True,
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
        }
        try:
            response = self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as error:
            raise MiniMaxError(f"MiniMax request failed: {type(error).__name__}") from error
        if response.status_code != 200:
            raise MiniMaxError(f"MiniMax returned HTTP {response.status_code}")
        try:
            body = response.json()
            base_response = body.get("base_resp") or {}
            if base_response.get("status_code", 0) != 0:
                raise MiniMaxError("MiniMax returned a provider error")
            text = body["choices"][0]["message"].get("content") or ""
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
        )

    def close(self) -> None:
        self._client.close()


class MiniMaxManagerModelClient:
    """MiniMax OpenAI-compatible implementation of manager task analysis."""

    provider = "minimax"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_MINIMAX_BASE_URL,
        model: str = DEFAULT_MINIMAX_MODEL,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError("A MiniMax API key is required")
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
            "max_completion_tokens": 1_000,
            "reasoning_split": True,
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
            response = self._client.post(
                "/chat/completions",
                json=payload,
                timeout=timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise MiniMaxError(
                f"MiniMax manager request failed: {type(error).__name__}"
            ) from error
        if response.status_code != 200:
            raise MiniMaxError(f"MiniMax manager returned HTTP {response.status_code}")
        try:
            body = response.json()
            base_response = body.get("base_resp") or {}
            if base_response.get("status_code", 0) != 0:
                raise MiniMaxError("MiniMax manager returned a provider error")
            raw_output = body["choices"][0]["message"].get("content")
            if isinstance(raw_output, str):
                raw_output = _LEADING_THINK.sub("", raw_output, count=1)
            if not isinstance(raw_output, (str, dict)):
                raise TypeError("manager content must be JSON text or an object")
        except MiniMaxError:
            raise
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise MiniMaxError("MiniMax manager returned an unexpected response shape") from error
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
