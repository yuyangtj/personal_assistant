"""Reminders, alarms and routines: stored schedules the worker runs when they are due.

A reminder posts into its chat and rings on the phone; an alarm only rings. The Android
app schedules both itself (see app/api/phone.py); a push through ntfy is sent only for the
ones it hasn't picked up. A routine starts a run (a chat
task linked to its work item) with the scheduled message as the request. Times are
stored in UTC and recurrences are computed in the schedule's own timezone, so "every
day at 9" stays at 9 across daylight-saving changes.
"""

from __future__ import annotations

import calendar
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select

from app.persistence.database import Database
from app.persistence.models import OutboxDeliveryModel, ScheduleModel, utc_now
from app.persistence.outbox import enqueue, schedule_payload
from app.work.items import get_work_item

logger = logging.getLogger(__name__)


class ScheduleKind(StrEnum):
    REMINDER = "reminder"
    ROUTINE = "routine"
    #: Rings on the phone like a clock alarm (full screen, until dismissed).
    ALARM = "alarm"


class Recurrence(StrEnum):
    NONE = "none"
    DAILY = "daily"
    WEEKDAYS = "weekdays"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ScheduleNotFoundError(LookupError):
    pass


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"Unknown timezone: {name}") from error


def aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def next_occurrence(previous: datetime, recurrence: Recurrence, timezone: str) -> datetime | None:
    """The next run after ``previous`` (UTC), keeping the same local wall-clock time."""
    if recurrence == Recurrence.NONE:
        return None
    local = aware(previous).astimezone(zone(timezone))
    if recurrence == Recurrence.DAILY:
        local = local + timedelta(days=1)
    elif recurrence == Recurrence.WEEKLY:
        local = local + timedelta(days=7)
    elif recurrence == Recurrence.WEEKDAYS:
        local = local + timedelta(days=1)
        while local.weekday() >= 5:
            local = local + timedelta(days=1)
    else:
        month = local.month % 12 + 1
        year = local.year + (1 if local.month == 12 else 0)
        day = min(local.day, calendar.monthrange(year, month)[1])
        local = local.replace(year=year, month=month, day=day)
    # Rebuild from the wall-clock fields so a DST change keeps "9:00" at 9:00.
    wall = datetime(local.year, local.month, local.day, local.hour, local.minute)
    return wall.replace(tzinfo=zone(timezone)).astimezone(UTC)


class ScheduleService:
    def __init__(self, database: Database):
        self.database = database

    def create(
        self,
        *,
        kind: ScheduleKind,
        message: str,
        run_at: datetime,
        recurrence: Recurrence = Recurrence.NONE,
        timezone: str = "UTC",
        work_item_id: str | None = None,
        chat_session_id: str | None = None,
        capabilities: list[str] | None = None,
        quiet: bool = False,
    ) -> ScheduleModel:
        normalized = " ".join(message.split())
        if not normalized or len(normalized) > 500:
            raise ValueError("Schedule message must be 1 to 500 characters")
        zone(timezone)
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=zone(timezone))
        with self.database.session() as session, session.begin():
            item_id = get_work_item(session, work_item_id).id if work_item_id else None
            schedule = ScheduleModel(
                id=str(uuid4()),
                kind=ScheduleKind(kind).value,
                message=normalized,
                work_item_id=item_id,
                chat_session_id=chat_session_id,
                next_run_at=run_at.astimezone(UTC),
                recurrence=Recurrence(recurrence).value,
                timezone=timezone,
                capabilities=capabilities,
                quiet=quiet,
                active=True,
            )
            session.add(schedule)
            session.flush()
            return schedule

    def list(
        self, *, work_item_id: str | None = None, active_only: bool = True
    ) -> list[ScheduleModel]:
        statement = select(ScheduleModel)
        if work_item_id is not None:
            statement = statement.where(ScheduleModel.work_item_id == work_item_id)
        if active_only:
            statement = statement.where(ScheduleModel.active.is_(True))
        with self.database.session() as session:
            return list(session.scalars(statement.order_by(ScheduleModel.next_run_at)))

    def get(self, schedule_id: str) -> ScheduleModel:
        with self.database.session() as session:
            schedule = session.get(ScheduleModel, schedule_id)
            if schedule is None:
                raise ScheduleNotFoundError(schedule_id)
            return schedule

    def cancel(self, schedule_id: str) -> ScheduleModel:
        with self.database.session() as session, session.begin():
            schedule = session.get(ScheduleModel, schedule_id)
            if schedule is None:
                raise ScheduleNotFoundError(schedule_id)
            schedule.active = False
            # A time that already fired but isn't delivered yet (a push being retried) is
            # dropped too: the user cancelled it.
            session.execute(
                delete(OutboxDeliveryModel).where(
                    OutboxDeliveryModel.kind == "schedule",
                    OutboxDeliveryModel.payload["schedule_id"].as_string() == schedule_id,
                )
            )
            return schedule


