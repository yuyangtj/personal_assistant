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


def _started_run(client: TestClient) -> tuple[str, str, dict]:
    chat, message = _chat_with_message(client, "change the icon")
    starter, _ = _supervisor(
        client,
        _step(action="start_coding", repository_id="personal-assistant", goal="Change the icon."),
        _step(action="reply", reply="On it."),
    )
    _run(starter, chat, message, "change the icon")
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
    executor, model = _supervisor(
        client,
        _step(action="message_agent", run=run["id"][:8], text="Use a rocket instead."),
        _step(action="reply", reply="Told the agent to use a rocket."),
    )

    _run(executor, chat, message, "make it a rocket instead")

    inbox = client.app.state.task_service.user_messages_after(run["task_id"], 0)
    assert [text for _, text in inbox] == ["Use a rocket instead."]
    assert '"sent_to"' in model.requests[1][-1].content


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
    executor, _ = _supervisor(
        client,
        _step(action="message_agent", run=run["id"][:8], text="Also make it blue."),
        _step(action="reply", reply="Started a revision of PR 41."),
    )

    _run(executor, chat, message, "also make it blue")

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
    executor, model = _supervisor(
        client,
        _step(action="message_agent", run=run["id"][:8], text="Use a rocket."),
        _step(action="reply", reply="It's finishing; I'll revise it once the PR is open."),
    )

    _run(executor, chat, message, "make it a rocket")

    assert "finishing up" in model.requests[1][-1].content
    assert client.app.state.task_service.user_messages_after(run["task_id"], 0) == []


def test_supervisor_replies_drop_markdown_emphasis(client: TestClient) -> None:
    chat, message = _chat_with_message(client, "status?")
    executor, _ = _supervisor(
        client,
        _step(action="reply", reply="Recent work:\n- **PR #35** is ready\n\n- __Run 804e__ failed"),
    )

    output = _run(executor, chat, message, "status?")

    assert output["reply"] == "Recent work:\n- PR #35 is ready\n- Run 804e failed"


def test_a_reply_without_an_action_key_is_still_a_reply(client: TestClient) -> None:
    chat, message = _chat_with_message(client, "status?")
    executor, model = _supervisor(client, '{"reply": "reply", "reply": "Nothing is running."}')

    output = _run(executor, chat, message, "status?")

    assert output["reply"] == "Nothing is running."
    assert len(model.requests) == 1  # no repair round-trip


def test_a_repair_prompt_restates_the_users_question(client: TestClient) -> None:
    chat, message = _chat_with_message(client, "status?")
    executor, model = _supervisor(
        client, "Here is the status, in prose.", _step(action="reply", reply="All quiet.")
    )

    _run(executor, chat, message, "What coding work has been going on recently?")

    repair = model.requests[1][-1].content
    assert "What coding work has been going on recently?" in repair


def test_only_links_the_supervisor_was_given_survive(client: TestClient) -> None:
    from app.coding_flow import start_coding_run
    from app.coding_runs import CodingRunPhase, CodingRunStore

    state = client.app.state
    chat, message = _chat_with_message(client, "fix the toggle")
    run = start_coding_run(
        state.task_service,
        state.workflow_service,
        state.repository_registry,
        repository_id="personal-assistant",
        request="Fix the toggle.",
        chat_session_id=chat,
        origin_message_id=message,
        approved_by="chat",
    )
    # The run's checkpoint holds the real PR URL.
    store = CodingRunStore(state.database)
    store.start(
        task_id=run.task_id,
        repository_id="personal-assistant",
        github_repository="yuyangtj/personal_assistant",
        branch="assistant/x",
    )
    real = "https://github.com/yuyangtj/personal_assistant/pull/35"
    store.update(
        run.task_id, CodingRunPhase.PR_CREATED, pull_request_number=35, pull_request_url=real
    )
    executor, model = _supervisor(
        client,
        _step(
            action="reply",
            reply="PR 35 is ready.",
            links=[
                {"label": "PR 35", "href": real},
                {"label": "Wrong owner", "href": "https://github.com/personal-assistant/x/pull/35"},
                {"label": "Made up", "href": "https://console/user/runs/1"},
                {"label": "Review", "href": f"#/chats/{chat}?task={run.task_id}"},
            ],
        ),
    )

    output = _run(executor, chat, message, "is the PR ready?")

    assert f"PR: {real}" in "\n".join(m.content for m in model.requests[0])
    assert [b["label"] for b in output["blocks"] if b["type"] == "link"] == ["PR 35", "Review"]
