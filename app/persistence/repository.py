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
    ExecutionModel,
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
        supports_coding: bool = True,
        coding_only: bool = False,
    ) -> TaskModel | None:
        candidates = list(
            session.scalars(
                select(TaskModel)
                .where(
                    TaskModel.status == TaskStatus.CREATED.value,
                    TaskModel.cancel_requested.is_(False),
                )
                .order_by(TaskModel.created_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        task = next(
            (
                candidate
                for candidate in candidates
                if (
                    TaskRepository._is_coding_task(candidate)
                    if coding_only
                    else supports_coding or not TaskRepository._is_coding_task(candidate)
                )
            ),
            None,
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

    @staticmethod
    def _is_coding_task(task: TaskModel) -> bool:
        capabilities = set(task.required_capabilities or [])
        return bool(
            capabilities.intersection({"coding", "pull_request_creation", "coding-pull-request"})
            or (task.source_context or {}).get("repository_id")
        )

    @staticmethod
    def recover_expired(session: Session, *, now: datetime) -> list[str]:
        tasks = list(
            session.scalars(
                select(TaskModel)
                .where(
                    TaskModel.status.in_(
                        [
                            TaskStatus.PLANNING.value,
                            TaskStatus.EXECUTING.value,
                            TaskStatus.VALIDATING.value,
                        ]
                    ),
                    TaskModel.lease_expires_at.is_not(None),
                    TaskModel.lease_expires_at < now,
                    TaskModel.cancel_requested.is_(False),
                )
                .with_for_update(skip_locked=True)
            )
        )
        for task in tasks:
            previous = task.status
            for execution in session.scalars(
                select(ExecutionModel).where(
                    ExecutionModel.task_id == task.id,
                    ExecutionModel.status == "running",
                )
            ):
                execution.status = "failed"
                execution.error = "Worker lease expired; execution will be reconciled"
                execution.completed_at = now
            task.status = TaskStatus.CREATED.value
            task.claimed_by = None
            task.lease_expires_at = None
            TaskRepository.append_event(
                session,
                task,
                EventType.TASK_RECOVERED,
                {"previous_status": previous, "reason": "worker_lease_expired"},
            )
            TaskRepository.append_event(
                session,
                task,
                EventType.EXECUTION_RETRIED,
                {"reason": "worker_lease_expired", "reconcile_checkpoint": True},
            )
        return [task.id for task in tasks]
