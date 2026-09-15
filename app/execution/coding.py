from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.execution.base import ConversationTurn, ExecutionResult
from app.execution.fake import ExecutionCancelled
from app.integrations.github import GitHubClient, GitHubPullRequest

logger = logging.getLogger(__name__)


class CodingAgentError(RuntimeError):
    """A safe coding-workflow failure suitable for task audit events."""


class CodeAgentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=4000)
    tests: list[str] = Field(default_factory=list, max_length=50)
    notes: list[str] = Field(default_factory=list, max_length=50)


_CODE_AGENT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "tests": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "tests", "notes"],
    "additionalProperties": False,
}


class CodeAgentRunner(Protocol):
    provider: str

    def run(
        self,
        *,
        worktree: Path,
        request: str,
        timeout_seconds: int,
        is_cancelled: Callable[[], bool],
    ) -> CodeAgentReport: ...


class CodexCliRunner:
    """Runs Codex non-interactively with workspace-only write access."""

    provider = "codex-cli"

    def __init__(self, *, executable: str = "codex", model: str | None = None):
        self.executable = executable
        self.model = model

    def run(
        self,
        *,
        worktree: Path,
        request: str,
        timeout_seconds: int,
        is_cancelled: Callable[[], bool],
    ) -> CodeAgentReport:
        with tempfile.TemporaryDirectory(prefix="assistant-codex-") as temporary:
            temporary_path = Path(temporary)
            schema_path = temporary_path / "output-schema.json"
            output_path = temporary_path / "last-message.json"
            stdout_path = temporary_path / "stdout.jsonl"
            stderr_path = temporary_path / "stderr.log"
            schema_path.write_text(json.dumps(_CODE_AGENT_OUTPUT_SCHEMA), encoding="utf-8")
            command = [
                self.executable,
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--sandbox",
                "workspace-write",
                "--color",
                "never",
                "--json",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "--cd",
                str(worktree),
            ]
            if self.model:
                command.extend(["--model", self.model])
            command.append("-")
            prompt = _coding_prompt(request)
            try:
                with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.PIPE,
                        stdout=stdout,
                        stderr=stderr,
                        env=_safe_agent_environment(),
                    )
                    assert process.stdin is not None
                    process.stdin.write(prompt.encode())
                    process.stdin.close()
                    deadline = time.monotonic() + timeout_seconds
                    while process.poll() is None:
                        if is_cancelled():
                            process.terminate()
                            _wait_or_kill(process)
                            raise ExecutionCancelled("Coding task was cancelled")
                        if time.monotonic() >= deadline:
                            process.terminate()
                            _wait_or_kill(process)
                            raise CodingAgentError("Coding agent timed out")
                        time.sleep(0.2)
            except FileNotFoundError as error:
                raise CodingAgentError("Codex CLI executable was not found") from error
            if process.returncode != 0:
                raise CodingAgentError(f"Codex CLI exited with status {process.returncode}")
            try:
                return CodeAgentReport.model_validate_json(output_path.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as error:
                raise CodingAgentError("Codex CLI returned an invalid completion report") from error


class CodingPullRequestExecutor:
    """Runs a code agent in an isolated worktree and publishes a draft pull request."""

    id = "coding-pull-request"

    def __init__(
        self,
        *,
        repository_path: Path,
        worktree_root: Path,
        github_repository: str,
        github: GitHubClient,
        agent: CodeAgentRunner,
        base_branch: str = "main",
        remote: str = "origin",
        timeout_seconds: int = 1800,
        draft_pull_requests: bool = True,
    ):
        self.repository_path = repository_path.resolve()
        self.worktree_root = worktree_root.resolve()
        self.github_repository = github_repository
        self.github = github
        self.agent = agent
        self.base_branch = _safe_ref(base_branch)
        self.remote = _safe_ref(remote)
        self.timeout_seconds = timeout_seconds
        self.draft_pull_requests = draft_pull_requests

    def execute(
        self,
        *,
        task_id: str,
        request: str,
        is_cancelled: Callable[[], bool],
        history: Sequence[ConversationTurn] = (),
        context: Mapping[str, Any] | None = None,
    ) -> ExecutionResult:
        del history, context
        if not (self.repository_path / ".git").exists():
            raise CodingAgentError("Configured coding repository is not a Git repository")
        branch = f"assistant/task-{task_id.replace('-', '')[:12]}"
        worktree = self.worktree_root / task_id
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        if worktree.exists():
            raise CodingAgentError("Task worktree already exists")
        created = False
        try:
            _git(
                self.repository_path,
                "fetch",
                "--no-tags",
                self.remote,
                self.base_branch,
                timeout=120,
            )
            _git(
                self.repository_path,
                "worktree",
                "add",
                "-b",
                branch,
                str(worktree),
                f"{self.remote}/{self.base_branch}",
            )
            created = True
            report = self.agent.run(
                worktree=worktree,
                request=request,
                timeout_seconds=self.timeout_seconds,
                is_cancelled=is_cancelled,
            )
            if is_cancelled():
                raise ExecutionCancelled("Coding task was cancelled")
            if not _git(worktree, "status", "--porcelain").strip():
                raise CodingAgentError("Coding agent completed without repository changes")
            _git(worktree, "diff", "--check")
            _git(worktree, "add", "-A")
            _git(
                worktree,
                "-c",
                "user.name=Personal Assistant Agent",
                "-c",
                "user.email=assistant-agent@localhost",
                "commit",
                "-m",
                f"Agent task {task_id[:12]}",
            )
            commit_sha = _git(worktree, "rev-parse", "HEAD").strip()
            _git(worktree, "push", self.remote, f"HEAD:refs/heads/{branch}", timeout=120)
            pull_request = self.github.create_pull_request(
                repository=self.github_repository,
                title=_pull_request_title(request),
                head=branch,
                base=self.base_branch,
                body=_pull_request_body(task_id, report),
                draft=self.draft_pull_requests,
            )
            return ExecutionResult(
                output=_coding_output(report, pull_request, commit_sha, self.agent.provider)
            )
        finally:
            if created:
                try:
                    _git(self.repository_path, "worktree", "remove", "--force", str(worktree))
                except CodingAgentError:
                    # Do not turn a successfully published PR into a retryable task failure.
                    logger.exception("Could not remove task worktree %s", worktree)


def _coding_prompt(request: str) -> str:
    return (
        "Implement the requested repository change. Work only inside this checkout. "
        "Inspect existing instructions, preserve unrelated work, run proportionate tests, "
        "and leave "
        "all intended edits in the worktree. Do not commit, push, create a pull request, or merge; "
        "the trusted host performs those steps. Your final response must match the supplied JSON "
        "schema.\n\nTASK REQUEST\n"
        f"{request}\nEND TASK REQUEST\n"
    )


def _safe_agent_environment() -> dict[str, str]:
    allowed = {
        "CODEX_HOME",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "PATH",
        "SHELL",
        "SSL_CERT_FILE",
        "TERM",
        "TMPDIR",
        "USER",
    }
    return {name: value for name, value in os.environ.items() if name in allowed}


def _wait_or_kill(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _git(directory: Path, *arguments: str, timeout: int = 60) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(directory), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CodingAgentError(f"Git operation failed: {arguments[0]}") from error
    if completed.returncode != 0:
        raise CodingAgentError(f"Git operation failed: {arguments[0]}")
    return completed.stdout


def _safe_ref(value: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_/"
    if not value or any(character not in allowed for character in value):
        raise ValueError("Git names may contain only letters, numbers, slash, dash, and underscore")
    if value.startswith("/") or value.endswith("/") or ".." in value or "//" in value:
        raise ValueError("Invalid Git name")
    return value


def _pull_request_title(request: str) -> str:
    normalized = " ".join(request.split())
    return (normalized[:69] + "…") if len(normalized) > 70 else normalized


def _pull_request_body(task_id: str, report: CodeAgentReport) -> str:
    tests = "\n".join(f"- {item}" for item in report.tests) or "- Not reported"
    notes = "\n".join(f"- {item}" for item in report.notes) or "- None"
    return (
        f"## Assistant task\n\n`{task_id}`\n\n"
        f"## Summary\n\n{report.summary}\n\n"
        f"## Tests\n\n{tests}\n\n"
        f"## Notes\n\n{notes}\n"
    )[:20_000]


def _coding_output(
    report: CodeAgentReport,
    pull_request: GitHubPullRequest,
    commit_sha: str,
    agent_provider: str,
) -> dict[str, Any]:
    artifact = {
        "type": "github_pull_request",
        "repository": pull_request.repository,
        "number": pull_request.number,
        "url": pull_request.url,
        "head_branch": pull_request.head_branch,
        "head_sha": pull_request.head_sha,
        "base_branch": pull_request.base_branch,
        "draft": pull_request.draft,
        "commit_sha": commit_sha,
    }
    return {
        "summary": report.summary,
        "reply": (
            f"I created pull request #{pull_request.number}. Review it before approving merge."
        ),
        "emotion": "Warm",
        "provider": "coding-agent",
        "agent_provider": agent_provider,
        "tests": report.tests,
        "notes": report.notes,
        "artifacts": [artifact],
        "approval_request": {
            "type": "github_pull_request_merge",
            "repository": pull_request.repository,
            "number": pull_request.number,
            "url": pull_request.url,
            "expected_head_sha": pull_request.head_sha,
            "draft": pull_request.draft,
        },
    }
