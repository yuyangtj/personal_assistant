"""A stand-in for ``claude --print --output-format stream-json``.

It replays ``fixtures/claude_stream_sample.jsonl``, recorded from Claude Code 2.1.280 on
MiniMax, with ``{cwd}`` set to the working directory. FAKE_CLAUDE_MODE picks the
behaviour: "work" (replays it and writes test_calc.py), "hang" (starts, then waits until
killed), "quiet" (one tool step, silence for FAKE_CLAUDE_PAUSE seconds, then the rest),
"error" (ends with an error result but exit status 0).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

SAMPLE = Path(__file__).with_name("fixtures") / "claude_stream_sample.jsonl"
mode = os.environ.get("FAKE_CLAUDE_MODE", "work")
cwd = os.getcwd()
events = [
    json.loads(line.replace("{cwd}", cwd))
    for line in SAMPLE.read_text(encoding="utf-8").splitlines()
    if line.strip()
]


def send(event: dict) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def is_tool_use(event: dict) -> bool:
    content = (event.get("message") or {}).get("content") or []
    return event.get("type") == "assistant" and any(
        isinstance(block, dict) and block.get("type") == "tool_use" for block in content
    )


if mode == "hang":
    send(events[0])
    time.sleep(3600)

first_tool = next(index for index, event in enumerate(events) if is_tool_use(event))
for index, event in enumerate(events):
    if event.get("type") == "result":
        if mode == "error":
            event = {**event, "is_error": True, "result": "API Error: 429 rate limit reached"}
        else:
            redirected = "Name it minus" in sys.argv[-1]
            event = {**event, "result": "Done, redirected" if redirected else "Done"}
    send(event)
    if mode == "quiet" and index == first_tool:
        time.sleep(float(os.environ.get("FAKE_CLAUDE_PAUSE", "1.5")))
    time.sleep(0.05)
if mode == "work":
    Path(cwd, "test_calc.py").write_text("import unittest\n", encoding="utf-8")
