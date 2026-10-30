"""The chat agent's coding-run tools (``SupervisorTools``), and how runs and PRs report."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.assistant.supervisor import SupervisorTools
from app.execution.routing import adapter_for

ROUTED = {"coding-pull-request", "tool-agent", "model-conversation", "fake"}


def _tools(client: TestClient) -> SupervisorTools:
    state = client.app.state
    return SupervisorTools(
        state.database, state.task_service, state.workflow_service, state.repository_registry
    )


def _chat_with_message(client: TestClient, text: str) -> tuple[str, str]:
    chat = client.post("/chat-sessions", json={}).json()["id"]
    message = client.app.state.task_service.append_chat_message(chat, content=text).message
    return chat, message.id


def _start(client: TestClient, chat: str, message: str, goal: str, repository="personal-assistant"):
    return _tools(client).start_coding(
        repository, goal, chat_session_id=chat, origin_message_id=message
    )


def test_a_coding_agent_is_started_on_a_registered_repository(client: TestClient) -> None:
    chat, message = _chat_with_message(client, "change the tab icon to a rocket")

    started = _start(client, chat, message, "Change the browser tab icon to a rocket.")

    (run,) = client.get("/workflow-runs").json()["runs"]
    assert started["workflow_run_id"] == run["id"]
    assert run["status"] == "running"
    assert run["input"]["request"] == "Change the browser tab icon to a rocket."
    assert run["origin_message_id"] == message
    events = client.get(f"/workflow-runs/{run['id']}/events").json()["events"]
    assert any(event["payload"].get("by") == "supervisor" for event in events)
    assert "No coding runs in the last week." not in _tools(client).overview(chat)[0]


def test_progress_comes_from_the_runs_own_steps(client: TestClient) -> None:
    chat, message = _chat_with_message(client, "fix the toggle")
    _start(client, chat, message, "Fix the toggle.")
    (run,) = client.get("/workflow-runs").json()["runs"]
    service = client.app.state.task_service
    service.record_progress(run["task_id"], "▸ Find the toggle\n· Fix it", "plan")
    service.record_progress(run["task_id"], "Writing app/web/index.html", "tool")

    overview, _ = _tools(client).overview(chat)
    detail = _tools(client).run_detail(run["id"][:8], chat)

    assert f"run {run['id'][:8]} [personal-assistant] “Fix the toggle.”" in overview
    assert "latest step: Writing app/web/index.html" in overview
    assert "(started from this chat)" in overview
    assert "▸ Find the toggle" in overview
    assert detail["recent_steps"] == ["Writing app/web/index.html"]


def test_a_run_is_stopped_when_asked(client: TestClient) -> None:
    chat, message = _chat_with_message(client, "fix the toggle")
    _start(client, chat, message, "Fix the toggle.")
    (run,) = client.get("/workflow-runs").json()["runs"]

    assert _tools(client).stop_run(run["id"][:8], chat) == {"stopped": run["id"][:8]}
    assert client.get(f"/tasks/{run['task_id']}").json()["status"] == "cancelled"


def test_no_work_starts_in_an_unknown_repository(client: TestClient) -> None:
    chat, message = _chat_with_message(client, "fix the secret repo")

    started = _start(client, chat, message, "Fix it.", repository="secret-repo")

    assert "error" in started
    assert client.get("/workflow-runs").json()["runs"] == []


def test_a_ready_pull_request_is_reported_in_the_chat(client: TestClient) -> None:
    service = client.app.state.task_service
    chat, message = _chat_with_message(client, "fix the toggle")
    task = service.create_task(
        request="Fix the toggle",
        required_capabilities=["coding", "pull_request_creation"],
        source_context={"repository_id": "personal-assistant"},
        chat_session_id=chat,
        origin_message_id=message,
    )
    service.claim_next_task(worker_id="w", lease_seconds=30)
    execution_id = service.start_execution(
        task.id,
        capability_id="coding-pull-request",
        executor_id="coding-pull-request",
        execution_input={},
    )
    service.start_validation(task.id, execution_id, output={"summary": "Opened a PR"})

    service.request_approval(
        task.id,
        execution_id,
        artifacts=[],
        approval={"number": 41, "url": "https://github.com/acme/widget/pull/41"},
    )

    report = client.get(f"/chat-sessions/{chat}/messages").json()["messages"][-1]
    assert report["content"] == "Draft PR #41 is ready for your review: Fix the toggle"
    assert [block["label"] for block in report["blocks"]] == [
        "Review and merge",
        "PR #41 on GitHub",
    ]
    assert report["blocks"][0]["href"] == f"#/chats/{chat}?task={task.id}"


def _started_run(client: TestClient) -> tuple[str, str, dict]:
    chat, message = _chat_with_message(client, "change the icon")
    _start(client, chat, message, "Change the icon.")
    (run,) = client.get("/workflow-runs").json()["runs"]
    return chat, message, run


def _execution(client: TestClient, task_id: str) -> str:
    service = client.app.state.task_service
    service.claim_next_task(worker_id="w", lease_seconds=30)
    return service.start_execution(
        task_id,
        capability_id="coding-pull-request",
        executor_id="coding-pull-request",
        execution_input={},
    )


def test_redirecting_a_working_agent_sends_it_the_instructions(client: TestClient) -> None:
    chat, message, run = _started_run(client)
    _execution(client, run["task_id"])

    sent = _tools(client).message_agent(run["id"][:8], "Use a rocket instead.", chat)

    inbox = client.app.state.task_service.user_messages_after(run["task_id"], 0)
    assert [text for _, text in inbox] == ["Use a rocket instead."]
    assert "sent_to" in sent


def test_redirecting_after_the_pr_is_open_starts_a_revision(client: TestClient) -> None:
    chat, message, run = _started_run(client)
    service = client.app.state.task_service
    execution_id = _execution(client, run["task_id"])
    service.start_validation(run["task_id"], execution_id, output={"summary": "Opened a PR"})
    service.request_approval(
        run["task_id"],
        execution_id,
        artifacts=[],
        approval={
            "number": 41,
            "url": "https://github.com/acme/widget/pull/41",
            "head_branch": "assistant/task-abc",
            "expected_head_sha": "a" * 40,
        },
    )
    _tools(client).message_agent(run["id"][:8], "Also make it blue.", chat)

    revision = client.app.state.task_service.get_task(
        client.get(f"/tasks/{run['task_id']}").json()["superseded_by_task_id"]
    )
    assert revision.original_request == "Also make it blue."
    assert revision.source_context["revision_pull_request"]["head_branch"] == "assistant/task-abc"
    assert revision.source_context["revision_pull_request"]["expected_head_sha"] == "a" * 40


def test_redirecting_a_finishing_run_is_refused(client: TestClient) -> None:
    chat, message, run = _started_run(client)
    execution_id = _execution(client, run["task_id"])
    client.app.state.task_service.start_validation(
        run["task_id"], execution_id, output={"summary": "Validating"}
    )
    refused = _tools(client).message_agent(run["id"][:8], "Use a rocket.", chat)

    assert "finishing up" in refused["error"]
    assert client.app.state.task_service.user_messages_after(run["task_id"], 0) == []


def test_a_revision_can_be_routed_to_the_coding_agent(client: TestClient) -> None:
    chat, message, run = _started_run(client)
    service = client.app.state.task_service
    execution_id = _execution(client, run["task_id"])
    service.start_validation(run["task_id"], execution_id, output={"summary": "Opened a PR"})
    service.request_approval(
        run["task_id"],
        execution_id,
        artifacts=[],
        approval={"number": 41, "head_branch": "assistant/x", "expected_head_sha": "a" * 40},
    )
    _tools(client).message_agent(run["id"][:8], "Use a different icon.", chat)

    revision = service.get_task(service.get_task(run["task_id"]).superseded_by_task_id)
    # The worker picks the agent by what it provides; this used to fail with
    # "No enabled capability provides: coding-pull-request".
    assert adapter_for(revision.required_capabilities, ROUTED) == "coding-pull-request"


def _open_pr_with_revision(client: TestClient) -> tuple[str, dict, str]:
    """A run with draft PR #41 waiting for review, and a revision of it requested."""
    chat, message, run = _started_run(client)
    service = client.app.state.task_service
    execution_id = _execution(client, run["task_id"])
    service.start_validation(run["task_id"], execution_id, output={"summary": "Opened a PR"})
    service.request_approval(
        run["task_id"],
        execution_id,
        artifacts=[],
        approval={
            "type": "github_pull_request_merge",
            "number": 41,
            "repository": "acme/widget",
            "url": "https://github.com/acme/widget/pull/41",
            "head_branch": "assistant/x",
            "expected_head_sha": "a" * 40,
            "draft": True,
        },
    )
    _tools(client).message_agent(run["id"][:8], "Use a different icon.", chat)
    revision_id = service.get_task(run["task_id"]).superseded_by_task_id
    return chat, run, revision_id


