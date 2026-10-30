from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from fastapi.testclient import TestClient

from app.delivery import DeliveryService
from app.notify import NtfyNotifier
from app.persistence.database import Database
from app.schedules import Recurrence, ScheduleKind, Scheduler, ScheduleService, next_occurrence
from app.services import TaskService
from tests.test_direct_actions import _do


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, int]] = []

    def send(self, title: str, message: str, *, priority: int = 3, path: str = "") -> bool:
        self.sent.append((title, message, priority))
        return True


class FailingNotifier:
    """A push that never reaches the phone (ntfy down, topic misconfigured, etc)."""

    def send(self, title: str, message: str, *, priority: int = 3, path: str = "") -> bool:
        return False


# --- recurrence -------------------------------------------------------------------


def test_daily_keeps_local_time_across_daylight_saving() -> None:
    # 2026-10-25: Sweden leaves summer time (UTC+2 → UTC+1).
    nine_local_summer = datetime(2026, 10, 24, 7, 0, tzinfo=UTC)

    following = next_occurrence(nine_local_summer, Recurrence.DAILY, "Europe/Stockholm")

    assert following == datetime(2026, 10, 25, 8, 0, tzinfo=UTC)  # still 09:00 local


def test_weekdays_skip_the_weekend_and_monthly_clamps() -> None:
    friday = datetime(2026, 10, 9, 7, 0, tzinfo=UTC)
    jan_31 = datetime(2027, 1, 31, 8, 0, tzinfo=UTC)

    assert next_occurrence(friday, Recurrence.WEEKDAYS, "UTC").weekday() == 0
    assert next_occurrence(jan_31, Recurrence.MONTHLY, "UTC") == datetime(
        2027, 2, 28, 8, 0, tzinfo=UTC
    )
    assert next_occurrence(friday, Recurrence.NONE, "UTC") is None


# --- scheduler ----------------------------------------------------------------------


def test_reminders_post_push_and_close_or_repeat(database: Database, service: TaskService) -> None:
    chat = service.create_chat_session()
    schedules = ScheduleService(database)
    now = datetime(2026, 10, 5, 7, 0, tzinfo=UTC)
    once = schedules.create(
        kind=ScheduleKind.REMINDER,
        message="Call mum",
        run_at=now - timedelta(minutes=1),
        chat_session_id=chat.id,
    )
    daily = schedules.create(
        kind=ScheduleKind.REMINDER,
        message="Stand-up",
        run_at=now - timedelta(days=3),  # three missed days: fire once, not three times
        recurrence=Recurrence.DAILY,
    )
    later = schedules.create(
        kind=ScheduleKind.REMINDER, message="Later", run_at=now + timedelta(hours=1)
    )
    notifier = RecordingNotifier()

    scheduler = Scheduler(database, clock=lambda: now)
    delivery = DeliveryService(
        database,
        service,
        start_run=lambda _schedule, _key: None,
        notifier=notifier,
    )

    assert scheduler.run_due() == 2
    assert scheduler.run_due() == 0
    assert delivery.run_due() == 2
    assert [(title, message) for title, message, _ in notifier.sent] == [
        ("Reminder", "Reminder: Stand-up"),
        ("Reminder", "Reminder: Call mum"),
    ]
    messages = service.list_chat_messages(chat.id)
    assert [message.content for message in messages] == ["Reminder: Call mum"]
    active = {schedule.id: schedule for schedule in schedules.list()}
    assert once.id not in active
    assert active[daily.id].next_run_at.replace(tzinfo=UTC) > now
    assert later.id in active


def test_failed_reminder_push_is_logged_with_the_schedule_id(
    database: Database, service: TaskService, caplog
) -> None:
    chat = service.create_chat_session()
    schedules = ScheduleService(database)
    now = datetime(2026, 10, 5, 7, 0, tzinfo=UTC)
    reminder = schedules.create(
        kind=ScheduleKind.REMINDER,
        message="Call mum",
        run_at=now - timedelta(minutes=1),
        chat_session_id=chat.id,
    )

    Scheduler(database, clock=lambda: now).run_due()
    delivery = DeliveryService(
        database,
        service,
        start_run=lambda _schedule, _key: None,
        notifier=FailingNotifier(),
    )
    with caplog.at_level("WARNING"):
        assert delivery.run_due() == 0

    assert any(reminder.id in record.message for record in caplog.records)

    # With push turned off there is nothing to report.
    caplog.clear()
    schedules.create(
        kind=ScheduleKind.REMINDER, message="Water plants", run_at=now - timedelta(minutes=1)
    )
    Scheduler(database, clock=lambda: now).run_due()
    with caplog.at_level("WARNING"):
        assert (
            DeliveryService(database, service, start_run=lambda _schedule, _key: None).run_due()
            == 1
        )
    assert not [r for r in caplog.records if "push failed" in r.message]


