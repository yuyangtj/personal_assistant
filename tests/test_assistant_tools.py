from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.assistant.supervisor import SupervisorTools
from app.assistant.toolbox import AssistantTool, Toolbox, ToolContext
from app.assistant.tools import bookkeeping_tools, coding_tools, job_tools
from app.execution.conversation import ConversationExecutor
from tests.test_repository_tools import ScriptedChat


def call(tool: str, **arguments) -> str:
    return json.dumps({"tool": tool, "arguments": arguments})


def reply(text: str) -> str:
    return json.dumps({"reply": text, "emotion": "Warm"})


@pytest.fixture
def chat_id(client: TestClient) -> str:
    return client.post("/chat-sessions", json={}).json()["id"]


def toolbox(client: TestClient, **kwargs) -> Toolbox:
    state = client.app.state
    tools = bookkeeping_tools(
        items=state.work_item_service,
        memory=state.memory_service,
        schedules=state.schedule_service,
        notes=state.note_service,
        tasks=state.task_service,
    )
    tools += job_tools(state.task_service, research=True, shopping=True)
    tools += coding_tools(
        SupervisorTools(
            state.database,
            state.task_service,
            state.workflow_service,
            state.repository_registry,
        )
    )
    return Toolbox(tools, **kwargs)


def agent(client: TestClient, *replies: str) -> tuple[ConversationExecutor, ScriptedChat]:
    chat = ScriptedChat(*replies)
    executor = ConversationExecutor(  # type: ignore[arg-type]
        chat, now=lambda: datetime(2026, 10, 24, 10, 0, tzinfo=UTC)
    )
    executor.toolbox = toolbox(client)
    executor.default_timezone = "Europe/Stockholm"
    return executor, chat


def run(executor: ConversationExecutor, chat_id: str, request: str):
    return executor.execute(
        task_id="t",
        request=request,
        is_cancelled=lambda: False,
        context={"chat_session_id": chat_id},
    )


def test_one_message_can_remember_add_to_a_list_and_save_a_note(
    client: TestClient, chat_id: str
) -> None:
    groceries = client.post("/work-items", json={"title": "Groceries", "kind": "list"}).json()
    executor, chat = agent(
        client,
        call("remember", text="I prefer oat milk", kind="preference"),
        call("list_add", text="oat milk", list="#groceries"),
        call("note_add", text="Try the new bakery on Sveavägen"),
        reply("Done: remembered, added to groceries, and noted."),
    )

    result = run(executor, chat_id, "I like oat milk, put it on the list, and note the bakery")

    assert result.output["reply"] == "Done: remembered, added to groceries, and noted."
    assert [c["tool"] for c in result.output["tool_calls"]] == ["remember", "list_add", "note_add"]
    assert all(c["ok"] for c in result.output["tool_calls"])
    memories = client.get("/memories").json()["memories"]
    assert [(m["kind"], m["content"]) for m in memories] == [("preference", "I prefer oat milk")]
    entries = client.get(f"/work-items/{groceries['id']}").json()["checklist"]
    assert [e["text"] for e in entries] == ["oat milk"]
    notes = client.get("/notes").json()["notes"]
    assert [n["body"] for n in notes] == ["Try the new bakery on Sveavägen"]
    # What the tools said went back to the model, and the note's link joins the reply.
    assert "Added “oat milk” to #groceries." in chat.requests[2][-1].content
    assert {"type": "link", "href": "#/notes", "label": "Open Notes"} in result.output["blocks"]


