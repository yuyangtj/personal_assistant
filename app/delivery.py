from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select

from app.notify import Notifier, NullNotifier
from app.persistence.database import Database
from app.persistence.models import ChatMessageModel, OutboxDeliveryModel, ScheduleModel, utc_now
from app.persistence.repository import ChatMessageRepository
from app.services import TaskService
from app.work.items import get_work_item

logger = logging.getLogger(__name__)


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
        now = self.clock()
        with self.database.session() as session:
            deliveries = list(
                session.scalars(
                    select(OutboxDeliveryModel)
                    .where(OutboxDeliveryModel.available_at <= now)
                    .order_by(
                        OutboxDeliveryModel.available_at,
                        OutboxDeliveryModel.created_at,
                        OutboxDeliveryModel.id,
                    )
                    .limit(limit)
                )
            )
        delivered = 0
        for delivery in deliveries:
            if self._deliver(delivery):
                delivered += 1
        return delivered

    def _deliver(self, delivery: OutboxDeliveryModel) -> bool:
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

    def _deliver_schedule(self, delivery: OutboxDeliveryModel) -> None:
        payload = delivery.payload
        schedule = ScheduleModel(
            id=payload["schedule_id"],
            kind=payload["kind"],
            message=payload["message"],
            work_item_id=payload.get("work_item_id"),
            chat_session_id=payload.get("chat_session_id"),
            capabilities=payload.get("capabilities"),
            quiet=payload.get("quiet", False),
            timezone=payload["timezone"],
            next_run_at=datetime.fromisoformat(payload["fired_at"]),
        )
        if schedule.kind == "routine":
            self.start_run(schedule, delivery.id)
            return

        alarm = schedule.kind == "alarm"
        tag = ""
        if schedule.work_item_id:
            with self.database.session() as session:
                tag = f" (#{get_work_item(session, schedule.work_item_id).slug})"
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
        fired_at = datetime.fromisoformat(payload["fired_at"])
        phone_until = payload.get("phone_until")
        if phone_until and fired_at <= datetime.fromisoformat(phone_until):
            return
        if isinstance(self.notifier, NullNotifier):
            return
        sent = self.notifier.send(
            "Alarm" if alarm else "Reminder", text, priority=5 if alarm else 4
        )
        if not sent:
            raise RuntimeError(f"Push failed for schedule {schedule.id}")

    def _defer(self, delivery_id: str, error: Exception) -> None:
        logger.warning("Delivery %s failed: %s", delivery_id, error)
        with self.database.session() as session, session.begin():
            delivery = session.get(OutboxDeliveryModel, delivery_id)
            if delivery is None:
                return
            delivery.attempts += 1
            delivery.last_error = str(error)[:2000]
            delay = min(300, 2 ** min(delivery.attempts, 8))
            delivery.available_at = self.clock() + timedelta(seconds=delay)