class Scheduler:
    """Atomically advances due schedules and enqueues their delivery."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utc_now,
    ):
        self.database = database
        self.clock = clock

    def run_due(self, *, limit: int = 20) -> int:
        now = self.clock()
        with self.database.session() as session, session.begin():
            due = list(
                session.scalars(
                    select(ScheduleModel)
                    .where(ScheduleModel.active.is_(True), ScheduleModel.next_run_at <= now)
                    .order_by(ScheduleModel.next_run_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for schedule in due:
                fired_at = aware(schedule.next_run_at)
                schedule.last_run_at = now
                following = next_occurrence(
                    schedule.next_run_at, Recurrence(schedule.recurrence), schedule.timezone
                )
                # Never fire a backlog: skip occurrences that are already past.
                while following is not None and following <= now:
                    following = next_occurrence(
                        following, Recurrence(schedule.recurrence), schedule.timezone
                    )
                if following is None:
                    schedule.active = False
                else:
                    schedule.next_run_at = following
                enqueue(
                    session,
                    kind="schedule",
                    payload=schedule_payload(schedule, fired_at),
                )
        return len(due)


#: How far ahead the phone schedules reminders and alarms, and how many it holds at most.
PHONE_HORIZON = timedelta(days=14)
PHONE_MAX_PER_SCHEDULE = 20
PHONE_MAX = 64


def phone_alarms(database: Database, *, now: datetime | None = None) -> list[dict]:
    """Every time a reminder or alarm rings in the next two weeks, for the Android app."""
    now = now or utc_now()
    end = now + PHONE_HORIZON
    with database.session() as session:
        schedules = list(
            session.scalars(
                select(ScheduleModel)
                .where(
                    ScheduleModel.active.is_(True),
                    ScheduleModel.kind.in_((ScheduleKind.REMINDER.value, ScheduleKind.ALARM.value)),
                    ScheduleModel.next_run_at <= end,
                )
                .order_by(ScheduleModel.next_run_at)
            )
        )
    rings: list[dict] = []
    for schedule in schedules:
        at: datetime | None = aware(schedule.next_run_at)
        count = 0
        while at is not None and at <= end and count < PHONE_MAX_PER_SCHEDULE:
            if at > now:
                rings.append(
                    {
                        "id": f"{schedule.id}@{int(at.timestamp())}",
                        "schedule_id": schedule.id,
                        "at": int(at.timestamp() * 1000),
                        "kind": schedule.kind,
                        "text": schedule.message,
                    }
                )
                count += 1
            at = next_occurrence(at, Recurrence(schedule.recurrence), schedule.timezone)
    rings.sort(key=lambda ring: ring["at"])
    return rings[:PHONE_MAX]


def mark_on_phone(database: Database, until: dict[str, int]) -> int:
    """The app rings these schedules itself up to the given times (ms); no push for those."""
    marked = 0
    with database.session() as session, session.begin():
        for schedule_id, at_ms in until.items():
            schedule = session.get(ScheduleModel, schedule_id)
            if schedule is None or schedule.kind == ScheduleKind.ROUTINE.value:
                continue
            at = datetime.fromtimestamp(at_ms / 1000, tz=UTC)
            if schedule.phone_until is None or aware(schedule.phone_until) < at:
                schedule.phone_until = at
                marked += 1
    return marked