def test_the_agent_starts_lists_and_the_shopping_list_starts_itself(
    client: TestClient, chat_id: str
) -> None:
    executor, chat = agent(
        client,
        call("list_create", name="Camping"),
        call("list_add", text="tent", list="#camping"),
        call("list_create", name="camping"),
        call("list_add", text="oat milk", list="#groceries"),
        reply("Started #camping with a tent, and oat milk is on your groceries."),
    )

    result = run(executor, chat_id, "pack a tent for camping, and buy oat milk")

    results = [request[-1].content for request in chat.requests[1:]]
    assert "Created the list #camping." in results[0]
    assert "#camping already exists" in results[2]
    assert "Started your #groceries list with “oat milk”." in results[3]
    assert all(c["ok"] for c in result.output["tool_calls"])
    lists = {i["slug"]: i for i in client.get("/work-items?kind=list").json()["work_items"]}
    assert sorted(lists) == ["camping", "groceries"]
    entries = client.get(f"/work-items/{lists['camping']['id']}").json()["checklist"]
    assert [e["text"] for e in entries] == ["tent"]


def test_several_calls_in_one_reply_all_run_in_order(client: TestClient, chat_id: str) -> None:
    client.post("/work-items", json={"title": "Groceries", "kind": "list"})
    executor, chat = agent(
        client,
        # As MiniMax wrote it: three objects back to back, one misspelled, the last cut off.
        call("remember", text="I like oat milk", kind="preference")
        + "\n"
        + call("listadd", text="oat milk")
        + " "
        + call("list_add", text="oat milk", list="#groceries")
        + '\n{"tool": "remind", "argum',
        reply("Remembered and added; when should I remind you?"),
    )

    result = run(executor, chat_id, "remember I like oat milk and add it to groceries")

    assert result.output["reply"] == "Remembered and added; when should I remind you?"
    assert [(c["tool"], c["ok"]) for c in result.output["tool_calls"]] == [
        ("remember", True),
        ("listadd", False),
        ("list_add", True),
    ]
    # One round trip: the three results went back together.
    assert len(chat.requests) == 2
    results = chat.requests[1][-1].content
    assert results.count("END TOOL RESULT") == 3 and "no tool called 'listadd'" in results


def test_raw_tool_json_is_never_shown_as_the_answer(client: TestClient, chat_id: str) -> None:
    from app.execution.conversation import MAX_TOOL_CALLS

    many = "\n".join(call("list_add", text=f"item {n}") for n in range(MAX_TOOL_CALLS + 2))
    executor, chat = agent(client, many, many, many)

    result = run(executor, chat_id, "add lots")

    assert '"tool"' not in result.output["reply"]
    assert len(result.output["tool_calls"]) <= MAX_TOOL_CALLS
    assert "this one was not made" in chat.requests[1][-1].content


def test_the_same_call_is_never_carried_out_twice(client: TestClient, chat_id: str) -> None:
    groceries = client.post("/work-items", json={"title": "Groceries", "kind": "list"}).json()
    executor, chat = agent(
        client,
        call("list_add", text="bread"),
        call("list_add", text="bread"),
        reply("Added bread."),
    )

    result = run(executor, chat_id, "add bread")

    assert len(client.get(f"/work-items/{groceries['id']}").json()["checklist"]) == 1
    assert len(result.output["tool_calls"]) == 1
    assert "already made this call" in chat.requests[2][-1].content


def test_a_reminder_is_set_in_the_users_time_zone(client: TestClient, chat_id: str) -> None:
    executor, chat = agent(
        client,
        call("remind", text="call the dentist", at="2099-06-15T09:30"),
        reply("I'll remind you tomorrow at 9:30."),
    )

    run(executor, chat_id, "remind me to call the dentist tomorrow at 9:30")

    (schedule,) = client.app.state.schedule_service.list()
    assert schedule.message == "call the dentist" and schedule.timezone == "Europe/Stockholm"
    assert schedule.next_run_at.replace(tzinfo=UTC) == datetime(2099, 6, 15, 7, 30, tzinfo=UTC)
    assert schedule.chat_session_id == chat_id
    # The model is told the time where the user is, so "tomorrow" means the right day.
    assert "Saturday 24 October 2026, 12:00 (Europe/Stockholm)" in chat.requests[0][0].content


