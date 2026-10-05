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
