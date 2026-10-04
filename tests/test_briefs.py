from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.briefs import BriefWritebackJob, BriefWriter, BriefWriterError
from app.capabilities import CapabilityRegistry
from app.domain.enums import TaskStatus
from app.domain.work_items import Brief, render_work_item_context
from app.execution.base import ExecutionResult
from app.execution.coding import _with_work_item_context
from app.execution.conversation import ConversationExecutor
from app.integrations.chat import ChatCompletion, ChatMessage
from app.manager import DeterministicManager
from app.persistence.database import Database
from app.persistence.models import TaskModel, WorkItemEventModel, utc_now
from app.service import TaskService
from app.work_items import WorkItemService
from app.worker import TaskWorker

VALID_BRIEF = {
    "goal": "Ship voice login",
    "status_summary": "Design agreed; implementation not started",
    "decisions": ["Use the phone's speech recognizer"],
    "next_steps": ["Draft the login screen"],
    "open_questions": [],
}


class ScriptedChat:
    provider = "scripted"
    model = "scripted-model"

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.requests: list[list[ChatMessage]] = []

    def complete(self, messages: list[ChatMessage], *, max_tokens: int = 700) -> ChatCompletion:
        self.requests.append(list(messages))
        return ChatCompletion(text=self.replies.pop(0), model=self.model)

    def close(self) -> None:
        pass


# --- #mentions and slugs ---------------------------------------------------------


def test_mentions_focus_the_chat_and_unknown_tags_are_ignored(client: TestClient) -> None:
    item = client.post("/work-items", json={"title": "Add voice login to the app"}).json()
    chat = client.post("/chat-sessions", json={}).json()

    posted = client.post(
        f"/chat-sessions/{chat['id']}/messages",
        json={"content": f"Where are we on #{item['slug']}? Also #nothing-here."},
    ).json()

    assert item["slug"] == "add-voice-login"
    assert posted["focused_work_items"] == ["add-voice-login"]
    focus = client.get(f"/chat-sessions/{chat['id']}/focus").json()["work_items"]
    assert [entry["id"] for entry in focus] == [item["id"]]


def test_mentions_in_a_task_sent_to_a_chat_focus_it(client: TestClient) -> None:
    item = client.post("/work-items", json={"title": "Add voice login"}).json()
    chat = client.post("/chat-sessions", json={}).json()

    client.post(
        "/tasks",
        json={"request": "status of #add-voice-login?", "chat_session_id": chat["id"]},
    )

    focus = client.get(f"/chat-sessions/{chat['id']}/focus").json()["work_items"]
    assert [entry["id"] for entry in focus] == [item["id"]]


def test_slugs_are_short_and_can_be_renamed(client: TestClient) -> None:
    long = client.post(
        "/work-items",
        json={"title": "Update the icon on the light/dark mode toggle in the web console"},
    ).json()
    other = client.post("/work-items", json={"title": "Other"}).json()

    renamed = client.patch(f"/work-items/{long['id']}", json={"version": 1, "slug": "toggle-icon"})
    taken = client.patch(f"/work-items/{other['id']}", json={"version": 1, "slug": "toggle-icon"})
    invalid = client.patch(f"/work-items/{other['id']}", json={"version": 1, "slug": "Bad Tag"})

    assert long["slug"] == "update-icon-light-dark-mode"
    assert renamed.json()["slug"] == "toggle-icon"
    assert client.get("/work-items/toggle-icon").json()["id"] == long["id"]
    assert taken.status_code == 422
    assert invalid.status_code == 422


# --- context for the chat model and the coding agent --------------------------------


def test_work_item_context_reaches_the_conversation_model(
    service: TaskService, database: Database
) -> None:
    items = WorkItemService(database)
    chat = service.create_chat_session()
    item = items.create(title="Voice login", chat_session_id=chat.id)
    item = items.update(item.id, expected_version=1, brief=Brief.model_validate(VALID_BRIEF))
    items.add_checklist_entry(item.id, "Pick a wake word")
    task = service.create_task(request="What's next?", chat_session_id=chat.id)
    seen: dict = {}

    class Conversation:
        id = "model-conversation"

        def execute(self, *, context: Mapping, **_kwargs) -> ExecutionResult:
            seen.update(context)
            return ExecutionResult(output={"summary": "ok", "reply": "ok"})

    executors = {"model-conversation": Conversation()}
    registry = CapabilityRegistry.from_directory("capabilities").restricted_to_adapters(executors)
    TaskWorker(
        service=service,
        manager=DeterministicManager(registry),
        executors=executors,
        worker_id="test",
    ).run_once()

    (context,) = seen["work_items"]
    assert context["slug"] == "voice-login"
    assert context["brief"]["next_steps"] == ["Draft the login screen"]
    assert context["open_checklist"] == ["Pick a wake word"]
    assert any("Brief updated" in entry for entry in context["recent_activity"])
    assert service.get_task(task.id).status == TaskStatus.COMPLETED.value

    rendered = render_work_item_context(seen["work_items"])
    messages = ConversationExecutor(ScriptedChat())._messages("What's next?", (), seen)
    assert any("Work items this conversation is about" in m.content for m in messages)
    assert "Next steps:\n- Draft the login screen" in rendered
    background = _with_work_item_context("Implement it", seen["work_items"])
    assert background.startswith("Implement it\n\nBACKGROUND FROM THE WORK ITEM")
    assert "#voice-login — Voice login (goal, open)" in background
    assert _with_work_item_context("Implement it", None) == "Implement it"


# --- brief writer -----------------------------------------------------------------


