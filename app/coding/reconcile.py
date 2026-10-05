"""Keep pull-request gates in step with GitHub.

A draft PR waits for review in the console, but it can also be merged or closed on GitHub
directly. Without this, the assistant would keep reporting such a PR as waiting for review
and could even start a revision of a merged PR.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.integrations.github import GitHubClient, GitHubError, GitHubPullRequest
from app.services import TaskService

logger = logging.getLogger(__name__)


class PullRequestReconciler:
    def __init__(
        self,
        service: TaskService,
        github: GitHubClient,
        *,
        on_change: Callable[[str], None] | None = None,
    ):
        self.service = service
        self.github = github
        self.on_change = on_change

    def run_once(self) -> int:
        """Close the gates of PRs merged or closed elsewhere; returns how many changed."""
        changed = 0
        for gate in self.service.open_pull_request_gates():
            try:
                pull_request = self.github.get_pull_request(
                    repository=gate["repository"], number=gate["number"]
                )
            except GitHubError:
                logger.warning("Could not read PR #%s from GitHub", gate["number"])
                continue
            changed += self.settle(gate, pull_request)
        return changed

    def settle_task(self, task_id: str, pull_request: GitHubPullRequest) -> bool:
        """Close one task's gate now if its PR, just read from GitHub, is merged or closed."""
        gate = next(
            (g for g in self.service.open_pull_request_gates() if g["task_id"] == task_id), None
        )
        return gate is not None and self.settle(gate, pull_request)

    def settle(self, gate: dict, pull_request: GitHubPullRequest) -> bool:
        if pull_request.merged:
            self.service.record_merged_elsewhere(
                gate["task_id"], merge_sha=pull_request.merge_commit_sha
            )
        elif pull_request.state == "closed":
            self.service.reject_approval(
                gate["task_id"],
                reply=f"Pull request #{gate['number']} was closed on GitHub without merging.",
            )
        else:
            return False
        logger.info("PR #%s changed on GitHub; its gate is closed", gate["number"])
        if self.on_change is not None:
            self.on_change(gate["task_id"])
        return True
