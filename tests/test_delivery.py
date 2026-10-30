from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.chat.routines import start_routine
from app.delivery import CLAIM_LEASE, MAX_ATTEMPTS, PUSH_STALE_AFTER, DeliveryService
from app.persistence.database import Database
from app.persistence.models import OutboxDeliveryModel, utc_now
from app.persistence.outbox import enqueue
from app.schedules import Recurrence, ScheduleKind, Scheduler, ScheduleService
from app.services import TaskService
from app.work.items import WorkItemService

NOW = datetime(2026, 10, 26, 18, 0, tzinfo=UTC)


def _delivery(
    service: TaskService,
    *,
    notifier=None,
    start_run=None,
    clock=utc_now,
) -> DeliveryService:
    return DeliveryService(
        service.database,
        service,
        start_run=start_run or (lambda _schedule, _key: None),
        notifier=notifier,
        clock=clock,
    )


def _pending(database: Database) -> list[OutboxDeliveryModel]:
    with database.session() as session:
        return list(session.scalars(select(OutboxDeliveryModel)))


def _after_the_lease() -> datetime:
    return utc_now() + CLAIM_LEASE + timedelta(seconds=1)


def _crash_before_completion(delivery: DeliveryService, monkeypatch: pytest.MonkeyPatch) -> None:
    def crash(_delivery_id: str) -> None:
        raise RuntimeError("process stopped after the delivery committed")

    monkeypatch.setattr(delivery, "_complete", crash)


def test_task_delivery_dedupes_a_crash_after_task_commit(
    service: TaskService, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = service.create_chat_session()
    with service.database.session() as session, session.begin():
        delivery_id = enqueue(
            session,
            kind="task",
            payload={
                "request": "Report the finished work",
                "chat_session_id": chat.id,
                "source_context": {"report": []},
            },
        )
    first = _delivery(service)
    _crash_before_completion(first, monkeypatch)

    with pytest.raises(RuntimeError, match="process stopped"):
        first.run_due()

    (created,) = service.list_tasks()
    assert len(service.list_chat_messages(chat.id)) == 1
    # Still claimed by the crashed worker: no one else delivers it until the lease ends.
    assert _delivery(service).run_due() == 0
    restarted = _delivery(service, clock=_after_the_lease)
    assert restarted.run_due() == 1
    assert [task.id for task in service.list_tasks()] == [created.id]
    assert (created.external_source, created.external_key) == ("reporter", delivery_id)
    assert len(service.list_chat_messages(chat.id)) == 1
    assert _pending(service.database) == []


def test_routine_delivery_dedupes_a_crash_after_task_commit(
    service: TaskService, monkeypatch: pytest.MonkeyPatch
) -> None:
    routine = ScheduleService(service.database).create(
        kind=ScheduleKind.ROUTINE,
        message="Summarize my open work",
        run_at=NOW,
        recurrence=Recurrence.WEEKLY,
    )
    Scheduler(service.database, clock=lambda: NOW).run_due()
    (outbox,) = _pending(service.database)

    def run(schedule, external_key: str) -> str:
        return start_routine(
            service,
            schedule,
            external_source="schedule",
            external_key=external_key,
        )

    first = _delivery(service, start_run=run)
    _crash_before_completion(first, monkeypatch)
    with pytest.raises(RuntimeError, match="process stopped"):
        first.run_due()

    restarted = _delivery(service, start_run=run, clock=_after_the_lease)
    assert restarted.run_due() == 1
    (task,) = service.list_tasks()
    assert task.original_request == routine.message
    assert (task.external_source, task.external_key) == ("schedule", outbox.id)
    assert task.source_context["schedule_id"] == routine.id
    assert _pending(service.database) == []


class FlakyNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, _title: str, message: str, *, priority: int = 3, path: str = "") -> bool:
        self.messages.append(message)
        return len(self.messages) > 1


def test_failed_push_retries_with_backoff_without_duplicating_the_chat_message(
    service: TaskService,
) -> None:
    now = utc_now()
    chat = service.create_chat_session()
    item = WorkItemService(service.database).create(title="Family")
    reminder = ScheduleService(service.database).create(
        kind=ScheduleKind.REMINDER,
        message="Call mum",
        run_at=NOW - timedelta(minutes=1),
        chat_session_id=chat.id,
        work_item_id=item.id,
    )
    Scheduler(service.database, clock=lambda: now).run_due()
    notifier = FlakyNotifier()
    now = utc_now()
    delivery = _delivery(service, notifier=notifier, clock=lambda: now)

    assert delivery.run_due() == 0
    (pending,) = _pending(service.database)
    assert pending.attempts == 1
    assert pending.available_at.replace(tzinfo=UTC) == now + timedelta(seconds=2)
    assert reminder.id in (pending.last_error or "")
    expected = "Reminder (#family): Call mum"
    assert [message.content for message in service.list_chat_messages(chat.id)] == [expected]

    assert delivery.run_due() == 0
    assert notifier.messages == [expected]
    now += timedelta(seconds=2)
    restarted = _delivery(service, notifier=notifier, clock=lambda: now)
    assert restarted.run_due() == 1
    assert notifier.messages == [expected, expected]
    assert [message.content for message in service.list_chat_messages(chat.id)] == [expected]
    assert _pending(service.database) == []


def test_null_notifier_completes_delivery(service: TaskService) -> None:
    ScheduleService(service.database).create(
        kind=ScheduleKind.REMINDER,
        message="Water plants",
        run_at=NOW - timedelta(minutes=1),
    )
    Scheduler(service.database, clock=lambda: NOW).run_due()

    assert _delivery(service).run_due() == 1
    assert _pending(service.database) == []


class DownNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, _title: str, message: str, *, priority: int = 3, path: str = "") -> bool:
        self.messages.append(message)
        return False


def _fired_reminder(service: TaskService) -> str:
    reminder = ScheduleService(service.database).create(
        kind=ScheduleKind.REMINDER,
        message="Call mum",
        run_at=utc_now() - timedelta(minutes=1),
    )
    Scheduler(service.database).run_due()
    return reminder.id


def test_a_failing_delivery_is_given_up_after_its_attempts(service: TaskService) -> None:
    with service.database.session() as session, session.begin():
        enqueue(session, kind="task", payload={"request": "x", "chat_session_id": "gone"})
    now = utc_now()
    for _ in range(MAX_ATTEMPTS):
        assert _delivery(service, clock=lambda at=now: at).run_due() == 0
        now += timedelta(seconds=301)

    assert _pending(service.database) == []
    assert service.list_tasks() == []


def test_a_push_still_failing_long_after_it_fired_is_skipped(service: TaskService) -> None:
    _fired_reminder(service)
    notifier = DownNotifier()
    assert _delivery(service, notifier=notifier).run_due() == 0
    assert len(notifier.messages) == 1

    late = utc_now() + PUSH_STALE_AFTER + timedelta(minutes=1)
    assert _delivery(service, notifier=notifier, clock=lambda: late).run_due() == 1
    assert len(notifier.messages) == 1  # not sent again, half an hour late
    assert _pending(service.database) == []


def test_cancelling_a_schedule_drops_its_undelivered_push(service: TaskService) -> None:
    reminder_id = _fired_reminder(service)
    notifier = DownNotifier()
    assert _delivery(service, notifier=notifier).run_due() == 0
    assert len(_pending(service.database)) == 1

    ScheduleService(service.database).cancel(reminder_id)

    assert _pending(service.database) == []
