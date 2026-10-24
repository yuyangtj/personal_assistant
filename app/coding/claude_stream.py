"""Read Claude Code's ``--output-format stream-json`` events as progress.

With ``--print --verbose --output-format stream-json`` Claude Code writes one JSON event
per line while it works. The shapes handled here were recorded from Claude Code 2.1.280
on MiniMax (``tests/fixtures/claude_stream_sample.jsonl``):

- ``{"type": "system", ...}``: session start and thinking-token counts.
- ``{"type": "assistant", "message": {"content": [...]}}`` with ``text``, ``thinking``
  and ``tool_use`` blocks (``name``, ``input``).
- ``{"type": "user", ...}``: tool results.
- ``{"type": "result", "result": "...", "is_error": false}``: the last line.

That version has no todo-list tool in print mode, so there is no plan to report, only
tool steps.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from app.coding.agent_session import ProgressCallback

logger = logging.getLogger(__name__)

#: Tools whose input names a file; the step reads "<Tool> <path>".
_FILE_TOOLS = {"Read", "Edit", "Write", "NotebookEdit"}
_TITLE_LIMIT = 160


class ClaudeStreamProgress:
    """Turns stream events into "tool" progress lines; one per run of the agent."""

    def __init__(self, cwd: Path, on_progress: ProgressCallback | None):
        self.on_progress = on_progress
        self._prefixes = sorted(
            {str(cwd) + "/", os.path.realpath(cwd) + "/"}, key=len, reverse=True
        )
        self.last_step = ""

    def feed(self, line: str) -> None:
        """Handle one stdout line; ignores anything it doesn't recognise."""
        event = _json_object(line)
        if event is None or event.get("type") != "assistant":
            return
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                title = self._title(block)
                if title and title != self.last_step:
                    self.last_step = title
                    self._progress(title)

    def _title(self, block: dict[str, Any]) -> str:
        name = str(block.get("name") or "").strip()
        raw_input = block.get("input")
        tool_input: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
        if name == "Bash":
            # Claude Code gives every command a short description; prefer it.
            text = str(tool_input.get("description") or tool_input.get("command") or "")
        elif name in _FILE_TOOLS:
            path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
            text = f"{name} {path}" if path else name
        elif name == "WebFetch":
            text = f"Fetch {tool_input.get('url') or ''}"
        elif name == "WebSearch":
            text = f"Search: {tool_input.get('query') or ''}"
        elif name == "Task":
            text = f"Subagent: {tool_input.get('description') or ''}"
        else:
            text = name
        for prefix in self._prefixes:
            text = text.replace(prefix, "")
        return " ".join(text.split())[:_TITLE_LIMIT]

    def _progress(self, text: str) -> None:
        if self.on_progress is None:
            return
        try:
            self.on_progress(text, "tool")
        except Exception:  # progress is best effort; never fail the agent over it
            logger.exception("Progress callback failed")


def final_result(output: str) -> tuple[str | None, bool]:
    """The text and error flag of the stream's last ``result`` event, if it has one."""
    for line in reversed(output.splitlines()):
        event = _json_object(line)
        if event is not None and event.get("type") == "result":
            result = event.get("result")
            return (result if isinstance(result, str) else None), bool(event.get("is_error"))
    return None, False


def _json_object(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
