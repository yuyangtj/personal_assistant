"""Extracting the JSON object a model was asked to return."""

from __future__ import annotations

import json
import re
from typing import Any


def extract_json_object(text: str) -> Any:
    """The JSON object in a reply, tolerating reasoning tags, fences, or prose around it."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the reply")
    return json.loads(cleaned[start : end + 1])


_ARROW_TOOL = re.compile(r'tool\s*=>\s*"(?P<tool>[^"]+)"', re.DOTALL)
_ARROW_ARGUMENTS = re.compile(r"arguments\s*=>\s*", re.DOTALL)


def extract_arrow_tool_call(text: str) -> dict[str, Any] | None:
    """A tool call in MiniMax's native syntax, as an agent step; None when there is none.

    MiniMax sometimes answers ``[TOOL_CALL] {tool => "name", arguments => {...}}
    [/TOOL_CALL]`` instead of the JSON it was asked for.
    """
    tool = _ARROW_TOOL.search(text)
    if tool is None:
        return None
    arguments: Any = {}
    found = _ARROW_ARGUMENTS.search(text, tool.end())
    if found is not None:
        start = text.find("{", found.end())
        if start != -1:
            try:
                arguments, _ = json.JSONDecoder().raw_decode(text[start:])
            except ValueError:
                arguments = {}
    return {
        "action": "tool",
        "tool": tool.group("tool"),
        "arguments": arguments if isinstance(arguments, dict) else {},
    }


_DSML_INVOKE = re.compile(r'invoke\s+name\s*=\s*"(?P<tool>[^"]+)"')
_DSML_PARAMETER = re.compile(
    r'parameter\s+name\s*=\s*"(?P<name>[^"]+)"(?:\s+string\s*=\s*"(?P<string>true|false)")?'
    r"\s*>?(?P<value>.*?)(?=</|<\uff5c|\Z)",
    re.DOTALL,
)


def extract_dsml_tool_calls(text: str) -> list[dict[str, Any]]:
    """Tool calls in DeepSeek's native syntax, in order; empty when there are none.

    DeepSeek sometimes answers ``<｜DSML｜invoke name="x"> <｜DSML｜parameter name="k"
    string="true">v</｜DSML｜parameter>`` (often mangled, with the ``>`` missing, or a
    JSON object in place of the parameters) instead of the JSON it was asked for.
    """
    if "DSML" not in text:
        return []
    invokes = list(_DSML_INVOKE.finditer(text))
    calls = []
    for index, invoke in enumerate(invokes):
        end = invokes[index + 1].start() if index + 1 < len(invokes) else len(text)
        body = text[invoke.end() : end]
        arguments: dict[str, Any] = {}
        for parameter in _DSML_PARAMETER.finditer(body):
            raw = parameter["value"].strip()
            if parameter["string"] == "false":
                try:
                    arguments[parameter["name"]] = json.loads(raw)
                    continue
                except ValueError:
                    pass
            arguments[parameter["name"]] = raw
        if not arguments and (start := body.find("{")) != -1:
            try:
                value, _ = json.JSONDecoder().raw_decode(body[start:])
            except ValueError:
                value = {}
            arguments = value if isinstance(value, dict) else {}
        calls.append({"tool": invoke["tool"], "arguments": arguments})
    return calls
