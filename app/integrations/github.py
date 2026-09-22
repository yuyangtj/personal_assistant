from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import httpx


class GitHubError(RuntimeError):
    """A sanitized GitHub API failure that never includes credentials or response bodies."""


@dataclass(frozen=True, slots=True)
class GitHubPullRequest:
    repository: str
    number: int
    url: str
    head_branch: str
    head_sha: str
    base_branch: str
    state: str
    draft: bool
    merged: bool
    merge_commit_sha: str | None = None
    node_id: str | None = None


@dataclass(frozen=True, slots=True)
class GitHubMergeResult:
    merged: bool
    sha: str | None
    message: str


class GitHubClient:
    """Minimal GitHub REST client for creating, inspecting, and merging pull requests."""

    def __init__(
        self,
        *,
        token: str,
        base_url: str = "https://api.github.com",
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not token:
            raise ValueError("A GitHub token is required")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2026-03-10",
            },
            timeout=timeout_seconds,
            transport=transport,
        )

    def create_pull_request(
        self,
        *,
        repository: str,
        title: str,
        head: str,
        base: str,
        body: str,
        draft: bool = True,
    ) -> GitHubPullRequest:
        owner, name = _repository_parts(repository)
        response = self._request(
            "POST",
            f"/repos/{owner}/{name}/pulls",
            json={"title": title, "head": head, "base": base, "body": body, "draft": draft},
        )
        return _pull_request_from_json(repository, response)

    def get_pull_request(self, *, repository: str, number: int) -> GitHubPullRequest:
        owner, name = _repository_parts(repository)
        response = self._request("GET", f"/repos/{owner}/{name}/pulls/{number}")
        return _pull_request_from_json(repository, response)

    def merge_pull_request(
        self,
        *,
        repository: str,
        number: int,
        expected_head_sha: str,
        method: Literal["merge", "squash", "rebase"] = "squash",
        commit_title: str | None = None,
    ) -> GitHubMergeResult:
        owner, name = _repository_parts(repository)
        payload: dict[str, object] = {"sha": expected_head_sha, "merge_method": method}
        if commit_title:
            payload["commit_title"] = commit_title
        response = self._request(
            "PUT",
            f"/repos/{owner}/{name}/pulls/{number}/merge",
            json=payload,
        )
        return GitHubMergeResult(
            merged=response.get("merged") is True,
            sha=response.get("sha") if isinstance(response.get("sha"), str) else None,
            message=str(response.get("message") or "GitHub returned no merge message"),
        )

    def mark_pull_request_ready_for_review(self, *, node_id: str) -> bool:
        if not node_id:
            raise ValueError("A GitHub pull request node id is required")
        response = self._request(
            "POST",
            "/graphql",
            json={
                "query": (
                    "mutation MarkReady($pullRequestId: ID!) { "
                    "markPullRequestReadyForReview(input: {pullRequestId: $pullRequestId}) { "
                    "pullRequest { isDraft } } }"
                ),
                "variables": {"pullRequestId": node_id},
            },
        )
        if response.get("errors"):
            raise GitHubError("GitHub rejected the ready-for-review operation")
        try:
            data = response["data"]
            if not isinstance(data, dict):
                raise TypeError
            mutation = data["markPullRequestReadyForReview"]
            if not isinstance(mutation, dict):
                raise TypeError
            pull_request = mutation["pullRequest"]
            if not isinstance(pull_request, dict):
                raise TypeError
            return pull_request.get("isDraft") is False
        except (KeyError, TypeError) as error:
            raise GitHubError("GitHub returned an unexpected ready-for-review result") from error

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise GitHubError(f"GitHub request failed: {type(error).__name__}") from error
        if response.status_code not in range(200, 300):
            raise GitHubError(f"GitHub returned HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as error:
            raise GitHubError("GitHub returned invalid JSON") from error
        if not isinstance(body, dict):
            raise GitHubError("GitHub returned an unexpected response shape")
        return body


def _repository_parts(repository: str) -> tuple[str, str]:
    parts = repository.strip().split("/")
    if len(parts) != 2:
        raise ValueError("GitHub repository must use owner/name format")
    owner, name = parts
    owner_is_valid = re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", owner)
    name_is_valid = re.fullmatch(r"[A-Za-z0-9._-]+", name) and name not in {".", ".."}
    if not owner_is_valid or not name_is_valid:
        raise ValueError("GitHub repository must use owner/name format")
    return owner, name


def _pull_request_from_json(repository: str, body: dict[str, object]) -> GitHubPullRequest:
    try:
        head = body["head"]
        base = body["base"]
        if not isinstance(head, dict) or not isinstance(base, dict):
            raise TypeError("head and base must be objects")
        return GitHubPullRequest(
            repository=repository,
            number=int(body["number"]),
            url=str(body["html_url"]),
            head_branch=str(head["ref"]),
            head_sha=str(head["sha"]),
            base_branch=str(base["ref"]),
            state=str(body["state"]),
            draft=body.get("draft") is True,
            merged=body.get("merged") is True,
            merge_commit_sha=(
                body.get("merge_commit_sha")
                if isinstance(body.get("merge_commit_sha"), str)
                else None
            ),
            node_id=body.get("node_id") if isinstance(body.get("node_id"), str) else None,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise GitHubError("GitHub returned an unexpected pull request shape") from error
