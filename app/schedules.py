"""Reminders and routines: stored schedules the worker runs when they are due.

A reminder posts into its chat and pushes to the phone. A routine starts a run (a chat
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

from sqlalchemy import select

from app.notify import Notifier, NullNotifier
from app.persistence.database import Database
from app.persistence.models import ScheduleModel, utc_now
from app.persistence.repository import ChatMessageRepository
from app.work.items import get_work_item

logger = logging.getLogger(__name__)


class ScheduleKind(StrEnum):
    REMINDER = "reminder"
    ROUTINE = "routine"


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


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def next_occurrence(previous: datetime, recurrence: Recurrence, timezone: str) -> datetime | None:
    """The next run after ``previous`` (UTC), keeping the same local wall-clock time."""
    if recurrence == Recurrence.NONE:
        return None
    local = _aware(previous).astimezone(zone(timezone))
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

    def cancel(self, schedule_id: str) -> ScheduleModel:
        with self.database.session() as session, session.begin():
            schedule = session.get(ScheduleModel, schedule_id)
            if schedule is None:
                raise ScheduleNotFoundError(schedule_id)
            schedule.active = False
            return schedule


class Scheduler:
    """Runs due schedules; called from the worker's background thread."""

    def __init__(
        self,
        database: Database,
        *,
        start_run: Callable[[ScheduleModel], str | None],
        notifier: Notifier | None = None,
        clock: Callable[[], datetime] = utc_now,
    ):
        self.database = database
        self.start_run = start_run
        self.notifier = notifier or NullNotifier()
        self.clock = clock

    def run_due(self, *, limit: int = 20) -> int:
        """Claim due schedules (advancing or closing them) in one short transaction, then
        fire each outside it, so a slow push or run never holds database locks."""
        now = self.clock()
        claimed: list[ScheduleModel] = []
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
                claimed.append(schedule)
        for schedule in claimed:
            try:
                self._fire(schedule)
            except Exception:
                logger.exception("Schedule %s failed to fire", schedule.id)
        return len(claimed)

    def _fire(self, schedule: ScheduleModel) -> None:
        tag = ""
        if schedule.work_item_id:
            with self.database.session() as session:
                tag = f" (#{get_work_item(session, schedule.work_item_id).slug})"
        if schedule.kind == ScheduleKind.REMINDER.value:
            text = f"Reminder{tag}: {schedule.message}"
            if schedule.chat_session_id:
                with self.database.session() as session, session.begin():
                    ChatMessageRepository.append(
                        session,
                        chat_session_id=schedule.chat_session_id,
                        role="assistant",
                        content=text,
                    )
            self.notifier.send("Reminder", text, priority=4)
            return
        task_id = self.start_run(schedule)
        self.notifier.send(
            "Routine started",
            f"{schedule.message}{tag}",
            path=f"/?task={task_id}" if task_id else "",
        )
