from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.execution.base import ConversationTurn, ExecutionResult
from app.execution.fake import ExecutionCancelled
from app.integrations.github import GitHubClient, GitHubPullRequest

logger = logging.getLogger(__name__)


class CodingAgentError(RuntimeError):
    """A safe coding-workflow failure suitable for task audit events."""

    def __init__(self, message: str, *, category: str = "execution_failed"):
        super().__init__(message)
        self.category = category


class CodeAgentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=4000)
    tests: list[str] = Field(default_factory=list, max_length=50)
    notes: list[str] = Field(default_factory=list, max_length=50)


@dataclass(frozen=True, slots=True)
class RunnerAttempt:
    provider: str
    outcome: str
    category: str | None = None


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
                diagnostic = _read_diagnostic(stderr_path, stdout_path)
                raise CodingAgentError(
                    f"Codex CLI exited with status {process.returncode}",
                    category=_failure_category(diagnostic),
                )
            try:
                return CodeAgentReport.model_validate_json(output_path.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as error:
                raise CodingAgentError("Codex CLI returned an invalid completion report") from error


class KimiCodeCliRunner:
    """Runs Kimi Code in its documented non-interactive auto-approval mode."""

    provider = "kimi-code"

    def __init__(self, *, executable: str = "kimi", model: str | None = None):
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
        command = [self.executable]
        if self.model:
            command.extend(["--model", self.model])
        command.extend(["--prompt", _coding_prompt(request), "--output-format", "text"])
        output = _run_code_agent_process(
            command=command,
            worktree=worktree,
            timeout_seconds=timeout_seconds,
            is_cancelled=is_cancelled,
            environment=_safe_agent_environment(),
            display_name="Kimi Code CLI",
        )
        return _text_report(output, "Kimi Code completed the requested repository change")


class ClaudeCodeMiniMaxRunner:
    """Runs Claude Code against MiniMax's Anthropic-compatible endpoint."""

    provider = "minimax-claude-code"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        executable: str = "claude",
        model: str | None = None,
    ):
        if not api_key:
            raise ValueError("A MiniMax API key is required for Claude Code")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
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
        command = [
            self.executable,
            "--print",
            "--dangerously-skip-permissions",
            "--output-format",
            "json",
        ]
        if self.model:
            command.extend(["--model", self.model])
        command.append(_coding_prompt(request))
        environment = _safe_agent_environment()
        environment.update(
            {
                "ANTHROPIC_AUTH_TOKEN": self.api_key,
                "ANTHROPIC_BASE_URL": self.base_url,
            }
        )
        output = _run_code_agent_process(
            command=command,
            worktree=worktree,
            timeout_seconds=timeout_seconds,
            is_cancelled=is_cancelled,
            environment=environment,
            display_name="Claude Code with MiniMax",
        )
        try:
            body = json.loads(output)
            result = body.get("result") if isinstance(body, dict) else None
        except ValueError:
            result = None
        return _text_report(
            result if isinstance(result, str) else output,
            "Claude Code with MiniMax completed the requested repository change",
        )


class FallbackCodeAgentRunner:
    """Tries coding runners in order, cooling down limited providers between tasks."""

    def __init__(
        self,
        runners: Sequence[CodeAgentRunner],
        *,
        rate_limit_cooldown_seconds: int = 300,
        quota_cooldown_seconds: int = 3600,
    ):
        if not runners:
            raise ValueError("At least one coding runner is required")
        self.runners = tuple(runners)
        self.rate_limit_cooldown_seconds = rate_limit_cooldown_seconds
        self.quota_cooldown_seconds = quota_cooldown_seconds
        self._cooldowns: dict[str, float] = {}
        self.attempts: tuple[RunnerAttempt, ...] = ()
        self.provider = "->".join(runner.provider for runner in runners)

    def run(
        self,
        *,
        worktree: Path,
        request: str,
        timeout_seconds: int,
        is_cancelled: Callable[[], bool],
    ) -> CodeAgentReport:
        deadline = time.monotonic() + timeout_seconds
        attempts: list[RunnerAttempt] = []
        failures: list[str] = []
        for runner in self.runners:
            if self._cooldowns.get(runner.provider, 0) > time.monotonic():
                attempts.append(RunnerAttempt(runner.provider, "skipped", "cooldown"))
                continue
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                break
            try:
                report = runner.run(
                    worktree=worktree,
                    request=request,
                    timeout_seconds=remaining,
                    is_cancelled=is_cancelled,
                )
            except ExecutionCancelled:
                raise
            except CodingAgentError as error:
                attempts.append(RunnerAttempt(runner.provider, "failed", error.category))
                failures.append(f"{runner.provider}: {error.category}")
                if error.category in {"rate_limited", "quota_exhausted"}:
                    cooldown = (
                        self.quota_cooldown_seconds
                        if error.category == "quota_exhausted"
                        else self.rate_limit_cooldown_seconds
                    )
                    self._cooldowns[runner.provider] = time.monotonic() + cooldown
                _restore_clean_worktree(worktree)
                continue
            attempts.append(RunnerAttempt(runner.provider, "succeeded"))
            self.attempts = tuple(attempts)
            self.provider = runner.provider
            report.notes.append(
                "Runner attempts: "
                + ", ".join(
                    f"{attempt.provider}={attempt.outcome}"
                    + (f"({attempt.category})" if attempt.category else "")
                    for attempt in attempts
                )
            )
            return report
        self.attempts = tuple(attempts)
        detail = ", ".join(failures) or "all providers are cooling down"
        raise CodingAgentError(f"Coding runner chain exhausted: {detail}")


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