def test_a_revision_that_fails_before_pushing_gives_the_pr_its_gate_back(
    client: TestClient,
) -> None:
    chat, run, revision_id = _open_pr_with_revision(client)
    service = client.app.state.task_service

    service.fail_task(revision_id, error="No enabled capability provides: coding-pull-request")

    original = client.get(f"/tasks/{run['task_id']}").json()
    assert original["status"] == "waiting_for_approval"
    assert original["superseded_by_task_id"] is None
    approval = client.get(f"/tasks/{run['task_id']}/pending-approval").json()
    assert (approval["number"], approval["expected_head_sha"]) == (41, "a" * 40)
    assert client.get(f"/workflow-runs/{run['id']}").json()["task_id"] == run["task_id"]
    report = client.get(f"/chat-sessions/{chat}/messages").json()["messages"][-1]
    assert "PR #41" in report["content"] and "back waiting for your review" in report["content"]
    # The restored gate works: approving moves the task on to the merge.
    service.begin_pull_request_approval(run["task_id"], expected_head_sha="a" * 40)
    assert service.get_task(run["task_id"]).status == "executing"


def test_a_revision_that_already_pushed_keeps_the_old_gate_closed(client: TestClient) -> None:
    from app.coding.runs import CodingRunPhase, CodingRunStore

    _, run, revision_id = _open_pr_with_revision(client)
    store = CodingRunStore(client.app.state.database)
    store.start(
        task_id=revision_id,
        repository_id="personal-assistant",
        github_repository="acme/widget",
        branch="assistant/x",
    )
    store.update(revision_id, CodingRunPhase.PUSHED)

    client.app.state.task_service.fail_task(revision_id, error="GitHub was down")

    assert client.get(f"/tasks/{run['task_id']}").json()["status"] == "superseded"


