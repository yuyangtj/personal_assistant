from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

#: Longest a model API may take to accept a connection.
CONNECT_SECONDS = 5.0


class ProviderError(RuntimeError):
    """A sanitized provider failure that is safe to use for failover decisions."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One call the model asked for through the API's own tool calling."""

    id: str
    name: str
    arguments: dict[str, Any]
    #: The arguments exactly as the model wrote them, sent back with the call.
    raw_arguments: str = "{}"
    #: Other fields of the call, sent back unchanged (Gemini's thought signature).
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str
    #: An assistant message's tool calls, sent back so the model sees what it asked.
    tool_calls: tuple[ToolCall, ...] = ()
    #: For role "tool": the call this is the result of.
    tool_call_id: str | None = None
    #: Provider fields of an assistant message that must be sent back unchanged with its
    #: tool calls (DeepSeek's and Kimi's reasoning_content, MiniMax's reasoning_details).
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChatCompletion:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)


def correction_turns(reply: str, correction: str, *, limit: int = 1000) -> list[ChatMessage]:
    """The model's unusable reply and what to fix, to append before asking again.

    An empty reply (a thinking model that spent its whole budget thinking) is left out:
    providers reject an empty assistant message (Kimi answers HTTP 400).
    """
    turns = [ChatMessage("assistant", reply[:limit])] if reply.strip() else []
    return [*turns, ChatMessage("user", correction)]


class ChatClient(Protocol):
    provider: str
    model: str

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 700,
    ) -> ChatCompletion: ...

    def close(self) -> None: ...


def post_within(
    client: httpx.Client, path: str, *, json: Any, deadline_seconds: float, **kwargs: Any
) -> httpx.Response:
    """POST and read the whole response, giving up after ``deadline_seconds`` in total.

    httpx's timeout limits each connect and each read, so a server that keeps sending a
    trickle of bytes can hold a request open for as long as it likes (a chat reply once
    hung for over 17 minutes). This caps the whole exchange; going over raises
    ``httpx.ReadTimeout`` like any other timeout.
    """
    deadline = time.monotonic() + deadline_seconds
    body = bytearray()
    with client.stream("POST", path, json=json, **kwargs) as response:
        for chunk in response.iter_bytes():
            body += chunk
            if time.monotonic() > deadline:
                raise httpx.ReadTimeout(
                    f"no complete response within {deadline_seconds:g} s", request=response.request
                )
    # Already decoded, so no headers: they would make httpx decode the body again.
    return httpx.Response(response.status_code, content=bytes(body), request=response.request)


#: Assistant-message fields that carry a model's reasoning, kept for the next request.
REASONING_FIELDS = ("reasoning_content", "reasoning_details", "extra_content")


def wire_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    """Messages in the OpenAI chat format, tool calls and results included."""
    wired = []
    for message in messages:
        item: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    **call.extra,
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.raw_arguments},
                }
                for call in message.tool_calls
            ]
        if message.tool_call_id is not None:
            item["tool_call_id"] = message.tool_call_id
        item.update(message.extra)
        wired.append(item)
    return wired


def read_tool_calls(message: Mapping[str, Any]) -> tuple[ToolCall, ...]:
    """The tool calls in an OpenAI-format assistant message; bad arguments become {}."""
    calls = []
    for index, call in enumerate(message.get("tool_calls") or []):
        function = (call or {}).get("function") or {}
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        raw = function.get("arguments")
        if isinstance(raw, dict):  # some providers send an object, not a string
            raw = json.dumps(raw)
        raw = raw if isinstance(raw, str) and raw.strip() else "{}"
        try:
            arguments = json.loads(raw)
        except ValueError:
            arguments = {}
        calls.append(
            ToolCall(
                id=str(call.get("id") or f"call_{index}"),
                name=name,
                arguments=arguments if isinstance(arguments, dict) else {},
                raw_arguments=raw,
                extra={k: v for k, v in call.items() if k not in ("id", "type", "function")},
            )
        )
    return tuple(calls)


def reasoning_extra(message: Mapping[str, Any]) -> dict[str, Any]:
    return {key: message[key] for key in REASONING_FIELDS if message.get(key)}
