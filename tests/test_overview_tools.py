"""Looking across every chat: overview, chats and chat_read, and the Assistant marker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.assistant.supervisor import SupervisorTools
from app.assistant.toolbox import Toolbox, ToolContext
from app.assistant.tools import overview_tools
from app.execution.conversation import HOME_CONVERSATION, ConversationExecutor
from app.schedules import Recurrence, ScheduleKind
from tests.fakes import ScriptedChat


def _box(client: TestClient) -> Toolbox:
    state = client.app.state
    supervisor = SupervisorTools(
        state.database, state.task_service, state.workflow_service, state.repository_registry
    )
    return Toolbox(overview_tools(state.database, supervisor))


def _run(client: TestClient, tool: str, **arguments) -> str:
    return _box(client).run(tool, arguments, ToolContext(chat_session_id=None)).text


def _finish(service, task_id: str, reply: str) -> None:
    claimed = service.claim_next_task(worker_id="t", lease_seconds=30)
    assert claimed.id == task_id
    execution = service.start_execution(
        task_id, capability_id="tool-agent", executor_id="tool-agent", execution_input={}
    )
    service.start_validation(task_id, execution, output={"summary": reply})
    service.complete_task(task_id, execution, reply=reply)


def test_overview_shows_background_work_across_chats_and_the_routines(client: TestClient) -> None:
    service = client.app.state.task_service
    trip = service.create_chat_session(title="Shanghai trip").id
    museum = service.create_chat_session(title="Moderna Museet").id
    done = service.create_task(
        request="opening hours of Moderna Museet",
        required_capabilities=["web_research"],
        chat_session_id=museum,
        post_request=False,
    )
    _finish(service, done.id, "Open 10–18, Fridays until 20. Closed Mondays.")
    service.create_task(
        request="best xiaolongbao in Shanghai",
        required_capabilities=["web_research"],
        chat_session_id=trip,
        post_request=False,
    )
    client.app.state.schedule_service.create(
        kind=ScheduleKind.ROUTINE,
        message="summarize my Klarna spending",
        run_at=datetime.now(UTC) + timedelta(days=1),
        recurrence=Recurrence.WEEKLY,
        timezone="Europe/Stockholm",
    )

    text = _run(client, "overview")

    running, finished = text.split("FINISHED IN THE LAST 24 HOURS")
    assert "research: “best xiaolongbao in Shanghai” (in “Shanghai trip”)" in running
    assert "Moderna Museet" in finished and "Closed Mondays" in finished
    assert "summarize my Klarna spending" in text  # the routine, with its next run
    assert "coding runs" in text.lower()  # the coding overview, reused


def test_chats_and_chat_read_see_other_conversations(client: TestClient) -> None:
    service = client.app.state.task_service
    trip = service.create_chat_session(title="Shanghai trip").id
    service.append_chat_message(trip, content="which street foods should I try?")
    service.append_chat_message(
        trip, content="Xiaolongbao and shengjian bao, then tangyuan.", role="assistant"
    )

    listed = _run(client, "chats")
    read = _run(client, "chat_read", chat="the shanghai one")

    assert "“Shanghai trip”" in listed and "Xiaolongbao" in listed
    assert read.startswith("“Shanghai trip”")
    assert "user: which street foods should I try?" in read
    missed = _run(client, "chat_read", chat="zebra")
    assert "look with chats" in missed  # a clear miss, with where to look instead


def test_only_the_assistant_conversation_is_told_it_speaks_for_everything(
    client: TestClient,
) -> None:
    service = client.app.state.task_service
    home = service.home_chat().id
    other = service.create_chat_session().id

    assert service.is_home_chat(home) and not service.is_home_chat(other)
    executor = ConversationExecutor(ScriptedChat())
    home_prompt = executor._messages("what's going on?", [], {"home": True})
    other_prompt = executor._messages("what's going on?", [], {})
    assert any(m.content == HOME_CONVERSATION for m in home_prompt)
    assert not any(m.content == HOME_CONVERSATION for m in other_prompt)
