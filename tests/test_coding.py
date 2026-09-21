from __future__ import annotations

import subprocess
from pathlib import Path

from app.execution.coding import (
    CodeAgentReport,
    CodingAgentError,
    CodingPullRequestExecutor,
    FallbackCodeAgentRunner,
    _text_report,
)
from app.integrations.github import GitHubPullRequest


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
            sha = _git(repository, "rev-parse", kwargs["head"])
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