def test_a_refusal_says_not_done_so_the_model_cant_claim_it(
    client: TestClient, chat_id: str
) -> None:
    executor, chat = agent(
        client,
        call("remind", text="water the plants", at="2020-01-01T09:00"),
        call("forget", text="something never saved"),
        call("remember", text="x", kind="secret"),
        reply("That time has passed."),
    )

    result = run(executor, chat_id, "remind me")

    results = [request[-1].content for request in chat.requests[1:]]
    assert "not done: That time has already passed." in results[0]
    assert "not done: I don't have a memory about that." in results[1]
    assert "error: kind" in results[2]
    assert [c["ok"] for c in result.output["tool_calls"]] == [True, True, False]
    assert client.app.state.schedule_service.list() == []


def test_research_and_shopping_get_the_users_own_message(client: TestClient, chat_id: str) -> None:
    executor, _ = agent(client, call("shopping"), reply("Checking your Lidl spending."))
    run(executor, chat_id, "how much did I spend at Lidl last month?")
    executor, _ = agent(client, call("research"), reply("Looking it up."))
    run(executor, chat_id, "cheapest trains to Copenhagen next week")

    tasks = client.get(f"/chat-sessions/{chat_id}/tasks").json()["tasks"]
    assert sorted((t["original_request"], t["required_capabilities"]) for t in tasks) == [
        ("cheapest trains to Copenhagen next week", ["web_research"]),
        ("how much did I spend at Lidl last month?", ["groceries"]),
    ]
    # A hand-off is not something the user said: no message is posted in their name.
    assert client.get(f"/chat-sessions/{chat_id}/messages").json()["messages"] == []
    assert {t["source_context"].get("handed_off_by") for t in tasks} == {"t"}


def test_coding_tools_report_runs_and_refuse_an_unknown_repository(
    client: TestClient, chat_id: str
) -> None:
    executor, chat = agent(
        client,
        call("coding_status"),
        call("coding_start", repository="nope", goal="add a login page"),
        call("coding_stop", run="abc"),
        reply("There's nothing running, and I don't know that repository."),
    )

    result = run(executor, chat_id, "start coding in nope")

    results = [request[-1].content for request in chat.requests[1:]]
    assert "error" not in results[0]
    assert "error:" in results[1] and "error: No run matches 'abc'" in results[2]
    assert [c["ok"] for c in result.output["tool_calls"]] == [True, False, False]
    assert client.get("/workflow-runs").json().get("workflow_runs", []) == []


def test_the_prompt_lists_the_tools_and_lets_the_agent_start_coding(
    client: TestClient, chat_id: str
) -> None:
    executor, chat = agent(client, reply("Hi."))

    run(executor, chat_id, "hi")

    system = "\n".join(m.content for m in chat.requests[0] if m.role == "system")
    for name in ("remember", "list_add", "note_add", "remind", "research", "coding_start"):
        assert f"- {name} " in system
    assert "coding agent\nyou start with coding_start" in system
    assert '"repository": "<one of: analytics-agent-playground, personal-assistant>"' in system
    assert "set up a coding workflow" not in system
    assert "offer to research it" not in system


def test_tools_are_offered_only_for_available_abilities(client: TestClient) -> None:
    box = toolbox(client, abilities={"notes", "memories"})

    assert sorted(box.names) == ["forget", "note_add", "remember"]
    assert "coding_start" not in box.describe()


def test_a_failing_or_unknown_tool_comes_back_as_an_error() -> None:
    def broken(arguments, context):
        raise RuntimeError("boom")

    box = Toolbox([AssistantTool("broken", "notes", "{}", "breaks", broken)])

    assert box.run("broken", {}, ToolContext()).text == "error: RuntimeError while running broken"
    assert box.run("nope", {}, ToolContext()).text == "error: there is no tool called 'nope'"
    with pytest.raises(ValueError, match="Duplicate"):
        Toolbox([AssistantTool("a", "notes", "{}", "", broken)] * 2)


def test_without_a_chat_bookkeeping_tools_refuse(client: TestClient) -> None:
    box = toolbox(client)

    outcome = box.run("remember", {"text": "x"}, ToolContext())

    assert outcome.text == "error: this only works inside a chat"
    assert client.get("/memories").json()["memories"] == []