def test_routines_start_a_run(database: Database, service: TaskService) -> None:
    schedules = ScheduleService(database)
    now = datetime(2026, 10, 5, 7, 0, tzinfo=UTC)
    schedules.create(
        kind=ScheduleKind.ROUTINE,
        message="Summarize my open work",
        run_at=now,
        recurrence=Recurrence.WEEKLY,
    )
    started: list[str] = []

    def start_run(schedule, _external_key: str) -> str:
        started.append(schedule.message)
        return service.create_task(request=schedule.message).id

    notifier = RecordingNotifier()
    Scheduler(database, clock=lambda: now).run_due()
    DeliveryService(database, service, start_run=start_run, notifier=notifier).run_due()

    assert started == ["Summarize my open work"]
    # The run pushes its result when it finishes (worker), not that it started.
    assert notifier.sent == []


# --- ntfy -----------------------------------------------------------------------------


def test_ntfy_posts_title_priority_and_click_link() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    notifier = NtfyNotifier(
        "https://ntfy.sh/assistant-test",
        token="tk",
        public_url="https://assistant.example.com/",
        transport=httpx.MockTransport(handler),
    )

    assert notifier.send("Ready for your review ✓", "PR #14", priority=4, path="/?task=1")
    request = seen[0]
    assert request.content == b"PR #14"
    assert request.headers["Priority"] == "4"
    assert request.headers["Click"] == "https://assistant.example.com/?task=1"
    assert request.headers["Authorization"] == "Bearer tk"
    assert request.headers["Title"].startswith("=?UTF-8?B?")

    failing = NtfyNotifier(
        "https://ntfy.sh/x", transport=httpx.MockTransport(lambda _r: httpx.Response(500))
    )
    assert failing.send("t", "m") is False


# --- reminders from the agent's tool and the API --------------------------------------------------


def test_reminder_from_chat_uses_the_users_timezone(client: TestClient) -> None:
    item = client.post("/work-items", json={"title": "Family"}).json()
    chat = client.post("/chat-sessions", json={}).json()

    reply = _do(
        client,
        chat["id"],
        timezone="Europe/Stockholm",
        type="remind",
        text="call mum",
        at="2099-01-05T09:00",
        work_item=item["slug"],
    )

    assert reply == "I'll remind you Mon 05 Jan 09:00: call mum"
    (schedule,) = client.get("/schedules", params={"work_item_id": item["slug"]}).json()[
        "schedules"
    ]
    assert schedule["next_run_at"].startswith("2099-01-05T08:00")  # 09:00 CET
    assert schedule["timezone"] == "Europe/Stockholm"
    assert schedule["chat_session_id"] == chat["id"]

    cancelled = client.delete(f"/schedules/{schedule['id']}").json()
    assert cancelled["active"] is False
    assert client.get("/schedules").json()["schedules"] == []


def test_past_reminders_are_refused(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()

    reply = _do(client, chat["id"], type="remind", text="x", at="2001-01-01T09:00")

    assert reply == "That time has already passed."
    assert client.get("/schedules").json()["schedules"] == []


def test_schedules_can_be_created_through_the_api(client: TestClient) -> None:
    created = client.post(
        "/schedules",
        json={
            "kind": "routine",
            "message": "Review spending",
            "run_at": "2099-02-01T08:00:00+01:00",
            "recurrence": "monthly",
            "timezone": "Europe/Stockholm",
        },
    )
    bad_zone = client.post(
        "/schedules",
        json={"message": "x", "run_at": "2099-02-01T08:00:00", "timezone": "Mars/Base"},
    )

    assert created.status_code == 201
    assert created.json()["next_run_at"].startswith("2099-02-01T07:00")
    assert bad_zone.status_code == 422
