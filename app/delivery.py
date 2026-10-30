from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.notify import Notifier, NullNotifier
from app.persistence.database import Database
from app.persistence.models import ChatMessageModel, OutboxDeliveryModel, ScheduleModel, utc_now
from app.persistence.repository import ChatMessageRepository
from app.schedules import aware
from app.services import TaskService
from app.work.items import get_work_item

logger = logging.getLogger(__name__)

#: A claimed delivery is hidden from other workers this long; if its worker dies
#: mid-delivery, another one picks it up afterwards.
CLAIM_LEASE = timedelta(minutes=5)
#: Failed deliveries are retried with backoff (2 s doubling to 5 min) this many times,
#: about an hour in all, then dropped with an error in the log.
MAX_ATTEMPTS = 16
#: A reminder or alarm push still failing this long after it fired is no longer useful:
#: it is skipped, not sent hours late.
PUSH_STALE_AFTER = timedelta(minutes=30)


@dataclass(frozen=True)
class _Claimed:
    id: str
    kind: str
    payload: dict[str, Any]
    queued_at: datetime


class DeliveryService:
    def __init__(
        self,
        database: Database,
        tasks: TaskService,
        *,
        start_run: Callable[[ScheduleModel, str], str | None],
        notifier: Notifier | None = None,
        clock: Callable[[], datetime] = utc_now,
    ):
        self.database = database
        self.tasks = tasks
        self.start_run = start_run
        self.notifier = notifier or NullNotifier()
        self.clock = clock

    def run_due(self, *, limit: int = 20) -> int:
        """Claim due deliveries (so no other worker sends them too), then deliver each."""
        now = self.clock()
        with self.database.session() as session, session.begin():
            rows = list(
                session.scalars(
                    select(OutboxDeliveryModel)
                    .where(OutboxDeliveryModel.available_at <= now)
                    .order_by(
                        OutboxDeliveryModel.available_at,
                        OutboxDeliveryModel.created_at,
                        OutboxDeliveryModel.id,
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            claimed = []
            for row in rows:
                row.available_at = now + CLAIM_LEASE
                claimed.append(_Claimed(row.id, row.kind, dict(row.payload), aware(row.created_at)))
        return sum(1 for delivery in claimed if self._deliver(delivery))

    def _deliver(self, delivery: _Claimed) -> bool:
        try:
            if delivery.kind == "task":
                self.tasks.create_task(
                    **delivery.payload,
                    external_source="reporter",
                    external_key=delivery.id,
                )
            elif delivery.kind == "schedule":
                self._deliver_schedule(delivery)
            else:
                raise ValueError(f"Unknown delivery kind: {delivery.kind}")
        except Exception as error:
            self._defer(delivery.id, error)
            return False
        self._complete(delivery.id)
        return True

    def _complete(self, delivery_id: str) -> None:
        with self.database.session() as session, session.begin():
            delivery = session.get(OutboxDeliveryModel, delivery_id)
            if delivery is not None:
                session.delete(delivery)

    def _deliver_schedule(self, delivery: _Claimed) -> None:
        with self.database.session() as session:
            schedule = session.get(ScheduleModel, delivery.payload["schedule_id"])
            if schedule is None:
                return  # deleted since it fired: nothing to deliver
            tag = ""
            if schedule.work_item_id:
                tag = f" (#{get_work_item(session, schedule.work_item_id).slug})"
        if schedule.kind == "routine":
            self.start_run(schedule, delivery.id)
            return

        alarm = schedule.kind == "alarm"
        text = f"{'Alarm' if alarm else 'Reminder'}{tag}: {schedule.message}"
        if schedule.chat_session_id and not alarm:
            with self.database.session() as session, session.begin():
                if session.get(ChatMessageModel, delivery.id) is None:
                    ChatMessageRepository.append(
                        session,
                        message_id=delivery.id,
                        chat_session_id=schedule.chat_session_id,
                        role="assistant",
                        content=text,
                    )
        fired_at = aware(datetime.fromisoformat(delivery.payload["fired_at"]))
        if schedule.phone_until is not None and fired_at <= aware(schedule.phone_until):
            return  # the phone app rings it itself
        if isinstance(self.notifier, NullNotifier):
            return
        if self.clock() - delivery.queued_at > PUSH_STALE_AFTER:
            logger.warning("Skipped the late push for schedule %s", schedule.id)
            return
        sent = self.notifier.send(
            "Alarm" if alarm else "Reminder", text, priority=5 if alarm else 4
        )
        if not sent:
            raise RuntimeError(f"Push failed for schedule {schedule.id}")

    def _defer(self, delivery_id: str, error: Exception) -> None:
        with self.database.session() as session, session.begin():
            delivery = session.get(OutboxDeliveryModel, delivery_id)
            if delivery is None:
                return
            delivery.attempts += 1
            if delivery.attempts >= MAX_ATTEMPTS:
                logger.error(
                    "Gave up on delivery %s (%s) after %d attempts: %s",
                    delivery_id,
                    delivery.kind,
                    delivery.attempts,
                    error,
                )
                session.delete(delivery)
                return
            logger.warning("Delivery %s failed: %s", delivery_id, error)
            delivery.last_error = str(error)[:2000]
            delay = min(300, 2 ** min(delivery.attempts, 8))
            delivery.available_at = self.clock() + timedelta(seconds=delay)
