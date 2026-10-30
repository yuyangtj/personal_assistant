"""Reminders and alarms the Android app rings itself, and the push it then doesn't need."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.delivery import DeliveryService
from app.persistence.database import Database
from app.schedules import (
    Recurrence,
    ScheduleKind,
    Scheduler,
    ScheduleService,
    mark_on_phone,
    phone_alarms,
)
from app.services import TaskService
from tests.test_direct_actions import _do
from tests.test_schedules import RecordingNotifier

NOW = datetime(2026, 10, 26, 18, 0, tzinfo=UTC)


def test_the_phone_gets_two_weeks_of_reminders_and_alarms(database: Database) -> None:
    schedules = ScheduleService(database)
    wake = schedules.create(
        kind=ScheduleKind.ALARM,
        message="Wake up",
        run_at=NOW + timedelta(hours=12),
        recurrence=Recurrence.DAILY,
    )
    call = schedules.create(
        kind=ScheduleKind.REMINDER, message="Call mum", run_at=NOW + timedelta(days=4)
    )
    schedules.create(kind=ScheduleKind.REMINDER, message="Far", run_at=NOW + timedelta(days=30))
    schedules.create(
        kind=ScheduleKind.ROUTINE, message="Summarize", run_at=NOW + timedelta(hours=1)
    )

    rings = phone_alarms(database, now=NOW)

    assert [r for r in rings if r["schedule_id"] == call.id] == [
        {
            "id": f"{call.id}@{int((NOW + timedelta(days=4)).timestamp())}",
            "schedule_id": call.id,
            "at": int((NOW + timedelta(days=4)).timestamp() * 1000),
            "kind": "reminder",
            "text": "Call mum",
        }
    ]
    daily = [r for r in rings if r["schedule_id"] == wake.id]
    assert len(daily) == 14 and {r["kind"] for r in daily} == {"alarm"}
    assert {r["text"] for r in rings} == {"Wake up", "Call mum"}  # not too far, no routines
    assert [r["at"] for r in rings] == sorted(r["at"] for r in rings)


def test_what_the_phone_has_gets_no_push_and_alarms_stay_out_of_the_chat(
    database: Database, service: TaskService
) -> None:
    chat = service.create_chat_session()
    schedules = ScheduleService(database)
    alarm = schedules.create(
        kind=ScheduleKind.ALARM,
        message="Wake up",
        run_at=NOW - timedelta(minutes=1),
        recurrence=Recurrence.DAILY,
        chat_session_id=chat.id,
    )
    schedules.create(
        kind=ScheduleKind.REMINDER,
        message="Call mum",
        run_at=NOW - timedelta(minutes=1),
        chat_session_id=chat.id,
    )
    mark_on_phone(database, {alarm.id: int((NOW + timedelta(days=1)).timestamp() * 1000)})
    notifier = RecordingNotifier()

    scheduler = Scheduler(database, clock=lambda: NOW)
    assert scheduler.run_due() == 2
    DeliveryService(
        database, service, start_run=lambda _schedule, _key: None, notifier=notifier
    ).run_due()

    # The phone rings the alarm; the reminder it never picked up goes through ntfy.
    assert notifier.sent == [("Reminder", "Reminder: Call mum", 4)]
    assert [m.content for m in service.list_chat_messages(chat.id)] == ["Reminder: Call mum"]

    # A day on, the phone still has it; the day after is past what it had: pushed, loudly.
    for days in (1, 2):
        at = NOW + timedelta(days=days)
        Scheduler(database, clock=lambda at=at: at).run_due()
        DeliveryService(
            database, service, start_run=lambda _schedule, _key: None, notifier=notifier
        ).run_due()
    assert notifier.sent[1:] == [("Alarm", "Alarm: Wake up", 5)]


def test_the_app_lists_and_confirms_through_the_api(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()["id"]
    reply = _do(client, chat, type="alarm", text="Wake up", at="2099-01-09T08:00")
    assert reply == "Alarm set for Fri 09 Jan 08:00: Wake up (it rings on the phone)"

    alarms = client.get("/phone/alarms").json()["alarms"]
    # 2099 is beyond two weeks: nothing yet. A sooner one is listed.
    assert alarms == []
    soon = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    _do(client, chat, type="alarm", text="Train", at=soon)
    (train,) = client.get("/phone/alarms").json()["alarms"]
    assert train["kind"] == "alarm" and train["text"] == "Train"

    marked = client.post(
        "/phone/alarms/ack", json={"until": {train["schedule_id"]: train["at"]}}
    ).json()
    assert marked == {"marked": 1}
    # Unknown ids are ignored.
    assert client.post("/phone/alarms/ack", json={"until": {"nope": 1}}).json() == {"marked": 0}


def test_postgres_allows_every_schedule_kind() -> None:
    # Production is Postgres, with a check on schedules.kind that SQLite tests don't have:
    # "alarm" was refused there until migration 030. The latest check must list them all.
    import re
    from pathlib import Path

    checks = [
        match
        for path in sorted(Path("migrations").glob("*.sql"))
        for match in re.findall(
            r"ck_schedules_kind\s+CHECK\s*\(kind IN \(([^)]*)\)", path.read_text()
        )
    ]
    allowed = set(re.findall(r"'(\w+)'", checks[-1]))
    assert {kind.value for kind in ScheduleKind} <= allowed
