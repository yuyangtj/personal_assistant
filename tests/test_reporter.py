"""Workers report to the Assistant: finished background work, gathered into one message
with links to the details, and a push only for the must-knows or when it's notable."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.assistant.reporter import Reporter
from app.capabilities import CapabilityRegistry
from app.execution.conversation import REPORT, ConversationExecutor
from app.manager import DeterministicManager
from app.services import TaskService
from app.worker import TaskWorker
from tests.test_conversation import RecordingChatClient
from tests.test_schedules import RecordingNotifier

LATER = datetime.now(UTC) + timedelta(hours=1)


class FailingChat(RecordingChatClient):
    def complete(self, messages, *, max_tokens: int = 700):
        raise RuntimeError("model down")


def _worker(service: TaskService, client, notifier) -> TaskWorker:
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


def _finish(service: TaskService, task_id: str, reply: str, *, fail: bool = False) -> None:
    claimed = service.claim_next_task(worker_id="t", lease_seconds=30)
    assert claimed.id == task_id
    execution = service.start_execution(
        task_id, capability_id="tool-agent", executor_id="tool-agent", execution_input={}
    )
    if fail:
        service.fail_task(task_id, error="boom", execution_id=execution)
        return
    service.start_validation(task_id, execution, output={"summary": reply})
    service.complete_task(task_id, execution, reply=reply)


def _research(service: TaskService, chat: str, question: str) -> str:
    return service.create_task(
        request=question,
        required_capabilities=["web_research"],
        chat_session_id=chat,
        post_request=False,
    ).id


def _ready_pr(service: TaskService, chat: str) -> str:
    task = service.create_task(
        request="Fix the theme toggle",
        required_capabilities=["coding", "pull_request_creation"],
        source_context={"repository_id": "personal-assistant"},
        chat_session_id=chat,
    )
    service.claim_next_task(worker_id="w", lease_seconds=30)
    execution = service.start_execution(
        task.id,
        capability_id="coding-pull-request",
        executor_id="coding-pull-request",
        execution_input={},
    )
    service.start_validation(task.id, execution, output={"summary": "Opened a PR"})
    service.request_approval(
        task.id, execution, artifacts=[], approval={"number": 41, "url": "https://x/41"}
    )
    return task.id


def test_finished_work_is_gathered_into_one_report_once(service: TaskService) -> None:
    museum = service.create_chat_session(title="Moderna Museet").id
    trip = service.create_chat_session(title="Shanghai trip").id
    first = _research(service, museum, "opening hours of Moderna Museet")
    _finish(service, first, "Open 10–18, closed Mondays.")
    second = _research(service, trip, "best xiaolongbao")
    _finish(service, second, "Jia Jia Tang Bao on Huanghe Road.")
    reporter = Reporter(service, service.database)

    assert reporter.run_due() is None  # just finished: more may come in a moment
    report = reporter.run_due(now=LATER)
    assert reporter.run_due(now=LATER + timedelta(minutes=5)) is None  # never twice

    task = service.get_task(report)
    assert task.chat_session_id == service.home_chat().id
    items = task.source_context["report"]
    assert [(i["goal"], i["chat_title"], i["must_push"]) for i in items] == [
        ("opening hours of Moderna Museet", "Moderna Museet", False),
        ("best xiaolongbao", "Shanghai trip", False),
    ]
    assert "closed Mondays" in task.original_request


def test_replies_cancelled_work_and_work_from_assistant_are_not_reported(
    service: TaskService,
) -> None:
    chat = service.create_chat_session().id
    posted = service.append_chat_message(chat, content="hello")
    reply = service.create_task_from_message(chat, posted.message.id)
    _finish(service, reply.id, "Hi!")
    in_home = _research(service, service.home_chat().id, "weather tomorrow")
    _finish(service, in_home, "Sunny.")
    cancelled = _research(service, chat, "never mind")
    service.cancel_task(cancelled)

    assert Reporter(service, service.database).run_due(now=LATER) is None


def test_the_must_knows_are_a_pr_and_a_failure(service: TaskService) -> None:
    chat = service.create_chat_session(title="Theme").id
    _ready_pr(service, chat)
    failed = _research(service, chat, "train prices")
    _finish(service, failed, "", fail=True)

    report = Reporter(service, service.database).run_due(now=LATER)

    items = service.get_task(report).source_context["report"]
    assert [(i["kind"], i["status"], i["pull_request"], i["must_push"]) for i in items] == [
        ("coding run", "waiting_for_approval", 41, True),
        ("research", "failed", None, True),
    ]


def test_the_report_lands_in_assistant_with_links_and_pushes_only_when_it_should(
    service: TaskService,
) -> None:
    museum = service.create_chat_session(title="Moderna Museet").id
    _finish(service, _research(service, museum, "opening hours"), "Open 10–18.")
    model = RecordingChatClient(['{"reply": "The museum research is back.", "notable": false}'])
    notifier = RecordingNotifier()
    worker = _worker(service, model, notifier)

    Reporter(service, service.database).run_due(now=LATER)
    worker.run_once()

    assert any(m.content == REPORT for m in model.calls[0])
    (message,) = [
        m for m in service.list_chat_messages(service.home_chat().id) if m.role == "assistant"
    ]
    assert message.content == "The museum research is back."
    assert message.blocks[-1] == {
        "type": "link",
        "label": "Moderna Museet",
        "href": f"#/chats/{museum}",
    }
    assert notifier.sent == []  # finished research waits quietly in Assistant

    _ready_pr(service, museum)
    model.replies.append('{"reply": "PR 41 is ready for you.", "notable": false}')
    Reporter(service, service.database).run_due(now=LATER + timedelta(minutes=10))
    worker.run_once()
    assert notifier.sent == [("Assistant", "PR 41 is ready for you.", 4)]  # one push, no more


def test_without_a_model_the_report_is_still_told_plainly(service: TaskService) -> None:
    chat = service.create_chat_session(title="Theme").id
    task = _ready_pr(service, chat)
    notifier = RecordingNotifier()
    worker = _worker(service, FailingChat([]), notifier)

    Reporter(service, service.database).run_due(now=LATER)
    worker.run_once()

    message = [
        m for m in service.list_chat_messages(service.home_chat().id) if m.role == "assistant"
    ][-1]
    assert "Ready for your review: PR #41" in message.content
    assert message.blocks[-1]["href"] == f"#/chats/{chat}?task={task}"
    assert [(title, priority) for title, _, priority in notifier.sent] == [("Assistant", 4)]
