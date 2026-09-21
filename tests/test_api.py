from fastapi.testclient import TestClient
from sqlalchemy import select

from app.integrations.github import GitHubMergeResult, GitHubPullRequest
from app.integrations.speech import SpeechAudio
from app.persistence.models import ExecutionModel


def test_create_get_list_and_cancel_task(client: TestClient) -> None:
    created_response = client.post(
        "/tasks",
        json={"request": "Research database hosting", "goal": "Compare three options"},
    )
    assert created_response.status_code == 201
    created = created_response.json()
    assert created["status"] == "created"
    assert created["current_goal"] == "Compare three options"

    fetched = client.get(f"/tasks/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]

    listed = client.get("/tasks", params={"status": "created"})
    assert listed.status_code == 200
    assert [task["id"] for task in listed.json()["tasks"]] == [created["id"]]

    cancelled = client.post(f"/tasks/{created['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    events = client.get(f"/tasks/{created['id']}/events").json()["events"]
    assert [event["event_type"] for event in events] == [
        "TASK_CREATED",
        "ASSISTANT_REPLY",
        "TASK_CANCELLED",
    ]
    assert events[1]["payload"]["text"] == "Okay, I've stopped working on that."


def test_repositories_are_listed_and_task_accepts_trusted_repository(client: TestClient) -> None:
    repositories = client.get("/repositories")

    assert repositories.status_code == 200
    assert [item["id"] for item in repositories.json()["repositories"]] == [
        "analytics-agent-playground",
        "personal-assistant",
    ]

    created = client.post(
        "/tasks",
        json={"request": "Implement the metric", "repository_id": "analytics"},
    )
    assert created.status_code == 201
    assert created.json()["source_context"]["repository_id"] == "analytics-agent-playground"

    rejected = client.post(
        "/tasks",
        json={"request": "Implement something", "repository_id": "unknown"},
    )
    assert rejected.status_code == 422


def test_workflow_run_requires_explicit_authorized_decision(client: TestClient) -> None:
    client.app.state.approval_token = "workflow-approval-secret"

    workflows = client.get("/workflows")
    assert workflows.status_code == 200
    assert {item["id"] for item in workflows.json()["workflows"]} == {
        "assistant-deployment",
        "coding-change",
        "repository-onboarding",
    }

    created = client.post(
        "/workflow-runs",
        json={
            "workflow_id": "coding-change",
            "input": {
                "repository_id": "analytics",
                "request": "Add a retention example",
            },
        },
    )
    assert created.status_code == 201
    run = created.json()
    assert run["status"] == "proposed"
    assert run["input"]["repository_id"] == "analytics-agent-playground"
    assert run["current_stage"] is None

    unauthorized = client.post(
        f"/workflow-runs/{run['id']}/decision",
        json={"decision": "approve"},
    )
    assert unauthorized.status_code == 401

    approved = client.post(
        f"/workflow-runs/{run['id']}/decision",
        headers={"X-Assistant-Approval-Token": "workflow-approval-secret"},
        json={"decision": "approve"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["current_stage"] == "implement"

    events = client.get(f"/workflow-runs/{run['id']}/events")
    assert [event["event_type"] for event in events.json()["events"]] == [
        "WORKFLOW_PROPOSED",
        "WORKFLOW_APPROVED",
    ]

    duplicate = client.post(
        f"/workflow-runs/{run['id']}/decision",
        headers={"X-Assistant-Approval-Token": "workflow-approval-secret"},
        json={"decision": "approve"},
    )
    assert duplicate.status_code == 409


def test_workflow_run_validates_registered_repository_and_required_inputs(
    client: TestClient,
) -> None:
    missing = client.post(
        "/workflow-runs",
        json={"workflow_id": "coding-change", "input": {"repository_id": "analytics"}},
    )
    assert missing.status_code == 422

    unknown_repository = client.post(
        "/workflow-runs",
        json={
            "workflow_id": "coding-change",
            "input": {"repository_id": "not-trusted", "request": "Change it"},
        },
    )
    assert unknown_repository.status_code == 422

    malformed_onboarding = client.post(
        "/workflow-runs",
        json={
            "workflow_id": "repository-onboarding",
            "input": {"github_repository": "https://github.com/owner/repo"},
        },
    )
    assert malformed_onboarding.status_code == 422


def test_external_key_makes_creation_idempotent(client: TestClient) -> None:
    payload = {
        "request": "Handle a Slack message",
        "external_source": "slack",
        "external_key": "Ev123",
    }
    first = client.post("/tasks", json=payload)
    second = client.post("/tasks", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert len(client.get("/tasks").json()["tasks"]) == 1


def test_chat_session_can_be_created_listed_resumed_and_populated(client: TestClient) -> None:
    created = client.post("/chat-sessions", json={})
    assert created.status_code == 201
    chat = created.json()
    assert chat["title"] == "New conversation"
    assert chat["archived"] is False

    first = client.post(
        "/tasks",
        json={"request": "Plan my afternoon", "chat_session_id": chat["id"]},
    )
    second = client.post(
        "/tasks",
        json={"request": "Make it less busy", "chat_session_id": chat["id"]},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["chat_session_id"] == chat["id"]
    assert first.json()["source_context"]["conversation_id"] == chat["id"]

    resumed = client.get(f"/chat-sessions/{chat['id']}")
    assert resumed.status_code == 200
    assert resumed.json()["title"] == "Plan my afternoon"

    sessions = client.get("/chat-sessions").json()["sessions"]
    assert [session["id"] for session in sessions] == [chat["id"]]

    tasks = client.get(f"/chat-sessions/{chat['id']}/tasks").json()["tasks"]
    assert [task["id"] for task in tasks] == [second.json()["id"], first.json()["id"]]

    messages = client.get(f"/chat-sessions/{chat['id']}/messages").json()["messages"]
    assert [(message["role"], message["content"]) for message in messages] == [
        ("user", "Plan my afternoon"),
        ("user", "Make it less busy"),
    ]
    assert messages[0]["linked_task_id"] == first.json()["id"]
    assert first.json()["origin_message_id"] == messages[0]["id"]


def test_chat_transcript_records_the_assistant_reply(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()
    task = client.post(
        "/tasks",
        json={"request": "Stop this", "chat_session_id": chat["id"]},
    ).json()

    cancelled = client.post(f"/tasks/{task['id']}/cancel")

    assert cancelled.status_code == 200
    messages = client.get(f"/chat-sessions/{chat['id']}/messages").json()["messages"]
    assert [(message["role"], message["content"]) for message in messages] == [
        ("user", "Stop this"),
        ("assistant", "Okay, I've stopped working on that."),
    ]
    assert all(message["linked_task_id"] == task["id"] for message in messages)


def test_existing_task_can_open_a_backfilled_chat(client: TestClient) -> None:
    task = client.post("/tasks", json={"request": "Explain the result"}).json()
    client.post(f"/tasks/{task['id']}/cancel")

    first = client.post(f"/tasks/{task['id']}/chat-session")
    second = client.post(f"/tasks/{task['id']}/chat-session")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    messages = client.get(
        f"/chat-sessions/{first.json()['id']}/messages"
    ).json()["messages"]
    assert [(message["role"], message["content"]) for message in messages] == [
        ("user", "Explain the result"),
        ("assistant", "Okay, I've stopped working on that."),
        (
            "system",
            'Discussing task: Task "Explain the result"\n'
            "Status: cancelled\n"
            "Result: Okay, I've stopped working on that.",
        ),
    ]
    # Re-entering Discuss does not stack duplicate references.
    assert sum(message["role"] == "system" for message in messages) == 1
    assert all(message["linked_task_id"] == task["id"] for message in messages)


def test_task_rejects_unknown_chat_session(client: TestClient) -> None:
    response = client.post(
        "/tasks",
        json={
            "request": "Continue this conversation",
            "chat_session_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Chat session not found"


def test_missing_task_returns_404(client: TestClient) -> None:
    assert client.get("/tasks/missing").status_code == 404
    assert client.post("/tasks/missing/cancel").status_code == 404
    assert client.get("/tasks/missing/events").status_code == 404


def test_health_checks_database(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_speech_is_unavailable_without_dedicated_tts_key(client: TestClient) -> None:
    response = client.post("/speech", json={"text": "Hello", "emotion": "Warm"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Cloud speech is not configured"


def test_speech_returns_cached_wav_with_provider_headers(client: TestClient) -> None:
    class StubSpeechSynthesizer:
        def synthesize(self, text: str, *, emotion: str) -> SpeechAudio:
            assert text == "Hello"
            assert emotion == "Excited"
            return SpeechAudio(
                wav_bytes=b"RIFFtest",
                provider="gemini-tts",
                model="gemini-tts-model",
                voice="Achird",
                duration_ms=420,
                cache_hit=True,
            )

    client.app.state.speech_synthesizer = StubSpeechSynthesizer()

    response = client.post("/speech", json={"text": "Hello", "emotion": "Excited"})

    assert response.status_code == 200
    assert response.content == b"RIFFtest"
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["x-speech-provider"] == "gemini-tts"
    assert response.headers["x-speech-model"] == "gemini-tts-model"
    assert response.headers["x-speech-voice"] == "Achird"
    assert response.headers["x-speech-duration-ms"] == "420"
    assert response.headers["x-speech-cache"] == "hit"


def test_speech_rejects_blank_or_oversized_text(client: TestClient) -> None:
    assert client.post("/speech", json={"text": "   "}).status_code == 422
    assert client.post("/speech", json={"text": "x" * 601}).status_code == 422


def test_capabilities_include_enabled_and_planned_adapters(client: TestClient) -> None:
    response = client.get("/capabilities")
    assert response.status_code == 200
    capabilities = response.json()["capabilities"]
    assert [capability["id"] for capability in capabilities] == [
        "coding-pull-request",
        "fake-executor",
        "kimi-code",
        "model-conversation",
    ]
    assert capabilities[0]["availability"]["enabled"] is True
    assert capabilities[1]["availability"]["enabled"] is True
    assert capabilities[2]["availability"]["enabled"] is False
    assert capabilities[3]["availability"]["enabled"] is True

    enabled = client.get("/capabilities", params={"include_disabled": False}).json()
    assert [capability["id"] for capability in enabled["capabilities"]] == [
        "coding-pull-request",
        "fake-executor",
        "model-conversation",
    ]


def test_task_accepts_explicit_capability_requirements(client: TestClient) -> None:
    response = client.post(
        "/tasks",
        json={
            "request": "Inspect a repository",
            "required_capabilities": ["repository_analysis"],
        },
    )
    assert response.status_code == 201
    assert response.json()["required_capabilities"] == ["repository_analysis"]


def test_task_rejects_invalid_capability_requirements(client: TestClient) -> None:
    response = client.post(
        "/tasks",
        json={
            "request": "Do something",
            "required_capabilities": ["Not Valid"],
        },
    )
    assert response.status_code == 422
    assert "Invalid required capability identifiers" in response.json()["detail"]


def _pending_pull_request_task(client: TestClient, *, draft: bool = False) -> tuple[str, str]:
    client.app.state.approval_token = "approval-secret"
    service = client.app.state.task_service
    task = service.create_task(
        request="Implement a reviewed change",
        required_capabilities=["pull_request_creation"],
    )
    service.claim_next_task(worker_id="test-worker", lease_seconds=30)
    execution_id = service.start_execution(
        task.id,
        capability_id="coding-pull-request",
        executor_id="coding-pull-request",
        execution_input={"request": task.original_request},
    )
    assert execution_id is not None
    service.start_validation(task.id, execution_id, output={"summary": "Implemented"})
    service.request_approval(
        task.id,
        execution_id,
        artifacts=[{"type": "github_pull_request", "number": 17}],
        approval={
            "type": "github_pull_request_merge",
            "repository": "acme/widget",
            "number": 17,
            "url": "https://github.com/acme/widget/pull/17",
            "expected_head_sha": "a" * 40,
            "draft": draft,
        },
    )
    return task.id, execution_id


def test_get_pending_approval_returns_latest_typed_payload(client: TestClient) -> None:
    task_id, execution_id = _pending_pull_request_task(client)
    service = client.app.state.task_service
    service.begin_pull_request_approval(task_id, expected_head_sha="a" * 40)
    service.return_to_pull_request_approval(
        task_id,
        reason="pull_request_head_changed",
        replacement_head_sha="d" * 40,
    )

    response = client.get(f"/tasks/{task_id}/pending-approval")

    assert response.status_code == 200
    assert response.json() == {
        "type": "github_pull_request_merge",
        "repository": "acme/widget",
        "number": 17,
        "url": "https://github.com/acme/widget/pull/17",
        "expected_head_sha": "d" * 40,
        "draft": False,
        "execution_id": execution_id,
        "reason": "Pull request head changed; review the new commit before approval",
    }


def test_get_pending_approval_returns_404_when_none_is_pending(client: TestClient) -> None:
    created = client.post("/tasks", json={"request": "No approval yet"}).json()

    assert client.get(f"/tasks/{created['id']}/pending-approval").status_code == 404
    assert client.get("/tasks/missing/pending-approval").status_code == 404


def test_pull_request_merge_requires_reviewed_sha_and_completes_task(
    client: TestClient,
) -> None:
    task_id, _ = _pending_pull_request_task(client)

    class GitHub:
        def get_pull_request(self, **_kwargs) -> GitHubPullRequest:
            return GitHubPullRequest(
                repository="acme/widget",
                number=17,
                url="https://github.com/acme/widget/pull/17",
                head_branch="assistant/task-123",
                head_sha="a" * 40,
                base_branch="main",
                state="open",
                draft=False,
                merged=False,
            )

        def merge_pull_request(self, **kwargs) -> GitHubMergeResult:
            assert kwargs["expected_head_sha"] == "a" * 40
            assert kwargs["method"] == "squash"
            return GitHubMergeResult(merged=True, sha="b" * 40, message="merged")

    client.app.state.github_client = GitHub()
    response = client.post(
        f"/tasks/{task_id}/pull-request-approval",
        headers={"X-Assistant-Approval-Token": "approval-secret"},
        json={
            "decision": "approve",
            "expected_head_sha": "a" * 40,
            "merge_method": "squash",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    events = client.get(f"/tasks/{task_id}/events").json()["events"]
    event_types = [event["event_type"] for event in events]
    assert "APPROVAL_GRANTED" in event_types
    assert "TOOL_CALLED" in event_types
    assert event_types[-1] == "TASK_COMPLETED"


def test_pull_request_approval_requires_separate_authorization(client: TestClient) -> None:
    task_id, _ = _pending_pull_request_task(client)

    response = client.post(
        f"/tasks/{task_id}/pull-request-approval",
        json={"decision": "reject"},
    )

    assert response.status_code == 401
    assert client.get(f"/tasks/{task_id}").json()["status"] == "waiting_for_approval"


def test_pull_request_merge_rejects_stale_review_without_calling_github(
    client: TestClient,
) -> None:
    task_id, _ = _pending_pull_request_task(client)

    class GitHub:
        def get_pull_request(self, **_kwargs):
            raise AssertionError("GitHub must not be called for an unrecognized reviewed SHA")

    client.app.state.github_client = GitHub()

    response = client.post(
        f"/tasks/{task_id}/pull-request-approval",
        headers={"X-Assistant-Approval-Token": "approval-secret"},
        json={"decision": "approve", "expected_head_sha": "c" * 40},
    )

    assert response.status_code == 409
    assert client.get(f"/tasks/{task_id}").json()["status"] == "waiting_for_approval"


def test_pull_request_merge_refreshes_approval_when_remote_head_changed(
    client: TestClient,
) -> None:
    task_id, _ = _pending_pull_request_task(client)

    class GitHub:
        def get_pull_request(self, **_kwargs) -> GitHubPullRequest:
            return GitHubPullRequest(
                repository="acme/widget",
                number=17,
                url="https://github.com/acme/widget/pull/17",
                head_branch="assistant/task-123",
                head_sha="d" * 40,
                base_branch="main",
                state="open",
                draft=False,
                merged=False,
            )

        def merge_pull_request(self, **_kwargs):
            raise AssertionError("A changed pull request must require fresh approval")

    client.app.state.github_client = GitHub()
    response = client.post(
        f"/tasks/{task_id}/pull-request-approval",
        headers={"X-Assistant-Approval-Token": "approval-secret"},
        json={"decision": "approve", "expected_head_sha": "a" * 40},
    )

    assert response.status_code == 409
    assert client.get(f"/tasks/{task_id}").json()["status"] == "waiting_for_approval"
    events = client.get(f"/tasks/{task_id}/events").json()["events"]
    approvals = [
        event for event in events if event["event_type"] == "APPROVAL_REQUESTED"
    ]
    assert len(approvals) == 2
    assert approvals[-1]["payload"]["expected_head_sha"] == "d" * 40


def test_pull_request_merge_refuses_draft_and_reopens_approval(client: TestClient) -> None:
    task_id, _ = _pending_pull_request_task(client, draft=True)

    class GitHub:
        def get_pull_request(self, **_kwargs) -> GitHubPullRequest:
            return GitHubPullRequest(
                repository="acme/widget",
                number=17,
                url="https://github.com/acme/widget/pull/17",
                head_branch="assistant/task-123",
                head_sha="a" * 40,
                base_branch="main",
                state="open",
                draft=True,
                merged=False,
            )

        def merge_pull_request(self, **_kwargs):
            raise AssertionError("Draft pull requests must not be merged")

    client.app.state.github_client = GitHub()
    response = client.post(
        f"/tasks/{task_id}/pull-request-approval",
        headers={"X-Assistant-Approval-Token": "approval-secret"},
        json={"decision": "approve", "expected_head_sha": "a" * 40},
    )

    assert response.status_code == 409
    assert "ready for review" in response.json()["detail"]
    assert client.get(f"/tasks/{task_id}").json()["status"] == "waiting_for_approval"


def test_pull_request_rejection_leaves_pr_unmerged_and_cancels_task(
    client: TestClient,
) -> None:
    task_id, execution_id = _pending_pull_request_task(client)

    response = client.post(
        f"/tasks/{task_id}/pull-request-approval",
        headers={"X-Assistant-Approval-Token": "approval-secret"},
        json={"decision": "reject"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    events = client.get(f"/tasks/{task_id}/events").json()["events"]
    assert events[-3]["event_type"] == "APPROVAL_REJECTED"
    assert events[-1]["event_type"] == "TASK_CANCELLED"
    with client.app.state.database.session() as session:
        execution = session.scalar(
            select(ExecutionModel).where(ExecutionModel.id == execution_id)
        )
        assert execution is not None
        assert execution.status == "cancelled"
        assert execution.completed_at is not None


def test_web_console_is_served(client: TestClient) -> None:
    response = client.get("/ui")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>Assistant Console</title>" in response.text


def test_root_redirects_to_web_console(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/ui"
