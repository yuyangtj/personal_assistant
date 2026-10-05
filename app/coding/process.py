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
from typing import Any

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
) -> str:
    """Run a one-shot agent. New user instructions restart it in the same worktree.

    A one-shot agent can't take input while it runs, so a redirect stops it and starts
    it again with the instructions added; its partial changes stay in the worktree.
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
) -> str:
    watcher = WorktreeWatcher(worktree, on_progress)
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
                    watcher.poll()
                    time.sleep(0.2)
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

    def poll(self) -> None:
        if self.on_progress is None or time.monotonic() < self._next:
            return
        self._next = time.monotonic() + self.interval
        try:
            summary = worktree_summary(self.worktree)
        except (CodingAgentError, OSError):
            return
        if summary and summary != self._last:
            self._last = summary
            _report_kind(self.on_progress, summary, "worktree")


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
