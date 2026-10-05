"""What running agents report, and what the user tells them while they run."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.domain.enums import EventType, TaskStatus
from app.domain.transitions import TERMINAL_STATUSES
from app.persistence.models import TaskEventModel
from app.persistence.repository import TaskRepository
from app.services.base import ServiceBase

logger = logging.getLogger(__name__)


class ProgressOperations(ServiceBase):
    """What running agents report, and what the user tells them while they run."""

    def record_progress(self, task_id: str, text: str, kind: str) -> None:
        """Note what a running agent is doing; best effort, never raises."""
        text = text.strip()[:2000]
        if not text:
            return
        try:
            with self.database.session() as session, session.begin():
                task = self._require_task(session, task_id, for_update=True)
                if TaskStatus(task.status) in TERMINAL_STATUSES:
                    return
                TaskRepository.append_event(
                    session, task, EventType.TASK_PROGRESS, {"text": text, "kind": kind}
                )
        except Exception:
            logger.exception("Could not record progress for task %s", task_id)

    def task_progress(self, task_ids: list[str]) -> dict[str, dict[str, str]]:
        """The latest progress line and plan of each task that has reported any."""
        if not task_ids:
            return {}
        progress: dict[str, dict[str, str]] = {}
        with self.database.session() as session:
            events = session.scalars(
                select(TaskEventModel)
                .where(
                    TaskEventModel.task_id.in_(task_ids),
                    TaskEventModel.event_type == EventType.TASK_PROGRESS.value,
                )
                .order_by(TaskEventModel.task_id, TaskEventModel.sequence.desc())
            )
            for event in events:
                entry = progress.setdefault(event.task_id, {})
                key = "plan" if event.payload.get("kind") == "plan" else "progress"
                entry.setdefault(key, str(event.payload.get("text", "")))
        return progress

    def user_messages_after(self, task_id: str, sequence: int) -> list[tuple[int, str]]:
        """Messages the user sent to a running task after the given event sequence."""
        with self.database.session() as session:
            return [
                (event.sequence, str(event.payload.get("message", "")))
                for event in session.scalars(
                    select(TaskEventModel)
                    .where(TaskEventModel.task_id == task_id)
                    .where(TaskEventModel.event_type == EventType.USER_MESSAGE_RECEIVED.value)
                    .where(TaskEventModel.sequence > sequence)
                    .order_by(TaskEventModel.sequence)
                )
            ]

    def add_user_message(self, task_id: str, message: str) -> None:
        normalized_message = message.strip()
        if not normalized_message:
            raise ValueError("Message cannot be empty")
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            TaskRepository.append_event(
                session,
                task,
                EventType.USER_MESSAGE_RECEIVED,
                {"message": normalized_message},
            )
