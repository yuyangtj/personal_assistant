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
