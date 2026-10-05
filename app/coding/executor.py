"""Runs a coding agent in an isolated worktree and publishes a draft pull request."""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from app.coding.agent_session import GuidanceSource, ProgressCallback
from app.coding.base import (
    CodeAgentReport,
    CodeAgentRunner,
    CodingAgentError,
    RunnerAttempt,
    safe_agent_environment,
)
from app.coding.git import delete_git_ref, git, git_ref_exists, safe_ref, share_repository
from app.coding.process import optional, report_milestone
from app.coding.reports import (
    coding_output,
    failed_required_step,
    pull_request_body,
    pull_request_title,
    repair_agent_request,
    revision_agent_request,
    validation_failure_reply,
    with_work_item_context,
)
from app.coding.runs import CodingRunPhase, CodingRunStore
from app.execution.base import ConversationTurn, ExecutionResult
from app.execution.fake import ExecutionCancelled
from app.integrations.github import GitHubClient, GitHubPullRequest
from app.validation import ValidationProfile

logger = logging.getLogger(__name__)


class RepositoryCodingExecutor:
    """Routes coding work to a trusted, preconfigured repository executor."""

    id = "coding-pull-request"

    def __init__(
        self,
        executors: Mapping[str, CodingPullRequestExecutor],
        *,
        default_repository_id: str | None = None,
    ):
        if not executors:
            raise ValueError("At least one configured repository is required")
        self.executors = dict(executors)
        self.github = next(iter(self.executors.values())).github
        if default_repository_id is not None and default_repository_id not in self.executors:
            raise ValueError(f"Default repository is not configured: {default_repository_id}")
        self.default_repository_id = default_repository_id

    def execute(
        self,
        *,
        task_id: str,
        request: str,
        is_cancelled: Callable[[], bool],
        history: Sequence[ConversationTurn] = (),
        context: Mapping[str, Any] | None = None,
    ) -> ExecutionResult:
        repository_id = str((context or {}).get("repository_id") or "").strip().lower()
        if not repository_id:
            repository_id = self.default_repository_id or ""
        if not repository_id and len(self.executors) == 1:
            repository_id = next(iter(self.executors))
        executor = self.executors.get(repository_id)
        if executor is None:
            available = ", ".join(sorted(self.executors))
            if repository_id:
                raise CodingAgentError(
                    f"Repository is not configured for coding: {repository_id}. "
                    f"Available: {available}"
                )
            raise CodingAgentError(f"A repository_id is required. Available: {available}")
        result = executor.execute(
            task_id=task_id,
            request=request,
            is_cancelled=is_cancelled,
            history=history,
            context=context,
        )
        return ExecutionResult(output={**result.output, "repository_id": repository_id})


def _runner_attempt_payload(agent: CodeAgentRunner) -> list[dict[str, str | None]]:
    attempts = getattr(agent, "attempts", ())
    return [
        {
            "provider": attempt.provider,
            "outcome": attempt.outcome,
            "category": attempt.category,
        }
        for attempt in attempts
        if isinstance(attempt, RunnerAttempt)
    ]


