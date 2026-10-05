"""Routines as repeatable skills: routed once, compared with earlier runs, results pushed."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.capabilities import CapabilityRegistry
from app.chat.routines import start_routine
from app.chat.triage import Triager
from app.execution.conversation import ConversationExecutor
from app.manager import DeterministicManager
from app.repositories import RepositoryRegistry
from app.schedules import Recurrence, ScheduleKind, ScheduleService
from app.services import TaskService
from app.worker import TaskWorker
from tests.test_conversation import RecordingChatClient
from tests.test_schedules import RecordingNotifier
from tests.test_triage import ScriptedChat, suggestion

NOW = datetime(2026, 10, 5, 7, 0, tzinfo=UTC)


def _routine(service: TaskService, message: str, *, quiet: bool = False, chat: str | None = None):
    return ScheduleService(service.database).create(
        kind=ScheduleKind.ROUTINE,
        message=message,
        run_at=NOW,
        recurrence=Recurrence.WEEKLY,
        chat_session_id=chat,
        capabilities=[],
        quiet=quiet,
    )


def _worker(service: TaskService, client: RecordingChatClient, notifier) -> TaskWorker:
    executors = {"model-conversation": ConversationExecutor(client)}
    registry = CapabilityRegistry.from_directory("capabilities").restricted_to_adapters(executors)
    return TaskWorker(
        service=service,
        manager=DeterministicManager(registry),
        executors=executors,
        worker_id="test-worker",
        lease_seconds=30,
        poll_interval_seconds=0.01,
        notifier=notifier,
    )


def _say(client: TestClient, chat: str, text: str) -> dict:
    return client.post(
        f"/chat-sessions/{chat}/messages",
        json={"content": text, "local_time": "2026-10-05T09:00", "timezone": "Europe/Stockholm"},
    ).json()


def test_a_routine_saved_in_chat_is_routed_once_and_a_watch_is_quiet(client: TestClient) -> None:
    watch = "tell me when Zoégas coffee is under 50 kr at Willys"
    client.app.state.triager = Triager(
        RepositoryRegistry.from_directory("repositories"),
        ScriptedChat(
            suggestion(
                intent="direct_action",
                action={
                    "type": "routine",
                    "text": watch,
                    "at": "2026-10-12T08:00",
                    "recurrence": "weekly",
                },
            ),
            # Routing the instruction itself, once, at creation.
            suggestion(intent="answer", needs_grocery_tools=True),
        ),
        groceries_available=True,
    )
    chat = client.post("/chat-sessions", json={}).json()["id"]

    reply = _say(client, chat, f"every Monday at 8, {watch}")["decision"]["result"]

    assert "only notify you when it's worth knowing" in reply
    (routine,) = client.app.state.schedule_service.list()
    assert routine.capabilities == ["groceries"] and routine.quiet is True


def test_a_run_compares_with_earlier_runs_and_pushes_its_result(service: TaskService) -> None:
    routine = _routine(service, "Summarize my Klarna spending this month")
    model = RecordingChatClient(
        ['{"reply": "4,213 kr so far."}', '{"reply": "4,980 kr, up 18% on last time."}']
    )
    notifier = RecordingNotifier()
    worker = _worker(service, model, notifier)

    start_routine(service, routine)
    worker.run_once()
    start_routine(service, routine)
    worker.run_once()

    second_prompt = "\n".join(message.content for message in model.calls[1])
    assert "Earlier runs of this routine" in second_prompt and "4,213 kr so far." in second_prompt
    assert [body for _, body, _ in notifier.sent] == [
        "4,213 kr so far.",
        "4,980 kr, up 18% on last time.",
    ]
    assert notifier.sent[0][0] == "Routine: Summarize my Klarna spending this month"


def test_a_watch_pushes_only_when_the_result_is_notable(service: TaskService) -> None:
    routine = _routine(service, "tell me when coffee is under 50 kr", quiet=True)
    model = RecordingChatClient(
        [
            '{"reply": "Still 54 kr.", "notable": false}',
            '{"reply": "Down to 45 kr this week!", "notable": true}',
        ]
    )
    notifier = RecordingNotifier()
    worker = _worker(service, model, notifier)

    for _ in range(2):
        start_routine(service, routine)
        worker.run_once()

    assert "This routine is a watch" in "\n".join(m.content for m in model.calls[0])
    assert [body for _, body, _ in notifier.sent] == ["Down to 45 kr this week!"]


def test_an_old_routine_without_a_route_uses_keyword_rules(service: TaskService) -> None:
    routine = ScheduleService(service.database).create(
        kind=ScheduleKind.ROUTINE, message="Summarize my Klarna spending", run_at=NOW
    )
    fallback = Triager(RepositoryRegistry.from_directory("repositories"), groceries_available=True)

    task_id = start_routine(service, routine, fallback=fallback)

    assert service.get_task(task_id).required_capabilities == ["groceries"]


def test_routines_are_listed_run_and_stopped_from_chat(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()["id"]
    service = client.app.state.task_service
    _routine(service, "analyze my Klarna spending", chat=chat)
    _routine(service, "tell me when coffee is on offer at Willys", quiet=True, chat=chat)

    listed = _say(client, chat, "what routines do I have?")["decision"]["result"]
    ran = _say(client, chat, "run my klarna spending now")["decision"]["result"]
    stopped = _say(client, chat, "stop the coffee watch")["decision"]["result"]

    assert "analyze my Klarna spending" in listed and "(watch:" in listed
    messages = client.get(f"/chat-sessions/{chat}/messages").json()["messages"]
    choices = next(m for m in messages if m["content"].startswith("Your routines"))["blocks"]
    assert choices[0]["options"][0]["label"] == "Run “analyze my Klarna spending” now"
    assert ran == "Running now: analyze my Klarna spending"
    (task,) = client.get(f"/chat-sessions/{chat}/tasks").json()["tasks"]
    assert task["original_request"] == "analyze my Klarna spending"
    assert stopped == "Stopped: tell me when coffee is on offer at Willys"
    assert [r.message for r in client.app.state.schedule_service.list()] == [
        "analyze my Klarna spending"
    ]


def test_an_ambiguous_stop_asks_which_one(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()["id"]
    service = client.app.state.task_service
    _routine(service, "analyze my Klarna spending", chat=chat)
    _routine(service, "analyze my Willys spending", chat=chat)

    reply = _say(client, chat, "stop the spending routine")["decision"]["result"]

    assert reply.startswith("More than one routine matches")
    assert len(client.app.state.schedule_service.list()) == 2
