"""One-shot agent processes: running, watching the worktree, and restarting with new
instructions."""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from app.coding.agent_session import (
    GUIDANCE_INTERVAL,
    MAX_REDIRECTS,
    GuidanceSource,
    ProgressCallback,
)
from app.coding.base import CodingAgentError
from app.coding.git import git
from app.execution.fake import ExecutionCancelled

logger = logging.getLogger(__name__)

#: Seconds without any output or worktree change before a streaming agent is reported
#: as quiet. It is only a notice: silence doesn't prove the agent is stuck or gone.
QUIET_AFTER = 180.0


class OutputListener(Protocol):
    """Reads an agent's stdout line by line while it runs."""

    last_step: str

    def feed(self, line: str) -> None: ...


class _Redirected(Exception):
    def __init__(self, guidance: str):
        super().__init__(guidance)
        self.guidance = guidance


def run_code_agent_process(
    *,
    command: list[str],
    worktree: Path,
    timeout_seconds: int,
    is_cancelled: Callable[[], bool],
    environment: Mapping[str, str],
    display_name: str,
    on_progress: ProgressCallback | None = None,
    next_guidance: GuidanceSource | None = None,
    redirected_command: Callable[[Sequence[str]], list[str]] | None = None,
    output_listener: Callable[[], OutputListener] | None = None,
) -> str:
    """Run a one-shot agent. New user instructions restart it in the same worktree.

    A one-shot agent can't take input while it runs, so a redirect stops it and starts
    it again with the instructions added; its partial changes stay in the worktree.

    ``output_listener`` makes a fresh listener for each start of the agent. It gets each
    stdout line as it is written, and enables the quiet-agent notice.
    """
    deadline = time.monotonic() + timeout_seconds
    notes: list[str] = []
    steerable = next_guidance if redirected_command is not None else None
    while True:
        try:
            return _run_agent_once(
                command=command,
                worktree=worktree,
                deadline=deadline,
                is_cancelled=is_cancelled,
                environment=environment,
                display_name=display_name,
                on_progress=on_progress,
                next_guidance=steerable if len(notes) < MAX_REDIRECTS else None,
                listener=output_listener() if output_listener is not None else None,
            )
        except _Redirected as redirect:
            notes.append(redirect.guidance)
            report_milestone(on_progress, f"Redirected: {redirect.guidance[:160]}")
            assert redirected_command is not None
            command = redirected_command(notes)


def _run_agent_once(
    *,
    command: list[str],
    worktree: Path,
    deadline: float,
    is_cancelled: Callable[[], bool],
    environment: Mapping[str, str],
    display_name: str,
    on_progress: ProgressCallback | None,
    next_guidance: GuidanceSource | None,
    listener: OutputListener | None = None,
) -> str:
    watcher = WorktreeWatcher(worktree, on_progress)
    quiet = _QuietNotice(on_progress, listener) if listener is not None else None
    next_check = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="assistant-code-agent-") as temporary:
        temporary_path = Path(temporary)
        stdout_path = temporary_path / "stdout.log"
        stderr_path = temporary_path / "stderr.log"
        try:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    command,
                    cwd=worktree,
                    stdout=stdout,
                    stderr=stderr,
                    env=dict(environment),
                )
                reader = _LineReader(stdout_path) if listener is not None else None
                while process.poll() is None:
                    if is_cancelled():
                        process.terminate()
                        wait_or_kill(process)
                        raise ExecutionCancelled("Coding task was cancelled")
                    if time.monotonic() >= deadline:
                        process.terminate()
                        wait_or_kill(process)
                        raise CodingAgentError(f"{display_name} timed out", category="timeout")
                    if next_guidance is not None and time.monotonic() >= next_check:
                        next_check = time.monotonic() + GUIDANCE_INTERVAL
                        guidance = next_guidance()
                        if guidance:
                            process.terminate()
                            wait_or_kill(process)
                            raise _Redirected(guidance)
                    changed = watcher.poll()
                    if reader is not None and listener is not None and quiet is not None:
                        if _deliver(reader.read_lines(), listener) or changed:
                            quiet.activity()
                        quiet.check()
                    time.sleep(0.2)
                if reader is not None and listener is not None:
                    _deliver(reader.read_lines(final=True), listener)
        except FileNotFoundError as error:
            raise CodingAgentError(
                f"{display_name} executable was not found", category="unavailable"
            ) from error
        if process.returncode != 0:
            diagnostic = read_diagnostic(stderr_path, stdout_path)
            raise CodingAgentError(
                f"{display_name} exited with status {process.returncode}",
                category=failure_category(diagnostic),
            )
        return stdout_path.read_text(encoding="utf-8", errors="replace")


def with_redirects(request: str, notes: Sequence[str]) -> str:
    if not notes:
        return request
    return (
        request + "\n\nWHILE AN EARLIER ATTEMPT WAS WORKING, THE USER ADDED THESE INSTRUCTIONS "
        "(the worktree already has its partial changes; continue from them):\n"
        + "\n".join(f"- {note}" for note in notes)
    )


