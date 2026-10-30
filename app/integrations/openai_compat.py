"""One client for the chat providers, which all speak the OpenAI chat-completions API.

They differ only in a few request fields and in how a reply comes back, so each is a
:class:`ProviderPreset`:

- Kimi: https://api.kimi.com/coding/v1 (streams);
- DeepSeek: https://api-docs.deepseek.com/ (streams). ``deepseek-flash`` thinks by default;
  chat replies turn it off, since the reply has to come within a few seconds;
- MiniMax: ``max_completion_tokens``, the reasoning split from the reply, a provider status
  in ``base_resp``, and a leading ``<think>`` block taken off the text;
- Gemini: https://ai.google.dev/gemini-api/docs/openai. Gemini 3 always thinks; a low
  ``reasoning_effort`` keeps chat replies quick.

Errors are :class:`ProviderError` with messages that never include the API key or the
provider's response body.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.integrations.chat import (
    CONNECT_SECONDS,
    ChatCompletion,
    ChatMessage,
    ProviderError,
    read_tool_calls,
    reasoning_extra,
    request_completion,
    wire_messages,
)

_LEADING_THINK = re.compile(r"\A\s*<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class ProviderPreset:
    provider: str
    #: How it is named in errors and logs.
    name: str
    base_url: str
    model: str
    #: Can hand over the reply's text as it is written (``on_text``).
    streams: bool = False
    #: The request field that caps the reply's length.
    token_field: str = "max_tokens"
    #: Request fields it always sends (a client's own ``extra_payload`` overrides them; None
    #: leaves a field out).
    extra_payload: Mapping[str, Any] = field(default_factory=dict)
    #: Raises ProviderError when a 200 response still reports a provider failure.
    check_body: Callable[[dict[str, Any]], None] | None = None
    #: Tidies the reply's text.
    clean_text: Callable[[str], str] | None = None


def _minimax_status(body: dict[str, Any]) -> None:
    if (body.get("base_resp") or {}).get("status_code", 0) != 0:
        raise ProviderError("MiniMax returned a provider error")


KIMI = ProviderPreset(
    "kimi", "Kimi", "https://api.kimi.com/coding/v1", "kimi-for-coding-highspeed", streams=True
)
DEEPSEEK = ProviderPreset(
    "deepseek",
    "DeepSeek",
    "https://api.deepseek.com",
    "deepseek-flash",
    streams=True,
    extra_payload={"thinking": {"type": "disabled"}},
)
MINIMAX = ProviderPreset(
    "minimax",
    "MiniMax",
    "https://api.minimax.chat/v1",
    "MiniMax-M3",
    token_field="max_completion_tokens",
    extra_payload={"reasoning_split": True},
    check_body=_minimax_status,
    clean_text=lambda text: _LEADING_THINK.sub("", text, count=1),
)
GEMINI = ProviderPreset(
    "gemini",
    "Gemini",
    "https://generativelanguage.googleapis.com/v1beta/openai",
    "gemini-3.8-flash",
    extra_payload={"reasoning_effort": "low"},
)
PRESETS = {preset.provider: preset for preset in (KIMI, DEEPSEEK, MINIMAX, GEMINI)}


class OpenAICompatibleChatClient:
    #: Takes ``tools`` and returns the model's calls (OpenAI-style tool calling).
    supports_tools = True

    def __init__(
        self,
        preset: ProviderPreset,
        *,
        api_key: str,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 30.0,
        extra_payload: Mapping[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError(f"A {preset.name} API key is required")
        self.preset = preset
        self.provider = preset.provider
        self.streams = preset.streams
        self.model = model or preset.model
        self._extra_payload = {
            key: value
            for key, value in {**preset.extra_payload, **(extra_payload or {})}.items()
            if value is not None
        }
        self._deadline_seconds = timeout_seconds
        self._client = httpx.Client(
            base_url=(base_url or preset.base_url).rstrip("/"),
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
        on_text: Callable[[str], None] | None = None,
    ) -> ChatCompletion:
        name = self.preset.name
        payload: dict[str, Any] = {
            "model": self.model,
            self.preset.token_field: max_tokens,
            **self._extra_payload,
            "messages": wire_messages(messages),
        }
        if tools:
            payload["tools"] = tools
        body = request_completion(
            self._client,
            payload,
            deadline_seconds=self._deadline_seconds,
            on_text=on_text if self.streams else None,
            error=ProviderError,
            name=name,
        )
        try:
            if self.preset.check_body is not None:
                self.preset.check_body(body)
            message = body["choices"][0]["message"]
            text = message.get("content") or ""
            if not isinstance(text, str):
                raise TypeError("chat content must be text")
        except ProviderError:
            raise
        except (KeyError, IndexError, TypeError, AttributeError) as error:
            raise ProviderError(f"{name} returned an unexpected response shape") from error
        if self.preset.clean_text is not None:
            text = self.preset.clean_text(text)
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