def _run_code_agent_process(
    *,
    command: list[str],
    worktree: Path,
    timeout_seconds: int,
    is_cancelled: Callable[[], bool],
    environment: Mapping[str, str],
    display_name: str,
) -> str:
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
                deadline = time.monotonic() + timeout_seconds
                while process.poll() is None:
                    if is_cancelled():
                        process.terminate()
                        _wait_or_kill(process)
                        raise ExecutionCancelled("Coding task was cancelled")
                    if time.monotonic() >= deadline:
                        process.terminate()
                        _wait_or_kill(process)
                        raise CodingAgentError(
                            f"{display_name} timed out", category="timeout"
                        )
                    time.sleep(0.2)
        except FileNotFoundError as error:
            raise CodingAgentError(
                f"{display_name} executable was not found", category="unavailable"
            ) from error
        if process.returncode != 0:
            diagnostic = _read_diagnostic(stderr_path, stdout_path)
            raise CodingAgentError(
                f"{display_name} exited with status {process.returncode}",
                category=_failure_category(diagnostic),
            )
        return stdout_path.read_text(encoding="utf-8", errors="replace")


def _read_diagnostic(*paths: Path) -> str:
    chunks: list[str] = []
    for path in paths:
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace")[-16_000:])
        except OSError:
            continue
    return "\n".join(chunks)


def _failure_category(diagnostic: str) -> str:
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


def _text_report(output: str, fallback_summary: str) -> CodeAgentReport:
    normalized = output.strip()
    structured = _embedded_json_object(normalized)
    if structured is not None:
        summary = structured.get("summary")
        if isinstance(summary, str) and summary.strip():
            tests = structured.get("tests")
            normalized_tests = (
                [str(item)[:500] for item in tests if str(item).strip()]
                if isinstance(tests, list)
                else []
            )
            tests_note = structured.get("tests_note")
            if not normalized_tests and isinstance(tests_note, str) and tests_note.strip():
                normalized_tests.append(tests_note.strip()[:500])
            notes = structured.get("notes")
            normalized_notes = (
                [str(item)[:500] for item in notes if str(item).strip()]
                if isinstance(notes, list)
                else []
            )
            files = structured.get("files_changed")
            if isinstance(files, list) and files:
                normalized_notes.append(
                    "Files changed: " + ", ".join(str(item) for item in files)[:1000]
                )
            return CodeAgentReport(
                summary=summary.strip()[:4000],
                tests=normalized_tests[:50],
                notes=normalized_notes[:50],
            )
    summary = normalized[-4000:] if normalized else fallback_summary
    return CodeAgentReport(summary=summary)


def _embedded_json_object(output: str) -> dict[str, Any] | None:
    candidates = [output]
    candidates.extend(
        match.group(1)
        for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", output, re.DOTALL)
    )
    for candidate in reversed(candidates):
        try:
            value = json.loads(candidate.strip())
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _restore_clean_worktree(worktree: Path) -> None:
    _git(worktree, "reset", "--hard", "HEAD")
    _git(worktree, "clean", "-fd")


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
