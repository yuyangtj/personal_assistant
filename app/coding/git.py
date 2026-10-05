"""Git commands for coding runs, with a lock per shared checkout."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Sequence
from pathlib import Path

from app.coding.base import CodingAgentError, safe_agent_environment


def git_ref_exists(repository: Path, ref: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(repository), "show-ref", "--verify", "--quiet", ref],
        capture_output=True,
        check=False,
        env=safe_agent_environment(),
    )
    return completed.returncode == 0


def delete_git_ref(repository: Path, ref: str) -> None:
    if git_ref_exists(repository, ref):
        git(repository, "update-ref", "-d", ref)


def restore_clean_worktree(worktree: Path) -> None:
    git(worktree, "reset", "--hard", "HEAD")
    git(worktree, "clean", "-fd")


#: Shared checkouts that several coding runs use at once, each with its own lock: two
#: agents work in separate worktrees, but fetches, worktree changes and branch deletes
#: on the shared repository must not overlap.
_REPOSITORY_LOCKS: dict[str, threading.Lock] = {}


def share_repository(path: Path) -> None:
    _REPOSITORY_LOCKS.setdefault(str(path), threading.Lock())


def git(directory: Path, *arguments: str, timeout: int = 60) -> str:
    lock = _REPOSITORY_LOCKS.get(str(directory))
    try:
        if lock is None:
            completed = _run_git(directory, arguments, timeout)
        else:
            with lock:
                completed = _run_git(directory, arguments, timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CodingAgentError(f"Git operation failed: {arguments[0]}") from error
    if completed.returncode != 0:
        raise CodingAgentError(f"Git operation failed: {arguments[0]}")
    return completed.stdout


def _run_git(
    directory: Path, arguments: Sequence[str], timeout: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(directory), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def safe_ref(value: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_/"
    if not value or any(character not in allowed for character in value):
        raise ValueError("Git names may contain only letters, numbers, slash, dash, and underscore")
    if value.startswith("/") or value.endswith("/") or ".." in value or "//" in value:
        raise ValueError("Invalid Git name")
    return value
