from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.coding_runs import CodingRunPhase, CodingRunStore
from app.execution.coding import (
    CodeAgentReport,
    CodingAgentError,
    CodingPullRequestExecutor,
    FallbackCodeAgentRunner,
    RepositoryCodingExecutor,
    _text_report,
)
from app.integrations.github import GitHubPullRequest
from app.persistence.database import Database
from app.providers import DatabaseProviderStateStore
from app.service import TaskService
from app.validation import ValidationProfile, ValidationStep


def _git(directory: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(directory), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    (repository / "README.md").write_text("before\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "Initial",
    )
    _git(repository, "remote", "add", "origin", str(remote))
    _git(repository, "push", "-u", "origin", "main")
    return repository, remote


def test_coding_executor_isolates_changes_pushes_branch_and_opens_draft(tmp_path: Path) -> None:
    repository, remote = _repository(tmp_path)

    class Agent:
        provider = "test-agent"

        def run(self, *, worktree: Path, **_kwargs) -> CodeAgentReport:
            (worktree / "feature.txt").write_text("implemented\n", encoding="utf-8")
            return CodeAgentReport(summary="Implemented feature", tests=["pytest passed"])

    class GitHub:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create_pull_request(self, **kwargs) -> GitHubPullRequest:
            self.calls.append(kwargs)
            sha = _git(repository, "rev-parse", f"origin/{kwargs['head']}")
            return GitHubPullRequest(
                repository="acme/widget",
                number=9,
                url="https://github.com/acme/widget/pull/9",
                head_branch=str(kwargs["head"]),
                head_sha=sha,
                base_branch="main",
                state="open",
                draft=True,
                merged=False,
            )

    github = GitHub()
    executor = CodingPullRequestExecutor(
        repository_path=repository,
        worktree_root=tmp_path / "worktrees",
        github_repository="acme/widget",
        github=github,  # type: ignore[arg-type]
        agent=Agent(),
    )

    result = executor.execute(
        task_id="12345678-1234-1234-1234-123456789abc",
        request="Implement feature",
        is_cancelled=lambda: False,
    )

    branch = "assistant/task-123456781234"
    remote_sha = _git(remote, "rev-parse", f"refs/heads/{branch}")
    assert remote_sha == result.output["approval_request"]["expected_head_sha"]
    assert result.output["agent_provider"] == "test-agent"
    assert result.output["artifacts"][0]["draft"] is True
    assert not (tmp_path / "worktrees" / "12345678-1234-1234-1234-123456789abc").exists()
    assert not (repository / "feature.txt").exists()
    assert github.calls[0]["draft"] is True


def test_coding_executor_recovers_push_without_duplicate_agent_or_pr(
    tmp_path: Path, database: Database
) -> None:
    repository, _remote = _repository(tmp_path)
    task = TaskService(database).create_task(request="Implement recoverable feature")
    calls = {"agent": 0, "create": 0, "find": 0}

    class Agent:
        provider = "test-agent"

        def run(self, *, worktree: Path, **_kwargs) -> CodeAgentReport:
            calls["agent"] += 1
            (worktree / "feature.txt").write_text("implemented\n", encoding="utf-8")
            return CodeAgentReport(summary="Implemented recoverably")

    class GitHub:
        def create_pull_request(self, **kwargs) -> GitHubPullRequest:
            calls["create"] += 1
            if calls["create"] == 1:
                raise RuntimeError("simulated crash after push")
            sha = _git(repository, "rev-parse", f"origin/{kwargs['head']}")
            return GitHubPullRequest(
                repository="acme/widget",
                number=9,
                url="https://github.com/acme/widget/pull/9",
                head_branch=str(kwargs["head"]),
                head_sha=sha,
                base_branch="main",
                state="open",
                draft=True,
                merged=False,
            )

        def find_open_pull_request(self, **_kwargs):
            calls["find"] += 1
            return None

        def get_pull_request(self, **_kwargs):
            raise AssertionError("No completed PR checkpoint exists yet")

    executor = CodingPullRequestExecutor(
        repository_path=repository,
        worktree_root=tmp_path / "worktrees",
        github_repository="acme/widget",
        github=GitHub(),  # type: ignore[arg-type]
        agent=Agent(),
        repository_id="widget",
        checkpoint_store=CodingRunStore(database),
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        executor.execute(
            task_id=task.id,
            request=task.original_request,
            is_cancelled=lambda: False,
        )
    assert CodingRunStore(database).get(task.id).phase == CodingRunPhase.PUSHED.value

    result = executor.execute(
        task_id=task.id,
        request=task.original_request,
        is_cancelled=lambda: False,
    )

    assert result.output["approval_request"]["number"] == 9
    assert calls == {"agent": 1, "create": 2, "find": 1}
    assert CodingRunStore(database).get(task.id).phase == CodingRunPhase.PR_CREATED.value


def test_failed_validation_output_is_truncated_persisted_and_exposed(
    tmp_path: Path, database: Database
) -> None:
    repository, _remote = _repository(tmp_path)
    task = TaskService(database).create_task(request="Implement invalid feature")

    class Agent:
        provider = "test-agent"

        def run(self, *, worktree: Path, **_kwargs) -> CodeAgentReport:
            (worktree / "feature.txt").write_text("invalid\n", encoding="utf-8")
            return CodeAgentReport(summary="Implemented invalid feature")

    profile = ValidationProfile(
        id="test-validation",
        name="Test validation",
        steps=(
            ValidationStep(
                id="failing-check",
                command=(sys.executable, "-c", "print('x' * 6000); raise SystemExit(2)"),
                timeout_seconds=30,
            ),
        ),
    )
    executor = CodingPullRequestExecutor(
        repository_path=repository,
        worktree_root=tmp_path / "worktrees",
        github_repository="acme/widget",
        github=object(),  # type: ignore[arg-type]
        agent=Agent(),  # type: ignore[arg-type]
        repository_id="widget",
        checkpoint_store=CodingRunStore(database),
        validation_profile=profile,
    )

    with pytest.raises(CodingAgentError, match="Required validation step failed"):
        executor.execute(
            task_id=task.id,
            request=task.original_request,
            is_cancelled=lambda: False,
        )

    checkpoint = CodingRunStore(database).get(task.id)
    assert checkpoint is not None
    assert checkpoint.phase == CodingRunPhase.VALIDATION_FAILED.value
    assert len(checkpoint.validation_results[0]["output"]) == 4000
    context = TaskService(database).task_context(task.id)
    assert context["coding_checkpoint"]["validation"] == checkpoint.validation_results


def test_coding_runner_falls_back_from_quota_limit_with_clean_worktree(tmp_path: Path) -> None:
    repository, _remote = _repository(tmp_path)

    class LimitedAgent:
        provider = "limited"

        def run(self, *, worktree: Path, **_kwargs) -> CodeAgentReport:
            (worktree / "partial.txt").write_text("must not leak\n", encoding="utf-8")
            raise CodingAgentError("limit reached", category="quota_exhausted")

    class BackupAgent:
        provider = "backup"

        def run(self, *, worktree: Path, **_kwargs) -> CodeAgentReport:
            assert not (worktree / "partial.txt").exists()
            (worktree / "complete.txt").write_text("done\n", encoding="utf-8")
            return CodeAgentReport(summary="Completed with fallback")

    chain = FallbackCodeAgentRunner([LimitedAgent(), BackupAgent()])
    report = chain.run(
        worktree=repository,
        request="Implement feature",
        timeout_seconds=60,
        is_cancelled=lambda: False,
    )

    assert chain.provider == "backup"
    assert [attempt.outcome for attempt in chain.attempts] == ["failed", "succeeded"]
    assert chain.attempts[0].category == "quota_exhausted"
    assert "limited=failed(quota_exhausted)" in report.notes[-1]
    assert not (repository / "partial.txt").exists()
    assert (repository / "complete.txt").exists()


def test_coding_runner_cooldown_survives_runner_recreation(
    tmp_path: Path,
    database: Database,
) -> None:
    repository, _remote = _repository(tmp_path)
    calls = {"limited": 0, "backup": 0}

    class LimitedAgent:
        provider = "limited"

        def run(self, **_kwargs) -> CodeAgentReport:
            calls["limited"] += 1
            raise CodingAgentError("rate limited", category="rate_limited")

    class BackupAgent:
        provider = "backup"

        def run(self, **_kwargs) -> CodeAgentReport:
            calls["backup"] += 1
            return CodeAgentReport(summary="fallback succeeded")

    def clock() -> datetime:
        return datetime(2026, 9, 21, 12, 0, tzinfo=UTC)

    first = FallbackCodeAgentRunner(
        [LimitedAgent(), BackupAgent()],
        state_store=DatabaseProviderStateStore(database, clock=clock),
    )
    first.run(
        worktree=repository,
        request="Implement feature",
        timeout_seconds=60,
        is_cancelled=lambda: False,
    )

    second = FallbackCodeAgentRunner(
        [LimitedAgent(), BackupAgent()],
        state_store=DatabaseProviderStateStore(database, clock=clock),
    )
    second.run(
        worktree=repository,
        request="Implement another feature",
        timeout_seconds=60,
        is_cancelled=lambda: False,
    )

    assert calls == {"limited": 1, "backup": 2}
    assert second.attempts[0].outcome == "skipped"
    assert second.attempts[0].category == "cooldown"


def test_text_report_normalizes_kimi_fenced_json() -> None:
    report = _text_report(
        """• ```json
  {
    "status": "success",
    "summary": "Created the requested documentation.",
    "files_changed": ["docs/example.md"],
    "tests_run": false,
    "tests_note": "Documentation-only change; no tests applicable."
  }
  ```""",
        "Kimi completed the change",
    )

    assert report.summary == "Created the requested documentation."
    assert report.tests == ["Documentation-only change; no tests applicable."]
    assert report.notes == ["Files changed: docs/example.md"]


def test_repository_coding_executor_routes_by_trusted_repository_id() -> None:
    calls: list[str] = []

    class Executor:
        def __init__(self, repository_id: str):
            self.repository_id = repository_id
            self.github = object()

        def execute(self, **_kwargs):
            calls.append(self.repository_id)
            from app.execution.base import ExecutionResult

            return ExecutionResult(output={"summary": "done"})

    router = RepositoryCodingExecutor(
        {
            "personal-assistant": Executor("personal-assistant"),  # type: ignore[dict-item]
            "analytics-agent-playground": Executor(  # type: ignore[dict-item]
                "analytics-agent-playground"
            ),
        },
        default_repository_id="personal-assistant",
    )

    result = router.execute(
        task_id="task",
        request="change",
        is_cancelled=lambda: False,
        context={"repository_id": "analytics-agent-playground"},
    )

    assert calls == ["analytics-agent-playground"]
    assert result.output["repository_id"] == "analytics-agent-playground"
