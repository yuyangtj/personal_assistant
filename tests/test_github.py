from __future__ import annotations

import json

import httpx
import pytest

from app.integrations.github import GitHubClient, GitHubError


def _pull_request_json(*, draft: bool = True, merged: bool = False) -> dict[str, object]:
    return {
        "number": 17,
        "html_url": "https://github.com/acme/widget/pull/17",
        "head": {"ref": "assistant/task-123", "sha": "a" * 40},
        "base": {"ref": "main"},
        "state": "open",
        "draft": draft,
        "merged": merged,
        "merge_commit_sha": "b" * 40 if merged else None,
        "node_id": "PR_kwDOExample",
    }


def test_github_client_creates_draft_and_merges_exact_head() -> None:
    requests: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, payload))
        assert request.headers["authorization"] == "Bearer secret-token"
        if request.method == "PUT":
            return httpx.Response(200, json={"merged": True, "sha": "b" * 40, "message": "ok"})
        return httpx.Response(201, json=_pull_request_json())

    client = GitHubClient(token="secret-token", transport=httpx.MockTransport(handler))
    try:
        pull_request = client.create_pull_request(
            repository="acme/widget",
            title="Change widget",
            head="assistant/task-123",
            base="main",
            body="Summary",
        )
        merged = client.merge_pull_request(
            repository="acme/widget",
            number=17,
            expected_head_sha="a" * 40,
            method="squash",
        )
    finally:
        client.close()

    assert pull_request.draft is True
    assert pull_request.head_sha == "a" * 40
    assert merged.merged is True
    assert requests[0] == (
        "POST",
        "/repos/acme/widget/pulls",
        {
            "title": "Change widget",
            "head": "assistant/task-123",
            "base": "main",
            "body": "Summary",
            "draft": True,
        },
    )
    assert requests[1][2] == {"sha": "a" * 40, "merge_method": "squash"}


def test_github_errors_do_not_expose_response_or_token() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            403,
            json={"message": "secret-token cannot access private details"},
        )
    )
    client = GitHubClient(token="secret-token", transport=transport)
    try:
        with pytest.raises(GitHubError) as captured:
            client.get_pull_request(repository="acme/widget", number=17)
    finally:
        client.close()

    assert str(captured.value) == "GitHub returned HTTP 403"
    assert "secret-token" not in str(captured.value)


@pytest.mark.parametrize("repository", ["owner", "owner/repo/extra", "owner/repo bad"])
def test_github_rejects_invalid_repository_names(repository: str) -> None:
    client = GitHubClient(token="secret")
    try:
        with pytest.raises(ValueError, match="owner/name"):
            client.get_pull_request(repository=repository, number=1)
    finally:
        client.close()


def test_github_accepts_repository_names_with_dots() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=_pull_request_json()))
    client = GitHubClient(token="secret", transport=transport)
    try:
        pull_request = client.get_pull_request(repository="acme/widget.py", number=17)
    finally:
        client.close()

    assert pull_request.repository == "acme/widget.py"


def test_github_marks_pull_request_ready_for_review_with_graphql() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.url.path == "/graphql"
        return httpx.Response(
            200,
            json={"data": {"markPullRequestReadyForReview": {"pullRequest": {"isDraft": False}}}},
        )

    client = GitHubClient(token="secret", transport=httpx.MockTransport(handler))
    try:
        ready = client.mark_pull_request_ready_for_review(node_id="PR_kwDOExample")
    finally:
        client.close()

    assert ready is True
    assert captured["variables"] == {"pullRequestId": "PR_kwDOExample"}


def test_github_lists_paginated_check_runs() -> None:
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        pages.append(page)
        count = 100 if page == 1 else 1
        return httpx.Response(
            200,
            json={
                "check_runs": [
                    {
                        "name": f"check-{page}-{index}",
                        "status": "completed",
                        "conclusion": "success",
                        "html_url": f"https://example.test/{page}/{index}",
                    }
                    for index in range(count)
                ]
            },
        )

    client = GitHubClient(token="secret", transport=httpx.MockTransport(handler))
    try:
        checks = client.list_check_runs(repository="acme/widget", commit_sha="a" * 40)
    finally:
        client.close()

    assert pages == [1, 2]
    assert len(checks) == 101
    assert checks[-1].name == "check-2-0"