def test_a_cancelled_revision_also_gives_the_gate_back(client: TestClient) -> None:
    _, run, revision_id = _open_pr_with_revision(client)

    client.app.state.task_service.cancel_task(revision_id)

    assert client.get(f"/tasks/{run['task_id']}").json()["status"] == "waiting_for_approval"


def test_the_overview_tells_a_runs_whole_story(client: TestClient) -> None:
    """PR #41 merged, then a revision of it failed: the run is still a merge, not a failure."""
    from app.assistant.overview import coding_runs, render_overview
    from app.coding.runs import CodingRunPhase, CodingRunStore

    _, run, revision_id = _open_pr_with_revision(client)
    state = client.app.state
    store = CodingRunStore(state.database)
    store.start(
        task_id=run["task_id"],
        repository_id="personal-assistant",
        github_repository="acme/widget",
        branch="assistant/x",
    )
    store.update(
        run["task_id"],
        CodingRunPhase.PR_CREATED,
        pull_request_number=41,
        pull_request_url="https://github.com/acme/widget/pull/41",
    )
    service = state.task_service
    # The PR was merged (the restore puts the gate back, the merge completes it) ...
    service.fail_task(revision_id, error="No enabled capability provides: coding-pull-request")
    approval = service.begin_pull_request_approval(run["task_id"], expected_head_sha="a" * 40)
    service.complete_pull_request_merge(
        run["task_id"],
        execution_id=approval["execution_id"],
        repository="acme/widget",
        number=41,
        url="https://github.com/acme/widget/pull/41",
        merge_sha="b" * 40,
    )

    overview = render_overview(coding_runs(state.database))

    assert "Merged: PR #41" in overview
    assert "Needs your review: none" in overview
    assert "[MERGED]" in overview and "PR #41 merged" in overview


