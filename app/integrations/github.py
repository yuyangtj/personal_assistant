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
    mergeable: bool | None = None


@dataclass(frozen=True, slots=True)
class GitHubCheckRun:
    name: str
    status: str
    conclusion: str | None
    url: str | None


@dataclass(frozen=True, slots=True)
class GitHubReviewComment:
    author: str
    body: str
    path: str | None
    line: int | None
    url: str | None


@dataclass(frozen=True, slots=True)
class GitHubMergeResult:
    merged: bool
    sha: str | None
    message: str


@dataclass(frozen=True, slots=True)
class GitHubWorkflowRun:
    id: int
    url: str
    status: str
    conclusion: str | None
    head_sha: str
    display_title: str


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

    def find_open_pull_request(
        self, *, repository: str, head_branch: str
    ) -> GitHubPullRequest | None:
        owner, name = _repository_parts(repository)
        response = self._request_list(
            "GET",
            f"/repos/{owner}/{name}/pulls",
            params={"state": "open", "head": f"{owner}:{head_branch}", "per_page": 2},
        )
        if len(response) > 1:
            raise GitHubError("GitHub returned multiple open pull requests for the branch")
        return _pull_request_from_json(repository, response[0]) if response else None

    def list_check_runs(self, *, repository: str, commit_sha: str) -> tuple[GitHubCheckRun, ...]:
        owner, name = _repository_parts(repository)
        checks: list[GitHubCheckRun] = []
        page = 1
        while True:
            response = self._request(
                "GET",
                f"/repos/{owner}/{name}/commits/{commit_sha}/check-runs",
                params={"per_page": 100, "page": page},
            )
            raw_runs = response.get("check_runs")
            if not isinstance(raw_runs, list):
                raise GitHubError("GitHub returned an unexpected check-runs response")
            for raw in raw_runs:
                if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
                    raise GitHubError("GitHub returned an invalid check run")
                checks.append(
                    GitHubCheckRun(
                        name=raw["name"],
                        status=str(raw.get("status") or "unknown"),
                        conclusion=(
                            raw.get("conclusion")
                            if isinstance(raw.get("conclusion"), str)
                            else None
                        ),
                        url=(raw.get("html_url") if isinstance(raw.get("html_url"), str) else None),
                    )
                )
            if len(raw_runs) < 100:
                break
            page += 1
            if page > 20:
                raise GitHubError("GitHub check-run pagination limit exceeded")
        return tuple(checks)

    def list_unresolved_review_comments(
        self, *, repository: str, number: int
    ) -> tuple[GitHubReviewComment, ...]:
        owner, name = _repository_parts(repository)
        comments: list[GitHubReviewComment] = []
        cursor: str | None = None
        for _page in range(20):
            response = self._request(
                "POST",
                "/graphql",
                json={
                    "query": _REVIEW_THREADS_QUERY,
                    "variables": {
                        "owner": owner,
                        "name": name,
                        "number": number,
                        "cursor": cursor,
                    },
                },
            )
            if response.get("errors"):
                raise GitHubError("GitHub rejected the review-thread query")
            try:
                threads = response["data"]["repository"]["pullRequest"]["reviewThreads"]
                nodes = threads["nodes"]
                page_info = threads["pageInfo"]
                if not isinstance(nodes, list) or not isinstance(page_info, dict):
                    raise TypeError
            except (KeyError, TypeError) as error:
                raise GitHubError("GitHub returned unexpected review threads") from error
            for thread in nodes:
                if not isinstance(thread, dict) or thread.get("isResolved") is True:
                    continue
                raw_comments = (thread.get("comments") or {}).get("nodes")
                if not isinstance(raw_comments, list):
                    raise GitHubError("GitHub returned invalid review comments")
                for raw in raw_comments:
                    if not isinstance(raw, dict) or not isinstance(raw.get("body"), str):
                        raise GitHubError("GitHub returned an invalid review comment")
                    author = raw.get("author")
                    comments.append(
                        GitHubReviewComment(
                            author=(
                                str(author.get("login") or "unknown")
                                if isinstance(author, dict)
                                else "unknown"
                            ),
                            body=raw["body"][:2000],
                            path=raw.get("path") if isinstance(raw.get("path"), str) else None,
                            line=raw.get("line") if isinstance(raw.get("line"), int) else None,
                            url=raw.get("url") if isinstance(raw.get("url"), str) else None,
                        )
                    )
            if page_info.get("hasNextPage") is not True:
                return tuple(comments)
            cursor = page_info.get("endCursor")
            if not isinstance(cursor, str):
                raise GitHubError("GitHub omitted the review-thread cursor")
        raise GitHubError("GitHub review-thread pagination limit exceeded")

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

    def get_branch_head(self, *, repository: str, branch: str) -> str:
        owner, name = _repository_parts(repository)
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch) or ".." in branch.split("/"):
            raise ValueError("GitHub branch is invalid")
        response = self._request("GET", f"/repos/{owner}/{name}/git/ref/heads/{branch}")
        try:
            target = response["object"]
            if not isinstance(target, dict) or not isinstance(target.get("sha"), str):
                raise TypeError
            return target["sha"]
        except (KeyError, TypeError) as error:
            raise GitHubError("GitHub returned an unexpected branch response") from error

    def dispatch_workflow(
        self,
        *,
        repository: str,
        workflow_file: str,
        ref: str,
        inputs: dict[str, str],
    ) -> None:
        owner, name = _repository_parts(repository)
        _validate_workflow_file(workflow_file)
        self._request_empty(
            "POST",
            f"/repos/{owner}/{name}/actions/workflows/{workflow_file}/dispatches",
            json={"ref": ref, "inputs": inputs},
        )

    def find_workflow_run(
        self,
        *,
        repository: str,
        workflow_file: str,
        branch: str,
        commit_sha: str,
        display_title: str,
    ) -> GitHubWorkflowRun | None:
        owner, name = _repository_parts(repository)
        _validate_workflow_file(workflow_file)
        response = self._request(
            "GET",
            f"/repos/{owner}/{name}/actions/workflows/{workflow_file}/runs",
            params={"event": "workflow_dispatch", "branch": branch, "per_page": 100},
        )
        runs = response.get("workflow_runs")
        if not isinstance(runs, list):
            raise GitHubError("GitHub returned an unexpected workflow-runs response")
        for raw in runs:
            if not isinstance(raw, dict):
                raise GitHubError("GitHub returned an invalid workflow run")
            if raw.get("head_sha") != commit_sha or raw.get("display_title") != display_title:
                continue
            try:
                return GitHubWorkflowRun(
                    id=int(raw["id"]),
                    url=str(raw["html_url"]),
                    status=str(raw["status"]),
                    conclusion=(
                        raw.get("conclusion")
                        if isinstance(raw.get("conclusion"), str)
                        else None
                    ),
                    head_sha=str(raw["head_sha"]),
                    display_title=str(raw["display_title"]),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise GitHubError("GitHub returned an invalid workflow run") from error
        return None

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
        body = self._request_value(method, path, **kwargs)
        if not isinstance(body, dict):
            raise GitHubError("GitHub returned an unexpected response shape")
        return body

    def _request_list(self, method: str, path: str, **kwargs: object) -> list[dict[str, object]]:
        body = self._request_value(method, path, **kwargs)
        if not isinstance(body, list) or any(not isinstance(item, dict) for item in body):
            raise GitHubError("GitHub returned an unexpected response shape")
        return body

    def _request_value(self, method: str, path: str, **kwargs: object) -> object:
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
        return body

    def _request_empty(self, method: str, path: str, **kwargs: object) -> None:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise GitHubError(f"GitHub request failed: {type(error).__name__}") from error
        if response.status_code not in range(200, 300):
            raise GitHubError(f"GitHub returned HTTP {response.status_code}")


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


def _validate_workflow_file(workflow_file: str) -> None:
    if (
        not re.fullmatch(r"[A-Za-z0-9._/-]+\.ya?ml", workflow_file)
        or workflow_file.startswith("/")
        or ".." in workflow_file.split("/")
    ):
        raise ValueError("GitHub workflow file is invalid")


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
            mergeable=body.get("mergeable") if isinstance(body.get("mergeable"), bool) else None,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise GitHubError("GitHub returned an unexpected pull request shape") from error


_REVIEW_THREADS_QUERY = """
query ReviewThreads($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        nodes {
          isResolved
          comments(first: 100) {
            nodes { author { login } body path line url }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""