def test_github_rejects_malformed_check_run_response() -> None:
    client = GitHubClient(
        token="secret",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"checks": []})),
    )
    try:
        with pytest.raises(GitHubError, match="unexpected check-runs"):
            client.list_check_runs(repository="acme/widget", commit_sha="a" * 40)
    finally:
        client.close()


def test_github_returns_only_unresolved_review_comments() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "isResolved": True,
                                        "comments": {"nodes": [{"body": "old"}]},
                                    },
                                    {
                                        "isResolved": False,
                                        "comments": {
                                            "nodes": [
                                                {
                                                    "author": {"login": "reviewer"},
                                                    "body": "Please cover the retry case",
                                                    "path": "app/worker.py",
                                                    "line": 42,
                                                    "url": "https://example.test/comment",
                                                }
                                            ]
                                        },
                                    },
                                ],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    }
                }
            },
        )

    client = GitHubClient(token="secret", transport=httpx.MockTransport(handler))
    try:
        comments = client.list_unresolved_review_comments(repository="acme/widget", number=17)
    finally:
        client.close()

    assert len(comments) == 1
    assert comments[0].author == "reviewer"
    assert comments[0].path == "app/worker.py"


def test_github_paginates_and_truncates_unresolved_review_comments() -> None:
    cursors: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = json.loads(request.content)["variables"]["cursor"]
        cursors.append(cursor)
        final = cursor == "page-2"
        return httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "isResolved": False,
                                        "comments": {
                                            "nodes": [
                                                {
                                                    "author": {"login": "reviewer"},
                                                    "body": ("second" if final else "x" * 3000),
                                                    "path": "app/worker.py",
                                                    "line": 12,
                                                    "url": "https://example.test/comment",
                                                }
                                            ]
                                        },
                                    }
                                ],
                                "pageInfo": {
                                    "hasNextPage": not final,
                                    "endCursor": None if final else "page-2",
                                },
                            }
                        }
                    }
                }
            },
        )

    client = GitHubClient(token="secret", transport=httpx.MockTransport(handler))
    try:
        comments = client.list_unresolved_review_comments(repository="acme/widget", number=17)
    finally:
        client.close()

    assert cursors == [None, "page-2"]
    assert [len(comment.body) for comment in comments] == [2000, 6]


def test_github_dispatches_and_finds_exact_workflow_run() -> None:
    requests: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, payload))
        if request.method == "POST":
            return httpx.Response(204)
        return httpx.Response(
            200,
            json={
                "workflow_runs": [
                    {
                        "id": 42,
                        "html_url": "https://github.com/acme/widget/actions/runs/42",
                        "status": "completed",
                        "conclusion": "success",
                        "head_sha": "a" * 40,
                        "display_title": "Deploy run-123",
                    }
                ]
            },
        )

    client = GitHubClient(token="secret", transport=httpx.MockTransport(handler))
    try:
        client.dispatch_workflow(
            repository="acme/widget",
            workflow_file="deploy.yml",
            ref="main",
            inputs={"commit_sha": "a" * 40, "workflow_run_id": "run-123"},
        )
        run = client.find_workflow_run(
            repository="acme/widget",
            workflow_file="deploy.yml",
            branch="main",
            commit_sha="a" * 40,
            display_title="Deploy run-123",
        )
    finally:
        client.close()

    assert run is not None
    assert run.id == 42
    assert run.conclusion == "success"
    assert requests[0] == (
        "POST",
        "/repos/acme/widget/actions/workflows/deploy.yml/dispatches",
        {"ref": "main", "inputs": {"commit_sha": "a" * 40, "workflow_run_id": "run-123"}},
    )