class _FakeGitHub:
    def __init__(self, **state):
        self.state = state

    def get_pull_request(self, *, repository: str, number: int):
        from app.integrations.github import GitHubPullRequest

        return GitHubPullRequest(
            repository=repository,
            number=number,
            url=f"https://github.com/{repository}/pull/{number}",
            head_branch="assistant/x",
            head_sha="a" * 40,
            base_branch="main",
            state=self.state.get("state", "open"),
            draft=False,
            merged=self.state.get("merged", False),
            merge_commit_sha="c" * 40 if self.state.get("merged") else None,
        )


def _waiting_pr(client: TestClient) -> tuple[str, dict]:
    chat, _, run = _started_run(client)
    service = client.app.state.task_service
    execution_id = _execution(client, run["task_id"])
    service.start_validation(run["task_id"], execution_id, output={"summary": "Opened a PR"})
    service.request_approval(
        run["task_id"],
        execution_id,
        artifacts=[],
        approval={
            "type": "github_pull_request_merge",
            "number": 41,
            "repository": "acme/widget",
            "url": "https://github.com/acme/widget/pull/41",
            "head_branch": "assistant/x",
            "expected_head_sha": "a" * 40,
            "draft": True,
        },
    )
    return chat, run


def test_a_pr_merged_on_github_is_recorded_as_merged(client: TestClient) -> None:
    from app.coding.reconcile import PullRequestReconciler

    chat, run = _waiting_pr(client)
    synced: list[str] = []
    reconciler = PullRequestReconciler(
        client.app.state.task_service,
        _FakeGitHub(state="closed", merged=True),
        on_change=synced.append,
    )

    assert reconciler.run_once() == 1
    assert reconciler.run_once() == 0  # nothing waits on it any more

    task = client.get(f"/tasks/{run['task_id']}").json()
    assert task["status"] == "completed"
    assert synced == [run["task_id"]]
    messages = client.get(f"/chat-sessions/{chat}/messages").json()["messages"]
    assert any(m["content"] == "Pull request #41 was merged successfully." for m in messages)


def test_a_pr_closed_on_github_closes_its_gate(client: TestClient) -> None:
    from app.coding.reconcile import PullRequestReconciler

    _, run = _waiting_pr(client)
    reconciler = PullRequestReconciler(
        client.app.state.task_service, _FakeGitHub(state="closed", merged=False)
    )

    assert reconciler.run_once() == 1
    assert client.get(f"/tasks/{run['task_id']}").json()["status"] == "cancelled"


def test_an_open_pr_is_left_waiting(client: TestClient) -> None:
    from app.coding.reconcile import PullRequestReconciler

    _, run = _waiting_pr(client)

    assert PullRequestReconciler(client.app.state.task_service, _FakeGitHub()).run_once() == 0
    assert client.get(f"/tasks/{run['task_id']}").json()["status"] == "waiting_for_approval"


def test_a_finished_run_shows_what_its_agent_found(client: TestClient) -> None:
    chat, _, run = _started_run(client)
    service = client.app.state.task_service
    execution = _execution(client, run["task_id"])
    service.record_progress(run["task_id"], "▸ Trace the push path", "plan")
    service.record_progress(run["task_id"], "Writing docs/push.md", "tool")
    service.start_validation(
        run["task_id"],
        execution,
        output={
            "summary": "Pushes go through ntfy; the app itself never subscribes.",
            "tests": ["uv run pytest"],
            "notes": [],
            "review": {"outcome": "approved", "problems": []},
        },
    )
    service.request_approval(
        run["task_id"],
        execution,
        artifacts=[],
        approval={"number": 41, "url": "https://github.com/acme/widget/pull/41"},
    )

    detail = _tools(client).run_detail(run["id"][:8], chat)

    assert detail["summary"] == "Pushes go through ntfy; the app itself never subscribes."
    assert detail["tests"] == ["uv run pytest"] and "notes" not in detail
    assert detail["review"] == "approved"
    assert detail["plan"] == "▸ Trace the push path"
    assert detail["recent_steps"] == ["Writing docs/push.md"]
