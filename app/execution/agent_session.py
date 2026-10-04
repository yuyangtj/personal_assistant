"""Drive a coding agent as a live session over the Agent Client Protocol (ACP).

ACP is JSON-RPC 2.0 over the agent's stdio, one message per line. Unlike a one-shot
``--prompt`` run, a session streams what the agent is doing (its todo-list plan, each
tool call with a readable title) and can be cancelled cleanly. That is what lets the
assistant report progress while an agent works.

Kimi Code 2.0 speaks it as ``kimi acp``. The messages handled here were recorded from
that version: ``initialize``, ``session/new``, ``session/prompt`` → ``session/update``
notifications (``plan``, ``tool_call``, ``tool_call_update``, ``agent_message_chunk``)
and a final ``{"stopReason": ...}``; ``session/cancel`` ends a turn with
``stopReason: "cancelled"``; ``session/request_permission`` asks the client to choose.
"""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: (text, kind) where kind is "plan", "tool", "worktree" or "milestone".
ProgressCallback = Callable[[str, str], None]

ACP_PROTOCOL_VERSION = 1
_PLAN_MARKS = {"completed": "✓", "in_progress": "▸"}
#: Tool titles that only restate the plan, which is reported separately.
_QUIET_TOOL_TITLES = {"updating todo list", "todolist"}


class AcpError(RuntimeError):
    """The agent process failed, exited, or answered with a JSON-RPC error."""

    def __init__(self, message: str, *, diagnostic: str = ""):
        super().__init__(message)
        self.diagnostic = diagnostic


class AcpCancelled(RuntimeError):
    pass


class AcpTimeout(RuntimeError):
    pass


class AcpClient:
    """A newline-delimited JSON-RPC connection to an agent subprocess."""

    def __init__(self, command: list[str], *, cwd: Path, environment: Mapping[str, str]):
        # Closed in close(); it outlives this call, so no context manager.
        self._stderr = tempfile.TemporaryFile()  # noqa: SIM115
        self.process = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._next_id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                logger.debug("Ignoring non-JSON agent output: %s", line[:200])
                continue
            if isinstance(message, dict):
                self._messages.put(message)
        self._messages.put(None)  # the agent closed its output

    def _write(self, message: dict[str, Any]) -> None:
        if self.process.stdin is None or self.process.poll() is not None:
            raise AcpError("agent process is not running", diagnostic=self.diagnostic())
        try:
            self.process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise AcpError(
                "agent process closed its input", diagnostic=self.diagnostic()
            ) from error

    def request(self, method: str, params: dict[str, Any]) -> int:
        self._next_id += 1
        self._write({"id": self._next_id, "method": method, "params": params})
        return self._next_id

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"method": method, "params": params})

    def respond(self, request_id: Any, *, result: Any = None, error: dict | None = None) -> None:
        self._write({"id": request_id, **({"error": error} if error else {"result": result})})

    def next_message(self, timeout: float) -> dict[str, Any] | None:
        """The next message, None on timeout; raises when the agent has gone away."""
        try:
            message = self._messages.get(timeout=timeout)
        except queue.Empty:
            return None
        if message is None:
            self._messages.put(None)  # stay closed for later callers
            raise AcpError(
                f"agent exited with status {self.process.poll()}", diagnostic=self.diagnostic()
            )
        return message

    def diagnostic(self) -> str:
        try:
            self._stderr.seek(0)
            return self._stderr.read()[-16_000:].decode("utf-8", errors="replace")
        except (OSError, ValueError):
            return ""

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self._stderr.close()