def test_brief_writer_accepts_fenced_json_after_reasoning() -> None:
    chat = ScriptedChat("<think>hmm</think>\n```json\n" + json.dumps(VALID_BRIEF) + "\n```")

    brief = BriefWriter(chat).propose(title="Voice login", kind="goal", brief={}, material="x")

    assert brief.next_steps == ["Draft the login screen"]
    assert "untrusted data" in chat.requests[0][0].content


def test_brief_writer_repairs_once_then_gives_up() -> None:
    repaired = ScriptedChat("not json", json.dumps(VALID_BRIEF))
    assert BriefWriter(repaired).propose(title="t", kind="goal", brief={}, material="x").goal
    assert "invalid" in repaired.requests[1][-1].content

    broken = ScriptedChat('{"goal": 1}', "still not json")
    with pytest.raises(BriefWriterError, match="after 2 attempts"):
        BriefWriter(broken).propose(title="t", kind="goal", brief={}, material="x")


# --- write-back job ---------------------------------------------------------------


def _job(database: Database, chat: ScriptedChat, *, minutes_later: int) -> BriefWritebackJob:
    later = utc_now() + timedelta(minutes=minutes_later)
    return BriefWritebackJob(database, BriefWriter(chat), idle_seconds=600, clock=lambda: later)


def _events(database: Database, item_id: str) -> list[str]:
    with database.session() as session:
        return [
            event.event_type
            for event in session.query(WorkItemEventModel)
            .filter(WorkItemEventModel.work_item_id == item_id)
            .order_by(WorkItemEventModel.sequence)
        ]


def test_quiet_chat_updates_the_brief_once(service: TaskService, database: Database) -> None:
    items = WorkItemService(database)
    chat = service.create_chat_session()
    item = items.create(title="Voice login", chat_session_id=chat.id)
    service.append_chat_message(chat.id, content="Let's use the phone's speech recognizer")

    assert _job(database, ScriptedChat(), minutes_later=1).run_due() == 0  # still talking

    writer = ScriptedChat(json.dumps(VALID_BRIEF))
    assert _job(database, writer, minutes_later=11).run_due() == 1
    assert _job(database, ScriptedChat(), minutes_later=12).run_due() == 0  # nothing new

    updated = items.get(item.id)
    assert updated.brief["decisions"] == ["Use the phone's speech recognizer"]
    assert updated.brief_synced_at is not None
    assert "speech recognizer" in writer.requests[0][1].content
    assert _events(database, item.id)[-1] == "BRIEF_UPDATED"


def test_finished_run_is_material_immediately(service: TaskService, database: Database) -> None:
    items = WorkItemService(database)
    item = items.create(title="Voice login")
    task = service.create_task(request="Draft the login screen", work_item_id=item.id)
    with database.session() as session, session.begin():
        session.get(TaskModel, task.id).status = TaskStatus.COMPLETED.value

    writer = ScriptedChat(json.dumps(VALID_BRIEF))

    assert _job(database, writer, minutes_later=0).run_due() == 1
    assert "RUN RESULT" in writer.requests[0][1].content


def test_failed_write_back_is_recorded_and_not_retried_forever(
    service: TaskService, database: Database
) -> None:
    items = WorkItemService(database)
    item = items.create(title="Voice login")
    task = service.create_task(request="Draft it", work_item_id=item.id)
    with database.session() as session, session.begin():
        session.get(TaskModel, task.id).status = TaskStatus.FAILED.value

    assert _job(database, ScriptedChat("x", "y"), minutes_later=0).run_due() == 0
    assert _job(database, ScriptedChat(), minutes_later=1).run_due() == 0

    assert _events(database, item.id).count("BRIEF_UPDATE_FAILED") == 1
    assert items.get(item.id).brief == Brief(goal="Voice login").model_dump(mode="json")


def test_a_concurrent_edit_wins_over_the_write_back(
    service: TaskService, database: Database
) -> None:
    items = WorkItemService(database)
    item = items.create(title="Voice login")
    task = service.create_task(request="Draft it", work_item_id=item.id)
    with database.session() as session, session.begin():
        session.get(TaskModel, task.id).status = TaskStatus.COMPLETED.value

    class EditingChat(ScriptedChat):
        def complete(self, messages, *, max_tokens: int = 700) -> ChatCompletion:
            current = items.get(item.id)
            items.update(item.id, expected_version=current.version, title="Voice login v2")
            return super().complete(messages, max_tokens=max_tokens)

    assert _job(database, EditingChat(json.dumps(VALID_BRIEF)), minutes_later=0).run_due() == 0

    current = items.get(item.id)
    assert current.title == "Voice login v2"
    assert current.brief_synced_at is None
    assert "BRIEF_UPDATED" not in _events(database, item.id)


def test_brief_writeback_runs_off_the_task_loop(service: TaskService) -> None:
    import threading
    import time

    started = threading.Event()

    class SlowJob:
        def run_due(self) -> int:
            started.set()
            time.sleep(2)
            return 0

    executors = {"model-conversation": object()}
    registry = CapabilityRegistry.from_directory("capabilities").restricted_to_adapters(executors)
    worker = TaskWorker(
        service=service,
        manager=DeterministicManager(registry),
        executors=executors,  # type: ignore[arg-type]
        worker_id="test",
        brief_writeback=SlowJob(),  # type: ignore[arg-type]
        brief_writeback_interval_seconds=0.01,
    )

    thread = worker.start_brief_writeback()
    assert thread is not None and started.wait(timeout=1)
    began = time.monotonic()
    assert worker.run_once() is False
    assert time.monotonic() - began < 0.5

    coding_worker = TaskWorker(
        service=service,
        manager=DeterministicManager(registry),
        executors=executors,  # type: ignore[arg-type]
        worker_id="coding",
        coding_only=True,
        brief_writeback=SlowJob(),  # type: ignore[arg-type]
    )
    assert coding_worker.start_brief_writeback() is None
    worker.stop()
