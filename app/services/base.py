"""The database handle and the lookups every part of the service uses."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.chat.blocks import validate_blocks
from app.domain.enums import EventType, ExecutionStatus
from app.persistence.database import Database
from app.persistence.models import ExecutionModel, TaskModel
from app.persistence.repository import ChatMessageRepository, ChatSessionRepository, TaskRepository
from app.services.common import ExecutionNotFoundError, TaskNotFoundError


class ServiceBase:
    """The database handle and the lookups every part of the service uses."""

    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _record_assistant_reply(
        session,
        task: TaskModel,
        payload: dict[str, Any],
    ) -> None:
        TaskRepository.append_event(session, task, EventType.ASSISTANT_REPLY, payload)
        text = str(payload.get("text", "")).strip()
        if not task.chat_session_id or not text:
            return
        try:
            blocks = validate_blocks(payload.get("blocks") or [])
        except ValueError:
            blocks = []
        ChatMessageRepository.append(
            session,
            chat_session_id=task.chat_session_id,
            role="assistant",
            content=text,
            linked_task_id=task.id,
            blocks=blocks,
        )
        chat_session = ChatSessionRepository.get(session, task.chat_session_id)
        if chat_session is not None:
            chat_session.updated_at = datetime.now(UTC)

    @staticmethod
    def _require_task(session, task_id: str, *, for_update: bool = False) -> TaskModel:
        task = TaskRepository.get(session, task_id, for_update=for_update)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    @staticmethod
    def _require_execution(session, execution_id: str) -> ExecutionModel:
        execution = session.scalar(
            select(ExecutionModel).where(ExecutionModel.id == execution_id).with_for_update()
        )
        if execution is None:
            raise ExecutionNotFoundError(execution_id)
        return execution

    @staticmethod
    def _mark_execution_cancelled(execution: ExecutionModel) -> None:
        execution.status = ExecutionStatus.CANCELLED.value
        execution.completed_at = datetime.now(UTC)
