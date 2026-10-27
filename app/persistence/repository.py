from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.domain.enums import CODING_CAPABILITIES, EventType, TaskStatus
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
    def list(
        session: Session, *, limit: int = 100, archived: bool = False
    ) -> list[ChatSessionModel]:
        return list(
            session.scalars(
                select(ChatSessionModel)
                .where(ChatSessionModel.archived.is_(archived))
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
        before: tuple[datetime, str] | None = None,
    ) -> list[ChatMessageModel]:
        messages, _ = ChatMessageRepository.list_page(
            session, chat_session_id, limit=limit, before=before
        )
        return messages

    @staticmethod
    def list_page(
        session: Session,
        chat_session_id: str,
        *,
        limit: int = 500,
        before: tuple[datetime, str] | None = None,
    ) -> tuple[list[ChatMessageModel], bool]:
        statement = select(ChatMessageModel).where(
            ChatMessageModel.chat_session_id == chat_session_id
        )
        if before is not None:
            created_at, message_id = before
            statement = statement.where(
                or_(
                    ChatMessageModel.created_at < created_at,
                    (ChatMessageModel.created_at == created_at)
                    & (ChatMessageModel.id < message_id),
                )
            )
        newest_first = list(
            session.scalars(
                statement.order_by(
                    ChatMessageModel.created_at.desc(), ChatMessageModel.id.desc()
                ).limit(limit + 1)
            )
        )
        has_older = len(newest_first) > limit
        return list(reversed(newest_first[:limit])), has_older

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
        message_id: str | None = None,
        linked_task_id: str | None = None,
        blocks: list[dict[str, Any]] | None = None,
        emotion: str | None = None,
    ) -> ChatMessageModel:
        message = ChatMessageModel(
            id=message_id or str(uuid4()),
            chat_session_id=chat_session_id,
            role=role,
            content=content,
            linked_task_id=linked_task_id,
            blocks=blocks or None,
            emotion=emotion,
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
        lane: str = "all",
    ) -> TaskModel | None:
        """``lane``: "replies" takes only chat replies, "background" everything else."""
        after: tuple[datetime, str] | None = None
        while True:
            candidates = TaskRepository._created_batch(session, after=after)
            if not candidates:
                return None
            for candidate in candidates:
                if not TaskRepository._eligible(
                    candidate,
                    supports_coding=supports_coding,
                    coding_only=coding_only,
                    lane=lane,
                ):
                    continue
                task = session.scalar(
                    select(TaskModel)
                    .where(
                        TaskModel.id == candidate.id,
                        TaskModel.status == TaskStatus.CREATED.value,
                        TaskModel.cancel_requested.is_(False),
                    )
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True)
                )
                if task is None or not TaskRepository._eligible(
                    task,
                    supports_coding=supports_coding,
                    coding_only=coding_only,
                    lane=lane,
                ):
                    continue
                task.status = TaskStatus.PLANNING.value
                task.claimed_by = worker_id
                task.claim_id = str(uuid4())
                task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
                TaskRepository.append_event(
                    session,
                    task,
                    EventType.TASK_PLANNING_STARTED,
                    {"worker_id": worker_id},
                )
                return task
            after = (candidates[-1].created_at, candidates[-1].id)

    @staticmethod
    def _created_batch(
        session: Session,
        *,
        after: tuple[datetime, str] | None,
        limit: int = 100,
    ) -> list[TaskModel]:
        statement = select(TaskModel).where(
            TaskModel.status == TaskStatus.CREATED.value,
            TaskModel.cancel_requested.is_(False),
        )
        if after is not None:
            created_at, task_id = after
            statement = statement.where(
                or_(
                    TaskModel.created_at > created_at,
                    (TaskModel.created_at == created_at) & (TaskModel.id > task_id),
                )
            )
        return list(
            session.scalars(statement.order_by(TaskModel.created_at, TaskModel.id).limit(limit))
        )

    @staticmethod
    def _eligible(
        task: TaskModel,
        *,
        supports_coding: bool,
        coding_only: bool,
        lane: str,
    ) -> bool:
        return (
            not TaskRepository._is_malformed_repository_task(task)
            and (lane == "all" or (lane == "replies") == TaskRepository.is_reply(task))
            and (
                TaskRepository._is_coding_task(task)
                if coding_only
                else supports_coding or not TaskRepository._is_coding_task(task)
            )
        )

    @staticmethod
    def is_reply(task: TaskModel) -> bool:
        """A chat reply someone is waiting for: from a chat, needing no specialist.

        Research, shopping, supervision, coding and routine runs are background work.
        """
        context = task.source_context or {}
        return bool(
            task.chat_session_id
            and not task.required_capabilities
            and not context.get("schedule_id")
            and not context.get("repository_id")
            and not context.get("report")
        )

    @staticmethod
    def _is_coding_task(task: TaskModel) -> bool:
        capabilities = set(task.required_capabilities or [])
        return bool(
            capabilities.intersection(CODING_CAPABILITIES)
            or (task.source_context or {}).get("repository_id")
        )

    @staticmethod
    def _is_malformed_repository_task(task: TaskModel) -> bool:
        capabilities = set(task.required_capabilities or [])
        return bool(
            (task.source_context or {}).get("repository_id")
            and not capabilities.intersection(CODING_CAPABILITIES)
        )

    @staticmethod
    def malformed_queued(session: Session, *, limit: int = 20) -> list[TaskModel]:
        """Queued tasks naming a repository without a coding capability; no worker runs them."""
        malformed: list[TaskModel] = []
        after: tuple[datetime, str] | None = None
        while len(malformed) < limit:
            candidates = TaskRepository._created_batch(session, after=after)
            if not candidates:
                break
            for candidate in candidates:
                if not TaskRepository._is_malformed_repository_task(candidate):
                    continue
                task = session.scalar(
                    select(TaskModel)
                    .where(
                        TaskModel.id == candidate.id,
                        TaskModel.status == TaskStatus.CREATED.value,
                    )
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True)
                )
                if task is not None and TaskRepository._is_malformed_repository_task(task):
                    malformed.append(task)
                    if len(malformed) == limit:
                        break
            after = (candidates[-1].created_at, candidates[-1].id)
        return malformed

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
            task.claim_id = None
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