def _checkpoint_provider(attempts: list[dict[str, Any]], fallback: str) -> str:
    for attempt in reversed(attempts):
        if attempt.get("outcome") == "succeeded" and isinstance(attempt.get("provider"), str):
            return str(attempt["provider"])
    return fallback


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
        validation_profile: ValidationProfile | None = None,
        repository_id: str = "default",
        checkpoint_store: CodingRunStore | None = None,
    ):
        self.repository_path = repository_path.resolve()
        share_repository(self.repository_path)
        self.worktree_root = worktree_root.resolve()
        self.github_repository = github_repository
        self.github = github
        self.agent = agent
        self.base_branch = safe_ref(base_branch)
        self.remote = safe_ref(remote)
        self.timeout_seconds = timeout_seconds
        self.draft_pull_requests = draft_pull_requests
        self.validation_profile = validation_profile
        self.repository_id = repository_id
        self.checkpoint_store = checkpoint_store

    def execute(
        self,
        *,
        task_id: str,
        request: str,
        is_cancelled: Callable[[], bool],
        history: Sequence[ConversationTurn] = (),
        context: Mapping[str, Any] | None = None,
    ) -> ExecutionResult:
        del history
        progress: ProgressCallback | None = (context or {}).get("progress")
        guidance: GuidanceSource | None = (context or {}).get("guidance")
        if not (self.repository_path / ".git").exists():
            raise CodingAgentError("Configured coding repository is not a Git repository")
        revision = dict((context or {}).get("revision_pull_request") or {})
        branch = (
            safe_ref(str(revision["head_branch"]))
            if revision
            else f"assistant/task-{task_id.replace('-', '')[:12]}"
        )
        checkpoint_ref = f"refs/assistant/checkpoints/{task_id.replace('-', '')}"
        worktree = self.worktree_root / task_id
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        checkpoint = (
            self.checkpoint_store.start(
                task_id=task_id,
                repository_id=self.repository_id,
                github_repository=self.github_repository,
                branch=branch,
            )
            if self.checkpoint_store is not None
            else None
        )
        if checkpoint is not None and checkpoint.phase == CodingRunPhase.PR_CREATED.value:
            pull_request = self.github.get_pull_request(
                repository=self.github_repository,
                number=int(checkpoint.pull_request_number),
            )
            _require_expected_pull_request(
                pull_request,
                repository=self.github_repository,
                branch=branch,
                base_branch=self.base_branch,
                expected_sha=str(checkpoint.pull_request_head_sha),
            )
            delete_git_ref(self.repository_path, checkpoint_ref)
            report = CodeAgentReport.model_validate(checkpoint.report)
            return ExecutionResult(
                output=coding_output(
                    report,
                    pull_request,
                    str(checkpoint.commit_sha),
                    _checkpoint_provider(checkpoint.runner_attempts, self.agent.provider),
                    validation=checkpoint.validation_results,
                )
            )
        created = False
        try:
            if worktree.exists():
                try:
                    git(self.repository_path, "worktree", "remove", "--force", str(worktree))
                except CodingAgentError as error:
                    raise CodingAgentError(
                        "Stale task worktree requires operator cleanup",
                        category="reconciliation_required",
                    ) from error
            if (
                not revision
                and (checkpoint is None or checkpoint.phase != CodingRunPhase.COMMITTED.value)
                and git_ref_exists(self.repository_path, f"refs/heads/{branch}")
            ):
                git(self.repository_path, "branch", "-D", branch)
            git(
                self.repository_path,
                "fetch",
                "--no-tags",
                self.remote,
                branch if revision else self.base_branch,
                timeout=120,
            )
            start_ref = f"{self.remote}/{branch if revision else self.base_branch}"
            base_sha = git(self.repository_path, "rev-parse", start_ref).strip()
            if checkpoint is not None and checkpoint.base_sha is None:
                checkpoint = self.checkpoint_store.update(
                    task_id, CodingRunPhase.PREPARING, base_sha=base_sha
                )
            if checkpoint is not None and checkpoint.phase == CodingRunPhase.COMMITTED.value:
                if not checkpoint.commit_sha:
                    raise CodingAgentError("Committed checkpoint has no commit SHA")
                git(
                    self.repository_path,
                    "push",
                    self.remote,
                    f"{checkpoint.commit_sha}:refs/heads/{branch}",
                    timeout=120,
                )
                checkpoint = self.checkpoint_store.update(task_id, CodingRunPhase.PUSHED)
            if checkpoint is not None and checkpoint.phase == CodingRunPhase.PUSHED.value:
                git(
                    self.repository_path,
                    "fetch",
                    "--no-tags",
                    self.remote,
                    branch,
                    timeout=120,
                )
                remote_sha = git(
                    self.repository_path, "rev-parse", f"{self.remote}/{branch}"
                ).strip()
                if remote_sha != checkpoint.commit_sha:
                    raise CodingAgentError(
                        "Remote branch diverged from the coding checkpoint",
                        category="reconciliation_required",
                    )
                report = CodeAgentReport.model_validate(checkpoint.report)
                pull_request = (
                    self.github.get_pull_request(
                        repository=self.github_repository,
                        number=int(revision["number"]),
                    )
                    if revision
                    else self.github.find_open_pull_request(
                        repository=self.github_repository, head_branch=branch
                    )
                )
                if pull_request is None:
                    pull_request = self.github.create_pull_request(
                        repository=self.github_repository,
                        title=pull_request_title(request),
                        head=branch,
                        base=self.base_branch,
                        body=pull_request_body(task_id, report),
                        draft=self.draft_pull_requests,
                    )
                _require_expected_pull_request(
                    pull_request,
                    repository=self.github_repository,
                    branch=branch,
                    base_branch=self.base_branch,
                    expected_sha=str(checkpoint.commit_sha),
                )
                checkpoint = self.checkpoint_store.update(
                    task_id,
                    CodingRunPhase.PR_CREATED,
                    pull_request_number=pull_request.number,
                    pull_request_url=pull_request.url,
                    pull_request_head_sha=pull_request.head_sha,
                )
                delete_git_ref(self.repository_path, checkpoint_ref)
                return ExecutionResult(
                    output=coding_output(
                        report,
                        pull_request,
                        str(checkpoint.commit_sha),
                        _checkpoint_provider(checkpoint.runner_attempts, self.agent.provider),
                        validation=checkpoint.validation_results,
                    )
                )
            if not revision:
                remote_branch = git(
                    self.repository_path,
                    "ls-remote",
                    "--heads",
                    self.remote,
                    f"refs/heads/{branch}",
                ).strip()
                if remote_branch:
                    raise CodingAgentError(
                        "Deterministic task branch already exists without a matching checkpoint",
                        category="reconciliation_required",
                    )
            if revision:
                actual_sha = git(self.repository_path, "rev-parse", start_ref).strip()
                if actual_sha != revision.get("expected_head_sha"):
                    raise CodingAgentError(
                        "Pull request head changed before revision started",
                        category="stale_revision",
                    )
                git(self.repository_path, "worktree", "add", "--detach", str(worktree), start_ref)
            else:
                git(
                    self.repository_path,
                    "worktree",
                    "add",
                    "-b",
                    branch,
                    str(worktree),
                    start_ref,
                )
            created = True
            if self.checkpoint_store is not None:
                self.checkpoint_store.update(task_id, CodingRunPhase.AGENT_RUNNING)
            agent_request = with_work_item_context(
                revision_agent_request(request, revision),
                (context or {}).get("work_items"),
            )
            # Instructions sent while the run was queued belong in the first prompt.
            early = guidance() if guidance is not None else None
            if early:
                agent_request += "\n\nADDITIONAL INSTRUCTIONS FROM THE USER:\n" + early
            report = self.agent.run(
                worktree=worktree,
                request=agent_request,
                timeout_seconds=self.timeout_seconds,
                is_cancelled=is_cancelled,
                **optional(on_progress=progress, next_guidance=guidance),
            )
            if is_cancelled():
                raise ExecutionCancelled("Coding task was cancelled")
            if not git(worktree, "status", "--porcelain").strip():
                raise CodingAgentError("Coding agent completed without repository changes")
            git(worktree, "diff", "--check")
            report_milestone(progress, "Agent finished; running validation")
            validation, report, attempts = self._validate_with_repair(
                task_id=task_id,
                branch=branch,
                worktree=worktree,
                agent_request=agent_request,
                report=report,
                is_cancelled=is_cancelled,
                progress=progress,
                guidance=guidance,
            )
            if self.checkpoint_store is not None:
                self.checkpoint_store.update(
                    task_id,
                    CodingRunPhase.VALIDATED,
                    validation_results=validation,
                    report=report.model_dump(mode="json"),
                    runner_attempts=attempts,
                )
            git(worktree, "add", "-A")
            git(
                worktree,
                "-c",
                "user.name=Personal Assistant Agent",
                "-c",
                "user.email=assistant-agent@localhost",
                "commit",
                "-m",
                f"Agent task {task_id[:12]}",
            )
            commit_sha = git(worktree, "rev-parse", "HEAD").strip()
            git(worktree, "update-ref", checkpoint_ref, commit_sha)
            if self.checkpoint_store is not None:
                self.checkpoint_store.update(
                    task_id, CodingRunPhase.COMMITTED, commit_sha=commit_sha
                )
            report_milestone(progress, f"Validation passed; pushing {branch}")
            git(worktree, "push", self.remote, f"HEAD:refs/heads/{branch}", timeout=120)
            if self.checkpoint_store is not None:
                self.checkpoint_store.update(task_id, CodingRunPhase.PUSHED)
            if revision:
                pull_request = self.github.get_pull_request(
                    repository=self.github_repository, number=int(revision["number"])
                )
                _require_expected_pull_request(
                    pull_request,
                    repository=self.github_repository,
                    branch=branch,
                    base_branch=self.base_branch,
                    expected_sha=commit_sha,
                )
            else:
                pull_request = self.github.create_pull_request(
                    repository=self.github_repository,
                    title=pull_request_title(request),
                    head=branch,
                    base=self.base_branch,
                    body=pull_request_body(task_id, report),
                    draft=self.draft_pull_requests,
                )
            if self.checkpoint_store is not None:
                self.checkpoint_store.update(
                    task_id,
                    CodingRunPhase.PR_CREATED,
                    pull_request_number=pull_request.number,
                    pull_request_url=pull_request.url,
                    pull_request_head_sha=pull_request.head_sha,
                )
            delete_git_ref(self.repository_path, checkpoint_ref)
            return ExecutionResult(
                output=coding_output(
                    report,
                    pull_request,
                    commit_sha,
                    self.agent.provider,
                    decision=getattr(self.agent, "last_decision", None),
                    validation=validation,
                )
            )
        finally:
            if created:
                try:
                    git(self.repository_path, "worktree", "remove", "--force", str(worktree))
                except CodingAgentError:
                    # Do not turn a successfully published PR into a retryable task failure.
                    logger.exception("Could not remove task worktree %s", worktree)

    def _validate_with_repair(
        self,
        *,
        task_id: str,
        branch: str,
        worktree: Path,
        agent_request: str,
        report: CodeAgentReport,
        is_cancelled: Callable[[], bool],
        progress: ProgressCallback | None = None,
        guidance: GuidanceSource | None = None,
    ) -> tuple[list[dict[str, Any]], CodeAgentReport, list[dict[str, Any]]]:
        """Validate; on failure give the agent one repair pass, then keep the work if needed.

        A long agent run should never be thrown away over one failing check: the agent
        sees the failure output once, and work that still fails is pushed to a separate
        branch instead of being deleted with the worktree.
        """
        attempts = _runner_attempt_payload(self.agent)
        try:
            return self._validate(worktree, is_cancelled), report, attempts
        except CodingAgentError as error:
            if error.category != "validation_failed":
                raise
            results = error.details.get("validation", [])
        logger.info("Validation failed for task %s; starting one repair pass", task_id)
        failed_step = failed_required_step(results).get("id", "a check")
        report_milestone(progress, f"Validation failed ({failed_step}); the agent is repairing it")
        try:
            report = self.agent.run(
                worktree=worktree,
                request=repair_agent_request(agent_request, results),
                timeout_seconds=self.timeout_seconds,
                is_cancelled=is_cancelled,
                **optional(on_progress=progress, next_guidance=guidance),
            )
            if is_cancelled():
                raise ExecutionCancelled("Coding task was cancelled")
            attempts = attempts + _runner_attempt_payload(self.agent)
            return self._validate(worktree, is_cancelled), report, attempts
        except CodingAgentError as error:
            if error.category == "validation_failed":
                results = error.details.get("validation", results)
            else:
                logger.warning("Repair pass for task %s failed: %s", task_id, error)
        saved_branch = self._save_failed_work(task_id, branch, worktree, results)
        failed = failed_required_step(results)
        if self.checkpoint_store is not None:
            self.checkpoint_store.update(
                task_id,
                CodingRunPhase.VALIDATION_FAILED,
                validation_results=results,
                report=report.model_dump(mode="json"),
                runner_attempts=attempts,
                last_error=f"Required validation step failed: {failed.get('id', 'unknown')}",
            )
        raise CodingAgentError(
            f"Required validation step failed after a repair attempt: {failed.get('id', 'unknown')}"
            + (f"; work saved on branch {saved_branch}" if saved_branch else ""),
            category="validation_failed",
            details={"validation": results, "saved_branch": saved_branch},
            user_message=validation_failure_reply(
                failed, saved_branch, self.github_repository, self.base_branch
            ),
        )

    def _save_failed_work(
        self,
        task_id: str,
        branch: str,
        worktree: Path,
        results: list[dict[str, Any]],
    ) -> str | None:
        saved_branch = f"{branch}-validation-failed"
        step = failed_required_step(results).get("id", "unknown")
        try:
            git(worktree, "add", "-A")
            git(
                worktree,
                "-c",
                "user.name=Personal Assistant Agent",
                "-c",
                "user.email=assistant-agent@localhost",
                "commit",
                "--no-verify",
                "-m",
                f"Agent task {task_id[:12]} (validation failed: {step})",
            )
            # A dedicated branch keeps the task's PR branch free for a clean retry.
            git(
                worktree,
                "push",
                "--force",
                self.remote,
                f"HEAD:refs/heads/{saved_branch}",
                timeout=120,
            )
        except CodingAgentError:
            logger.exception("Could not save failed work for task %s", task_id)
            return None
        return saved_branch

    def _validate(self, worktree: Path, is_cancelled: Callable[[], bool]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if self.validation_profile is None:
            return results
        for step in self.validation_profile.steps:
            if is_cancelled():
                raise ExecutionCancelled("Coding task was cancelled")
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    list(step.command),
                    cwd=worktree,
                    capture_output=True,
                    text=True,
                    timeout=step.timeout_seconds,
                    env=safe_agent_environment(),
                    check=False,
                )
                passed = completed.returncode == 0
                result = {
                    "id": step.id,
                    "passed": passed,
                    "required": step.required,
                    "exit_code": completed.returncode,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "output": (completed.stdout + completed.stderr)[-4000:],
                }
            except (FileNotFoundError, subprocess.TimeoutExpired) as error:
                passed = False
                result = {
                    "id": step.id,
                    "passed": False,
                    "required": step.required,
                    "exit_code": None,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "output": type(error).__name__,
                }
            results.append(result)
            if not passed and step.required:
                raise CodingAgentError(
                    f"Required validation step failed: {step.id}",
                    category="validation_failed",
                    details={"validation": results},
                )
        return results


def _require_expected_pull_request(
    pull_request: GitHubPullRequest,
    *,
    repository: str,
    branch: str,
    base_branch: str,
    expected_sha: str,
) -> None:
    if (
        pull_request.repository != repository
        or pull_request.head_branch != branch
        or pull_request.base_branch != base_branch
        or pull_request.head_sha != expected_sha
        or pull_request.state != "open"
    ):
        raise CodingAgentError(
            "Pull request identity diverged from the coding checkpoint",
            category="reconciliation_required",
        )