def optional(**kwargs: Any) -> dict[str, Any]:
    """Keyword arguments that are set, so runners without them keep working."""
    return {name: value for name, value in kwargs.items() if value is not None}


def report_milestone(progress: ProgressCallback | None, text: str) -> None:
    if progress is not None:
        try:
            progress(text, "milestone")
        except Exception:  # progress is best effort
            logger.exception("Progress callback failed")


class WorktreeWatcher:
    """For agents that can't stream their steps: report what changes in the worktree."""

    def __init__(
        self, worktree: Path, on_progress: ProgressCallback | None, *, interval: float = 20.0
    ):
        self.worktree = worktree
        self.on_progress = on_progress
        self.interval = interval
        self._next = time.monotonic() + interval
        self._last = ""

    def poll(self) -> bool:
        """Report a new worktree summary when one is due; True when it changed."""
        if self.on_progress is None or time.monotonic() < self._next:
            return False
        self._next = time.monotonic() + self.interval
        try:
            summary = worktree_summary(self.worktree)
        except (CodingAgentError, OSError):
            return False
        if summary and summary != self._last:
            self._last = summary
            _report_kind(self.on_progress, summary, "worktree")
            return True
        return False


class _LineReader:
    """Reads the complete lines an agent has appended to its stdout file so far."""

    def __init__(self, path: Path):
        self.path = path
        self._offset = 0
        self._partial = b""

    def read_lines(self, *, final: bool = False) -> list[str]:
        try:
            with self.path.open("rb") as stream:
                stream.seek(self._offset)
                chunk = stream.read()
        except OSError:
            return []
        self._offset += len(chunk)
        *lines, self._partial = (self._partial + chunk).split(b"\n")
        if final and self._partial:
            lines.append(self._partial)
            self._partial = b""
        return [line.decode("utf-8", errors="replace") for line in lines]


def _deliver(lines: list[str], listener: OutputListener) -> bool:
    for line in lines:
        try:
            listener.feed(line)
        except Exception:  # progress is best effort; never fail the agent over it
            logger.exception("Output listener failed")
    return bool(lines)


class _QuietNotice:
    """Says once when a streaming agent has gone quiet, and again after it resumes."""

    def __init__(self, on_progress: ProgressCallback | None, listener: OutputListener):
        self.on_progress = on_progress
        self.listener = listener
        self._last_activity = time.monotonic()
        self._reported = False

    def activity(self) -> None:
        self._last_activity = time.monotonic()
        self._reported = False

    def check(self) -> None:
        quiet_for = time.monotonic() - self._last_activity
        if self._reported or quiet_for < QUIET_AFTER:
            return
        self._reported = True
        minutes = max(1, round(quiet_for / 60))
        text = f"No activity for {minutes} min"
        if self.listener.last_step:
            text += f" (last step: {self.listener.last_step})"
        report_milestone(self.on_progress, text)


def _report_kind(progress: ProgressCallback, text: str, kind: str) -> None:
    try:
        progress(text, kind)
    except Exception:
        logger.exception("Progress callback failed")


def worktree_summary(worktree: Path) -> str:
    """E.g. "Changed app/web/index.html, app/api/routes.py (+40 −12)"."""
    changed = [
        line[3:].strip()
        for line in git(worktree, "status", "--porcelain", timeout=10).splitlines()
        if line.strip()
    ]
    if not changed:
        return ""
    numbers = re.findall(
        r"(\d+) insertion|(\d+) deletion", git(worktree, "diff", "--shortstat", timeout=10)
    )
    added = sum(int(a) for a, _ in numbers if a)
    removed = sum(int(d) for _, d in numbers if d)
    shown = ", ".join(changed[:3]) + (f" and {len(changed) - 3} more" if len(changed) > 3 else "")
    stats = f" (+{added} −{removed})" if added or removed else ""
    return f"Changed {shown}{stats}"[:200]


def read_diagnostic(*paths: Path) -> str:
    chunks: list[str] = []
    for path in paths:
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace")[-16_000:])
        except OSError:
            continue
    return "\n".join(chunks)


def failure_category(diagnostic: str) -> str:
    normalized = diagnostic.lower()
    quota_markers = (
        "quota exceeded",
        "quota exhausted",
        "usage limit",
        "credit balance",
        "insufficient balance",
        "out of tokens",
    )
    if any(marker in normalized for marker in quota_markers):
        return "quota_exhausted"
    rate_markers = ("rate limit", "rate_limit", "too many requests", "http 429", "status 429")
    if any(marker in normalized for marker in rate_markers):
        return "rate_limited"
    auth_markers = ("unauthorized", "invalid api key", "authentication failed", "http 401")
    if any(marker in normalized for marker in auth_markers):
        return "authentication_failed"
    return "execution_failed"


def wait_or_kill(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
