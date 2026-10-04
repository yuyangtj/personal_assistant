from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.execution.base import ConversationTurn
from app.supervisor import SupervisorExecutor, SupervisorTools
from tests.test_triage import ScriptedChat


def _supervisor(client: TestClient, *replies: str) -> tuple[SupervisorExecutor, ScriptedChat]:
    state = client.app.state
    chat = ScriptedChat(*replies)
    tools = SupervisorTools(
        state.database, state.task_service, state.workflow_service, state.repository_registry
    )
    return SupervisorExecutor(chat, tools), chat


def _chat_with_message(client: TestClient, text: str) -> tuple[str, str]:
    chat = client.post("/chat-sessions", json={}).json()["id"]
    message = client.app.state.task_service.append_chat_message(chat, content=text).message
    return chat, message.id


def _run(executor: SupervisorExecutor, chat: str, message: str, request: str, history=()):
    return executor.execute(
        task_id="supervisor-task",
        request=request,
        is_cancelled=lambda: False,
        history=history,
        context={"chat_session_id": chat, "origin_message_id": message},
    ).output


def _step(**fields) -> str:
    return json.dumps(fields)


def test_supervisor_starts_a_coding_agent_and_says_so(client: TestClient) -> None:
    chat, message = _chat_with_message(client, "change the tab icon to a rocket")
    executor, model = _supervisor(
        client,
        _step(
            action="start_coding",
            repository_id="personal-assistant",
            goal="Change the browser tab icon to a rocket.",
        ),
        _step(action="reply", reply="Started a coding agent on the rocket icon."),
    )

    output = _run(executor, chat, message, "change the tab icon to a rocket")

    (run,) = client.get("/workflow-runs").json()["runs"]
    assert run["status"] == "running"
    assert run["input"]["request"] == "Change the browser tab icon to a rocket."
    assert run["origin_message_id"] == message
    events = client.get(f"/workflow-runs/{run['id']}/events").json()["events"]
    assert any(event["payload"].get("by") == "supervisor" for event in events)
    assert output["reply"] == "Started a coding agent on the rocket icon."
    assert output["blocks"][0] == {"type": "workflow", "workflow_run_id": run["id"]}
    # The model saw the registered repositories and the (empty) active work.
    prompt = "\n".join(message.content for message in model.requests[0])
    assert "personal-assistant" in prompt
    assert "No coding runs are active or recent." in prompt


def test_supervisor_answers_progress_from_active_work(client: TestClient) -> None:
    chat, message = _chat_with_message(client, "fix the toggle")
    starter, _ = _supervisor(
        client,
        _step(action="start_coding", repository_id="personal-assistant", goal="Fix the toggle."),
        _step(action="reply", reply="On it."),
    )
    _run(starter, chat, message, "fix the toggle")
    (run,) = client.get("/workflow-runs").json()["runs"]
    service = client.app.state.task_service
    service.record_progress(run["task_id"], "▸ Find the toggle\n· Fix it", "plan")
    service.record_progress(run["task_id"], "Writing app/web/index.html", "tool")

    executor, model = _supervisor(
        client,
        _step(action="run_detail", run=run["id"][:8]),
        _step(action="reply", reply="It is editing index.html now."),
    )
    output = _run(
        executor,
        chat,
        message,
        "how's it going?",
        history=[ConversationTurn(request="fix the toggle", reply="On it.")],
    )

    assert output["reply"] == "It is editing index.html now."
    first = "\n".join(m.content for m in model.requests[0])
    assert f"run {run['id'][:8]} [personal-assistant] “Fix the toggle.”" in first
    assert "latest step: Writing app/web/index.html" in first
    assert "(started from this chat)" in first
    assert "▸ Find the toggle" in first
    detail = json.loads(model.requests[1][-1].content.split("\n", 1)[1])
    assert detail["recent_steps"] == ["Writing app/web/index.html"]


def test_supervisor_stops_a_run_when_asked(client: TestClient) -> None:
    chat, message = _chat_with_message(client, "fix the toggle")
    starter, _ = _supervisor(
        client,
        _step(action="start_coding", repository_id="personal-assistant", goal="Fix the toggle."),
        _step(action="reply", reply="On it."),
    )
    _run(starter, chat, message, "fix the toggle")
    (run,) = client.get("/workflow-runs").json()["runs"]

    executor, _ = _supervisor(
        client,
        _step(action="stop_run", run=run["id"][:8]),
        _step(action="reply", reply="Stopped it."),
    )
    _run(executor, chat, message, "stop that")

    assert client.get(f"/tasks/{run['task_id']}").json()["status"] == "cancelled"


def test_supervisor_cannot_start_work_in_an_unknown_repository(client: TestClient) -> None:
    chat, message = _chat_with_message(client, "fix the secret repo")
    executor, model = _supervisor(
        client,
        _step(action="start_coding", repository_id="secret-repo", goal="Fix it."),
        _step(action="reply", reply="That repository isn't registered."),
    )

    output = _run(executor, chat, message, "fix the secret repo")

    assert client.get("/workflow-runs").json()["runs"] == []
    assert "error" in model.requests[1][-1].content
    assert output["blocks"] == []


def test_supervisor_starts_one_run_per_message(client: TestClient) -> None:
    chat, message = _chat_with_message(client, "do three things")
    start = _step(action="start_coding", repository_id="personal-assistant", goal="Do a thing.")
    executor, _ = _supervisor(
        client, start, start, _step(action="reply", reply="Started the first one.")
    )

    output = _run(executor, chat, message, "do three things")

    assert len(client.get("/workflow-runs").json()["runs"]) == 1
    assert len([b for b in output["blocks"] if b["type"] == "workflow"]) == 1


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
