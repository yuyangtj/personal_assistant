from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.domain.enums import EventType, TaskStatus
from app.persistence.models import (
    ChatMessageModel,
    ChatSessionModel,
    TaskEventModel,
    TaskModel,
    utc_now,
)


class ChatSessionRepository:
    @staticmethod
    def get(session: Session, chat_session_id: str) -> ChatSessionModel | None:
        return session.get(ChatSessionModel, chat_session_id)

    @staticmethod
    def list(session: Session, *, limit: int = 100) -> list[ChatSessionModel]:
        return list(
            session.scalars(
                select(ChatSessionModel)
                .where(ChatSessionModel.archived.is_(False))
                .order_by(ChatSessionModel.updated_at.desc())
                .limit(limit)
            )
        )


class ChatMessageRepository:
    @staticmethod
    def list(
        session: Session,
        chat_session_id: str,
        *,
        limit: int = 500,
    ) -> list[ChatMessageModel]:
        return list(
            session.scalars(
                select(ChatMessageModel)
                .where(ChatMessageModel.chat_session_id == chat_session_id)
                .order_by(ChatMessageModel.created_at, ChatMessageModel.id)
                .limit(limit)
            )
        )

    @staticmethod
    def latest(session: Session, chat_session_id: str) -> ChatMessageModel | None:
        return session.scalar(
            select(ChatMessageModel)
            .where(ChatMessageModel.chat_session_id == chat_session_id)
            .order_by(ChatMessageModel.created_at.desc(), ChatMessageModel.id.desc())
            .limit(1)
        )

    @staticmethod
    def append(
        session: Session,
        *,
        chat_session_id: str,
        role: str,
        content: str,
        linked_task_id: str | None = None,
    ) -> ChatMessageModel:
        message = ChatMessageModel(
            id=str(uuid4()),
            chat_session_id=chat_session_id,
            role=role,
            content=content,
            linked_task_id=linked_task_id,
            created_at=utc_now(),
        )
        session.add(message)
        return message


class TaskRepository:
    @staticmethod
    def get(session: Session, task_id: str, *, for_update: bool = False) -> TaskModel | None:
        statement: Select[tuple[TaskModel]] = select(TaskModel).where(TaskModel.id == task_id)
        if for_update:
            statement = statement.with_for_update()
        return session.scalar(statement)

    @staticmethod
    def find_external(
        session: Session, *, external_source: str, external_key: str
    ) -> TaskModel | None:
        return session.scalar(
            select(TaskModel).where(
                TaskModel.external_source == external_source,
                TaskModel.external_key == external_key,
            )
        )

    @staticmethod
    def list(
        session: Session,
        *,
        status: TaskStatus | None = None,
        chat_session_id: str | None = None,
        limit: int = 100,
    ) -> list[TaskModel]:
        statement = select(TaskModel)
        if status is not None:
            statement = statement.where(TaskModel.status == status.value)
        if chat_session_id is not None:
            statement = statement.where(TaskModel.chat_session_id == chat_session_id)
        statement = statement.order_by(TaskModel.created_at.desc()).limit(limit)
        return list(session.scalars(statement))

    @staticmethod
    def list_events(session: Session, task_id: str) -> list[TaskEventModel]:
        return list(
            session.scalars(
                select(TaskEventModel)
                .where(TaskEventModel.task_id == task_id)
                .order_by(TaskEventModel.sequence)
            )
        )

    @staticmethod
    def append_event(
        session: Session,
        task: TaskModel,
        event_type: EventType,
        payload: dict[str, Any] | None = None,
    ) -> TaskEventModel:
        last_sequence = session.scalar(
            select(func.max(TaskEventModel.sequence)).where(TaskEventModel.task_id == task.id)
        )
        event = TaskEventModel(
            task_id=task.id,
            sequence=(last_sequence or 0) + 1,
            event_type=event_type.value,
            payload=payload or {},
        )
        session.add(event)
        task.version += 1
        task.updated_at = datetime.now(UTC)
        return event

    @staticmethod
    def claim_next(
        session: Session,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> TaskModel | None:
        task = session.scalar(
            select(TaskModel)
            .where(
                TaskModel.status == TaskStatus.CREATED.value,
                TaskModel.cancel_requested.is_(False),
            )
            .order_by(TaskModel.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if task is None:
            return None

        task.status = TaskStatus.PLANNING.value
        task.claimed_by = worker_id
        task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        TaskRepository.append_event(
            session,
            task,
            EventType.TASK_PLANNING_STARTED,
            {"worker_id": worker_id},
        )
        return task
