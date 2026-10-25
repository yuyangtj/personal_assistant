"""The assistant's memory of recent days: one rolling summary across all chats."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.execution.conversation import ConversationExecutor
from app.services import TaskService
from app.work.digest import DigestJob, DigestWriter, current, material_since
from tests.fakes import ScriptedChat


def _summary(text: str) -> str:
    return json.dumps({"summary": text})


def _job(service: TaskService, model, *, at: datetime) -> DigestJob:
    return DigestJob(service.database, DigestWriter(model), clock=lambda: at)


def test_it_waits_for_the_chats_to_go_quiet_then_summarizes_all_of_them(
    service: TaskService,
) -> None:
    trip = service.create_chat_session(title="Shanghai trip").id
    home = service.home_chat().id
    service.append_chat_message(trip, content="I'm flying to Shanghai on 12 November.")
    service.append_chat_message(home, content="remind me to pack on the 11th")
    now = datetime.now(UTC)
    model = ScriptedChat(_summary("Flying to Shanghai on 12 November; packing on the 11th."))

    assert _job(service, model, at=now + timedelta(minutes=2)).run_due() is False
    assert _job(service, model, at=now + timedelta(minutes=11)).run_due() is True

    digest = current(service.database)
    assert digest.content == "Flying to Shanghai on 12 November; packing on the 11th."
    sent = json.loads(model.requests[0][1].content)
    assert "· Shanghai trip] user: I'm flying to Shanghai" in sent["new_messages"]
    assert "· Assistant] user: remind me to pack" in sent["new_messages"]
    # Nothing new since: nothing to do, and no model call.
    assert _job(service, model, at=now + timedelta(minutes=30)).run_due() is False
    assert len(model.requests) == 1


def test_the_next_summary_builds_on_the_last_and_only_new_messages(service: TaskService) -> None:
    chat = service.create_chat_session(title="Plans").id
    service.append_chat_message(chat, content="dentist on Monday at 9")
    later = datetime.now(UTC) + timedelta(minutes=11)
    _job(service, ScriptedChat(_summary("Dentist Monday at 9.")), at=later).run_due()

    service.append_chat_message(chat, content="and the plumber on Thursday at 10")
    model = ScriptedChat(_summary("Dentist Monday at 9; plumber Thursday at 10."))
    assert _job(service, model, at=later + timedelta(minutes=30)).run_due() is True

    sent = json.loads(model.requests[0][1].content)
    assert sent["current_summary"] == "Dentist Monday at 9."
    assert "plumber" in sent["new_messages"] and "dentist" not in sent["new_messages"]


def test_a_failing_model_keeps_the_old_summary(service: TaskService) -> None:
    chat = service.create_chat_session().id
    service.append_chat_message(chat, content="hello")
    later = datetime.now(UTC) + timedelta(minutes=11)
    _job(service, ScriptedChat(_summary("Said hello.")), at=later).run_due()
    service.append_chat_message(chat, content="something new")

    broken = ScriptedChat("not json", "still not json")
    assert _job(service, broken, at=later + timedelta(minutes=30)).run_due() is False
    assert current(service.database).content == "Said hello."


def test_long_messages_are_clipped(service: TaskService) -> None:
    chat = service.create_chat_session().id
    service.append_chat_message(chat, content="x" * 5000)

    material, _ = material_since(service.database, None, now=datetime.now(UTC))

    assert len(material) < 600


def test_replies_get_the_summary_and_it_shows_on_memories(client: TestClient) -> None:
    executor = ConversationExecutor(ScriptedChat())
    prompt = executor._messages("when do I fly?", [], {"recent": "Flying on 12 November."})
    assert any("Flying on 12 November." in m.content for m in prompt if m.role == "system")
    assert not any("last few days" in m.content for m in executor._messages("hi", [], {}))

    assert client.get("/digest").json() == {"content": "", "updated_at": None}
    service = client.app.state.task_service
    chat = service.create_chat_session().id
    service.append_chat_message(chat, content="flying on the 12th")
    later = datetime.now(UTC) + timedelta(minutes=11)
    _job(service, ScriptedChat(_summary("Flying on the 12th.")), at=later).run_due()
    assert client.get("/digest").json()["content"] == "Flying on the 12th."