class AcpSession:
    """One agent session in a working directory: prompt it, watch it, cancel it."""

    def __init__(
        self,
        client: AcpClient,
        *,
        cwd: Path,
        on_progress: ProgressCallback | None = None,
        handshake_timeout: float = 60.0,
    ):
        self.client = client
        self.cwd = cwd
        self.on_progress = on_progress
        self.handshake_timeout = handshake_timeout
        self.session_id: str | None = None
        self._last_plan = ""
        self._last_tool = ""

    def open(self) -> str:
        self._call(
            "initialize",
            {
                "protocolVersion": ACP_PROTOCOL_VERSION,
                # The agent works on its own files and terminal inside the worktree.
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
            },
        )
        result = self._call("session/new", {"cwd": str(self.cwd), "mcpServers": []})
        session_id = result.get("sessionId") if isinstance(result, dict) else None
        if not isinstance(session_id, str) or not session_id:
            raise AcpError("agent did not return a session id")
        self.session_id = session_id
        return session_id

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        request_id = self.client.request(method, params)
        deadline = time.monotonic() + self.handshake_timeout
        while time.monotonic() < deadline:
            message = self.client.next_message(timeout=1.0)
            if message is None:
                continue
            if message.get("id") == request_id and "method" not in message:
                return self._result(message, method)
            self._handle(message)
        raise AcpError(f"agent did not answer {method}", diagnostic=self.client.diagnostic())

    def _result(self, message: dict[str, Any], method: str) -> Any:
        if "error" in message:
            error = message["error"] or {}
            raise AcpError(
                f"{method} failed: {error.get('message', error)}",
                diagnostic=json.dumps(error)[:4000] + "\n" + self.client.diagnostic(),
            )
        return message.get("result")

    def prompt(
        self, text: str, *, deadline: float, is_cancelled: Callable[[], bool]
    ) -> tuple[str, str]:
        """Run one turn; returns (stop reason, the agent's reply text)."""
        if self.session_id is None:
            raise AcpError("session is not open")
        request_id = self.client.request(
            "session/prompt",
            {"sessionId": self.session_id, "prompt": [{"type": "text", "text": text}]},
        )
        reply: list[str] = []
        cancelling_since: float | None = None
        while True:
            if cancelling_since is None and is_cancelled():
                self.client.notify("session/cancel", {"sessionId": self.session_id})
                cancelling_since = time.monotonic()
            if cancelling_since is None and time.monotonic() >= deadline:
                self.client.notify("session/cancel", {"sessionId": self.session_id})
                raise AcpTimeout("agent turn timed out")
            if cancelling_since is not None and time.monotonic() - cancelling_since > 15:
                raise AcpCancelled("agent did not stop after cancel")
            message = self.client.next_message(timeout=0.5)
            if message is None:
                continue
            if message.get("id") == request_id and "method" not in message:
                result = self._result(message, "session/prompt") or {}
                stop_reason = str(result.get("stopReason") or "end_turn")
                if cancelling_since is not None or stop_reason == "cancelled":
                    raise AcpCancelled("agent turn was cancelled")
                return stop_reason, "".join(reply)
            chunk = self._handle(message)
            if chunk:
                reply.append(chunk)

    def _handle(self, message: dict[str, Any]) -> str:
        """React to a notification or agent request; returns any reply text it carried."""
        method = message.get("method")
        if method == "session/update":
            return self._update((message.get("params") or {}).get("update") or {})
        if method == "session/request_permission" and "id" in message:
            # The agent runs inside its own worktree in the isolated coding worker, as the
            # one-shot runners do with auto-approval; nothing it asks reaches the host.
            options = (message.get("params") or {}).get("options") or []
            chosen = next(
                (o for o in options if str(o.get("kind", "")).startswith("allow")),
                options[0] if options else None,
            )
            if chosen is None:
                self.client.respond(message["id"], result={"outcome": {"outcome": "cancelled"}})
            else:
                self.client.respond(
                    message["id"],
                    result={"outcome": {"outcome": "selected", "optionId": chosen["optionId"]}},
                )
            return ""
        if method is not None and "id" in message:
            self.client.respond(
                message["id"], error={"code": -32601, "message": f"{method} is not supported"}
            )
        return ""

    def _update(self, update: dict[str, Any]) -> str:
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content") or {}
            return str(content.get("text") or "") if content.get("type") == "text" else ""
        if kind == "plan":
            plan = "\n".join(
                f"{_PLAN_MARKS.get(str(entry.get('status')), '·')} "
                f"{str(entry.get('content', '')).strip()[:120]}"
                for entry in update.get("entries") or []
                if str(entry.get("content", "")).strip()
            )
            if plan and plan != self._last_plan:
                self._last_plan = plan
                self._progress(plan, "plan")
        elif kind == "tool_call_update" and update.get("status") == "in_progress":
            title = " ".join(str(update.get("title") or "").split())
            if title and title.lower() not in _QUIET_TOOL_TITLES and title != self._last_tool:
                self._last_tool = title
                self._progress(title.replace(str(self.cwd) + "/", "")[:160], "tool")
        return ""

    def _progress(self, text: str, kind: str) -> None:
        if self.on_progress is None:
            return
        try:
            self.on_progress(text, kind)
        except Exception:  # progress is best effort; never fail the agent over it
            logger.exception("Progress callback failed")
