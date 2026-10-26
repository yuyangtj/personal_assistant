"""Short chat titles: a long first message is renamed by a model, in a few words."""

from __future__ import annotations

import json

from app.chat.titles import TitleJob, TitleWriter
from app.services import TaskService
from tests.fakes import ScriptedChat

LONG = (
    "can you look into why I didn't get the push notification for my dentist reminder "
    "this morning, it worked last week"
)


def _title(text: str) -> str:
    return json.dumps({"title": text})


def _chat(service: TaskService, chat_id: str):
    return next(c for c in service.list_chat_sessions() if c.id == chat_id)


def test_a_long_first_message_gets_a_short_title_without_moving_the_chat(
    service: TaskService,
) -> None:
    chat = service.create_chat_session().id
    service.append_chat_message(chat, content=LONG)
    service.append_chat_message(chat, role="assistant", content="Let me look at the logs.")
    before = _chat(service, chat)
    assert before.title.endswith("…") and before.title_pending

    model = ScriptedChat(_title('"Missed reminder push."'))
    assert TitleJob(service.database, TitleWriter(model)).run_due() == 1

    after = _chat(service, chat)
    assert after.title == "Missed reminder push"
    assert not after.title_pending
    assert after.updated_at == before.updated_at
    sent = json.loads(model.requests[0][1].content)["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant"]
    assert sent[0]["text"] == LONG
    # Named once: nothing more to do, and no more model calls.
    assert TitleJob(service.database, TitleWriter(model)).run_due() == 0
    assert len(model.requests) == 1


def test_short_first_messages_home_and_given_titles_are_kept(service: TaskService) -> None:
    short = service.create_chat_session().id
    service.append_chat_message(short, content="Groceries for Sunday")
    named = service.create_chat_session(title="Shanghai trip").id
    service.append_chat_message(named, content=LONG)
    home = service.home_chat().id
    service.append_chat_message(home, content=LONG)

    model = ScriptedChat()
    assert TitleJob(service.database, TitleWriter(model)).run_due() == 0
    assert model.requests == []
    assert _chat(service, short).title == "Groceries for Sunday"
    assert _chat(service, named).title == "Shanghai trip"


def test_a_bad_answer_is_corrected_and_a_failure_keeps_the_long_title(
    service: TaskService,
) -> None:
    fixed = service.create_chat_session().id
    service.append_chat_message(fixed, content=LONG)
    model = ScriptedChat("Push bug", _title("x" * 60), _title("Push bug"))
    # Not JSON, then too long: the third answer is used.
    writer = TitleWriter(model, max_attempts=3)
    assert TitleJob(service.database, writer).run_due() == 1
    assert _chat(service, fixed).title == "Push bug"

    broken = service.create_chat_session().id
    service.append_chat_message(broken, content=LONG + " again")
    job = TitleJob(service.database, TitleWriter(ScriptedChat(RuntimeError("down"))))
    assert job.run_due() == 0
    assert _chat(service, broken).title.endswith("…")
    # Not retried on every pass while the provider is down.
    assert job.run_due() == 0
