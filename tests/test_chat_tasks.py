"""Chat and tasks are linked by id, never by copying content between records."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.capabilities import CapabilityRegistry
from app.domain.proposals import propose_task
from app.execution import ConversationExecutor
from app.manager import DeterministicManager
from app.service import TaskService
from app.worker import TaskWorker
from tests.test_conversation import RecordingChatClient


def _chat(client: TestClient) -> str:
    return client.post("/chat-sessions", json={}).json()["id"]


def _say(client: TestClient, chat_id: str, content: str) -> dict:
    response = client.post(f"/chat-sessions/{chat_id}/messages", json={"content": content})
    assert response.status_code == 201
    return response.json()


def test_talking_in_a_chat_launches_no_work(client: TestClient) -> None:
    chat_id = _chat(client)

    body = _say(client, chat_id, "I have been thinking about our sign-in flow.")

    assert body["message"]["role"] == "user"
    assert body["message"]["linked_task_id"] is None
    assert body["proposal"] is None
    assert client.get("/tasks").json()["tasks"] == []
    # The first message still names the conversation.
    assert client.get(f"/chat-sessions/{chat_id}").json()["title"] == (
        "I have been thinking about our sign-in flow."
    )


def test_actionable_message_is_proposed_but_still_needs_confirming(client: TestClient) -> None:
    chat_id = _chat(client)

    body = _say(client, chat_id, "Please add authentication to the tasks repository.")

    assert body["proposal"] == {
        "reason": "This asks for work that changes code or infrastructure.",
        "suggested_goal": "Please add authentication to the tasks repository.",
        "required_capabilities": ["coding"],
        "consequential": True,
    }
    # Proposing is not launching.
    assert client.get("/tasks").json()["tasks"] == []


def test_confirming_a_proposal_links_the_task_to_its_originating_message(
    client: TestClient,
) -> None:
    chat_id = _chat(client)
    message_id = _say(client, chat_id, "Fix the failing timezone test.")["message"]["id"]

    created = client.post(
        f"/chat-sessions/{chat_id}/messages/{message_id}/task",
        json={"goal": "Make the timezone test pass"},
    )

    assert created.status_code == 201
    task = created.json()
    assert task["chat_session_id"] == chat_id
    assert task["origin_message_id"] == message_id
    assert task["current_goal"] == "Make the timezone test pass"
    assert task["parent_task_id"] is None

    messages = client.get(f"/chat-sessions/{chat_id}/messages").json()["messages"]
    assert [(message["role"], message["linked_task_id"]) for message in messages] == [
        ("user", task["id"])
    ]
    assert client.get(f"/chat-sessions/{chat_id}/tasks").json()["tasks"][0]["id"] == task["id"]


def test_confirming_twice_does_not_launch_the_work_twice(client: TestClient) -> None:
    chat_id = _chat(client)
    message_id = _say(client, chat_id, "Deploy the staging service.")["message"]["id"]
    path = f"/chat-sessions/{chat_id}/messages/{message_id}/task"

    first = client.post(path, json={})
    second = client.post(path, json={})

    assert first.json()["id"] == second.json()["id"]
    assert len(client.get("/tasks").json()["tasks"]) == 1


def test_creating_a_task_from_an_unknown_message_is_rejected(client: TestClient) -> None:
    chat_id = _chat(client)
    other_chat_id = _chat(client)
    message_id = _say(client, chat_id, "Write the migration.")["message"]["id"]

    missing = client.post(f"/chat-sessions/{chat_id}/messages/missing/task", json={})
    assert missing.status_code == 404
    # A message only launches work inside its own conversation.
    assert (
        client.post(
            f"/chat-sessions/{other_chat_id}/messages/{message_id}/task", json={}
        ).status_code
        == 404
    )
    assert (
        client.post("/chat-sessions/missing/messages/x/task", json={}).json()["detail"]
        == "Chat session not found"
    )


def test_task_context_is_curated_and_leaves_the_event_stream_behind(
    client: TestClient,
) -> None:
    service: TaskService = client.app.state.task_service
    task = service.create_task(request="Add the login endpoint")
    service.claim_next_task(worker_id="test-worker", lease_seconds=30)
    execution_id = service.start_execution(
        task.id,
        capability_id="coding-pull-request",
        executor_id="coding-pull-request",
        execution_input={"request": task.original_request},
    )
    assert execution_id is not None
    service.start_validation(
        task.id,
        execution_id,
        output={
            "summary": "Opened a pull request",
            "reply": "I opened a pull request for review.",
            "artifacts": [
                {
                    "type": "github_pull_request",
                    "number": 17,
                    "repository": "acme/widget",
                    "url": "https://github.com/acme/widget/pull/17",
                    "head_sha": "a" * 40,
                    # Bulk output never reaches a follow-up prompt.
                    "diff": "-secret\n+secret",
                }
            ],
            "usage": {"input_tokens": 900, "output_tokens": 120},
        },
    )
    service.complete_task(task.id, execution_id, reply="I opened a pull request for review.")

    context = client.get(f"/tasks/{task.id}/context").json()

    assert context["status"] == "completed"
    assert context["validation"] == "passed"
    assert context["final_answer"] == "I opened a pull request for review."
    assert context["summary"] == "Opened a pull request"
    assert context["error"] is None
    assert context["artifacts"] == [
        {
            "type": "github_pull_request",
            "number": 17,
            "repository": "acme/widget",
            "url": "https://github.com/acme/widget/pull/17",
            "head_sha": "a" * 40,
            "head_branch": None,
            "merge_sha": None,
        }
    ]
    serialized = json.dumps(context)
    assert "diff" not in serialized
    assert "input_tokens" not in serialized


def test_failed_task_context_carries_the_error(client: TestClient) -> None:
    service: TaskService = client.app.state.task_service
    task = service.create_task(request="Wire up the Android client")
    service.claim_next_task(worker_id="test-worker", lease_seconds=30)
    service.fail_task(
        task.id,
        error="Android integration incomplete",
        reply="I finished the API but not the Android part.",
    )

    context = client.get(f"/tasks/{task.id}/context").json()

    assert context["status"] == "failed"
    assert context["error"] == "Android integration incomplete"
    assert context["final_answer"] == "I finished the API but not the Android part."


def test_task_context_requires_a_known_task(client: TestClient) -> None:
    assert client.get("/tasks/missing/context").status_code == 404


def test_follow_up_task_references_its_parent_and_shares_the_chat(client: TestClient) -> None:
    chat_id = _chat(client)
    message_id = _say(client, chat_id, "Add authentication to the API.")["message"]["id"]
    parent = client.post(f"/chat-sessions/{chat_id}/messages/{message_id}/task", json={}).json()
    service: TaskService = client.app.state.task_service
    service.claim_next_task(worker_id="test-worker", lease_seconds=30)
    service.fail_task(parent["id"], error="Android integration incomplete")

    response = client.post(
        f"/tasks/{parent['id']}/follow-up",
        json={"request": "Finish the Android part"},
    )

    assert response.status_code == 201
    follow_up = response.json()
    assert follow_up["parent_task_id"] == parent["id"]
    assert follow_up["chat_session_id"] == chat_id
    # Linked by id, with only the curated snapshot carried across.
    carried = follow_up["source_context"]["parent_task"]
    assert carried["task_id"] == parent["id"]
    assert carried["status"] == "failed"
    assert carried["error"] == "Android integration incomplete"
    assert client.post("/tasks/missing/follow-up", json={"request": "x"}).status_code == 404


def test_follow_up_prompt_carries_the_parent_summary_not_its_events(
    service: TaskService,
) -> None:
    chat_client = RecordingChatClient(
        ['{"reply":"It failed on the Android side.","emotion":"Concerned"}']
    )
    worker = TaskWorker(
        service=service,
        manager=DeterministicManager(
            CapabilityRegistry.from_directory("capabilities").restricted_to_adapters(
                ["model-conversation"]
            )
        ),
        executors={"model-conversation": ConversationExecutor(chat_client)},
        worker_id="test-worker",
        lease_seconds=30,
        poll_interval_seconds=0.01,
    )
    chat = service.create_chat_session()
    parent = service.create_task(request="Add authentication", chat_session_id=chat.id)
    service.claim_next_task(worker_id="setup-worker", lease_seconds=30)
    service.fail_task(parent.id, error="Android integration incomplete")

    service.create_follow_up_task(parent.id, request="Why did this fail?")
    assert worker.run_once() is True

    system_messages = [
        message.content for message in chat_client.calls[0] if message.role == "system"
    ]
    carried = next(message for message in system_messages if "follows up" in message)
    assert 'Task "Add authentication"' in carried
    assert "Status: failed" in carried
    assert "Error: Android integration incomplete" in carried


@pytest.mark.parametrize(
    "content",
    [
        "Why did this fail?",
        "What is the status of the deploy?",
        "That makes sense, thanks.",
        "The sign-in flow feels slow.",
        "",
    ],
)
def test_conversation_is_left_alone(content: str) -> None:
    assert propose_task(content) is None


@pytest.mark.parametrize(
    ("content", "consequential", "capabilities"),
    [
        ("Refactor the payments module.", True, ["coding"]),
        ("Can you research three hosting options?", False, []),
        ("Let's deploy to production.", True, []),
        ("please write the release notes", True, []),
    ],
)
def test_work_requests_are_proposed_with_their_cost_flagged(
    content: str,
    consequential: bool,
    capabilities: list[str],
) -> None:
    proposal = propose_task(content)

    assert proposal is not None
    assert proposal.consequential is consequential
    assert proposal.required_capabilities == capabilities
