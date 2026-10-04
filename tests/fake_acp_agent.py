"""A stand-in for ``kimi acp``, replaying the message shapes recorded from Kimi Code 2.0.

Behaviour is chosen with FAKE_ACP_MODE: "work" (plan, two tool steps, writes a file,
replies), "hang" (plans, then waits until cancelled), "crash" (exits during the turn).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

mode = os.environ.get("FAKE_ACP_MODE", "work")
cancelled = threading.Event()
session_id = "session_fake"
cwd = Path.cwd()


def send(message: dict) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()


def update(payload: dict) -> None:
    send({"method": "session/update", "params": {"sessionId": session_id, "update": payload}})


def plan(*entries: tuple[str, str]) -> None:
    update(
        {
            "sessionUpdate": "plan",
            "entries": [
                {"content": text, "priority": "medium", "status": status}
                for text, status in entries
            ],
        }
    )


def tool(call_id: str, title: str) -> None:
    update(
        {
            "sessionUpdate": "tool_call",
            "toolCallId": call_id,
            "title": "Write",
            "kind": "edit",
            "status": "pending",
        }
    )
    update(
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": call_id,
            "status": "in_progress",
            "content": [{"type": "content", "content": {"type": "text", "text": "{"}}],
        }
    )
    update(
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": call_id,
            "title": title,
            "kind": "edit",
            "status": "in_progress",
        }
    )
    update({"sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": "completed"})


def run_prompt(request_id: int) -> None:
    plan(("Edit README", "in_progress"), ("Check the result", "pending"))
    if mode == "hang":
        while not cancelled.wait(0.05):
            pass
        send({"id": request_id, "result": {"stopReason": "cancelled"}})
        return
    if mode == "crash":
        sys.stderr.write("HTTP 429: rate limit reached\n")
        sys.stderr.flush()
        os._exit(3)
    tool("0:a", f"Writing {cwd}/README.md")
    (cwd / "README.md").write_text("after\n", encoding="utf-8")
    send(
        {
            "id": 99,
            "method": "session/request_permission",
            "params": {
                "sessionId": session_id,
                "options": [
                    {"optionId": "reject", "kind": "reject_once", "name": "No"},
                    {"optionId": "ok", "kind": "allow_once", "name": "Yes"},
                ],
            },
        }
    )
    tool("0:b", "Running: git diff --stat")
    plan(("Edit README", "completed"), ("Check the result", "completed"))
    for chunk in ['{"summary": "Updated', ' the README", "tests": [], "notes": []}']:
        update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": chunk}})
    time.sleep(0.05)
    send({"id": request_id, "result": {"stopReason": "end_turn"}})


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        send({"id": message["id"], "result": {"protocolVersion": 1, "agentCapabilities": {}}})
    elif method == "session/new":
        send({"id": message["id"], "result": {"sessionId": session_id}})
    elif method == "session/prompt":
        threading.Thread(target=run_prompt, args=(message["id"],), daemon=True).start()
    elif method == "session/cancel":
        cancelled.set()
    elif "result" in message and message.get("id") == 99:
        Path(cwd / ".permission").write_text(json.dumps(message["result"]), encoding="utf-8")
