"""Routines as repeatable skills: routed once, compared with earlier runs, results pushed."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.capabilities import CapabilityRegistry
from app.chat.routines import start_routine
from app.execution.conversation import ConversationExecutor
from app.manager import DeterministicManager
from app.schedules import Recurrence, ScheduleKind, ScheduleService
from app.services import TaskService
from app.worker import TaskWorker
from tests.test_conversation import RecordingChatClient
from tests.test_direct_actions import _do
from tests.test_schedules import RecordingNotifier

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


def test_a_routine_saved_in_chat_runs_the_chat_agent_and_a_watch_is_quiet(
    client: TestClient,
) -> None:
    watch = "tell me when Zoégas coffee is under 50 kr at Willys"
    chat = client.post("/chat-sessions", json={}).json()["id"]

    reply = _do(
        client, chat, type="routine", text=watch, at="2099-10-12T08:00", recurrence="weekly"
    )

    assert "only notify you when it's worth knowing" in reply
    (routine,) = client.app.state.schedule_service.list()
    assert routine.capabilities is None and routine.quiet is True
    # Each run is the chat agent, with its tools (the store tools among them).
    task_id = start_routine(client.app.state.task_service, routine)
    assert not client.app.state.task_service.get_task(task_id).required_capabilities


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


def test_an_old_routine_goes_to_the_chat_agent_unless_it_is_research(
    service: TaskService,
) -> None:
    schedules = ScheduleService(service.database)

    def started(capabilities: list[str]):
        routine = schedules.create(
            kind=ScheduleKind.ROUTINE, message="x", run_at=NOW, capabilities=capabilities
        )
        return service.get_task(start_routine(service, routine)).required_capabilities

    # The shopping agent and the supervisor are gone; the chat agent has their tools.
    assert not started(["groceries"]) and not started(["supervision"])
    assert started(["web_research"]) == ["web_research"]


def test_routines_are_listed_run_and_stopped_from_chat(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()["id"]
    service = client.app.state.task_service
    _routine(service, "analyze my Klarna spending", chat=chat)
    _routine(service, "tell me when coffee is on offer at Willys", quiet=True, chat=chat)

    listed = _do(client, chat, type="list_routines", text="all")
    ran = _do(client, chat, type="run_routine", text="klarna spending")
    stopped = _do(client, chat, type="cancel_routine", text="coffee watch")

    assert "analyze my Klarna spending" in listed and "(watch:" in listed
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

    reply = _do(client, chat, type="cancel_routine", text="spending routine")

    assert reply.startswith("More than one routine matches")
    assert len(client.app.state.schedule_service.list()) == 2
