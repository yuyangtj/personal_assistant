"""The database handle and the lookups every part of the service uses."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy import text as sql_text

from app.chat.blocks import MAX_BLOCKS, validate_blocks
from app.domain.enums import EventType, ExecutionStatus
from app.persistence.database import Database
from app.persistence.models import ExecutionModel, TaskModel
from app.persistence.repository import ChatMessageRepository, ChatSessionRepository, TaskRepository
from app.services.common import ExecutionNotFoundError, TaskNotFoundError


class ServiceBase:
    """The database handle and the lookups every part of the service uses."""

    def __init__(self, database: Database, *, coding_restricted: bool = False):
        self.database = database
        self.coding_restricted = coding_restricted

    def _record_assistant_reply(
        self,
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
        if payload.get("action"):
            # The proposed phone action, as a card the phone confirms (see PhoneActionBlock).
            card = validate_blocks([{"type": "phone_action", "action": payload["action"]}])
            blocks = [*card, *blocks][:MAX_BLOCKS]
        emotion = str(payload["emotion"]) if payload.get("emotion") else None
        if self.coding_restricted:
            session.execute(
                sql_text(
                    "SELECT post_coding_assistant_reply("
                    ":task_id, :message_id, :content, CAST(:blocks AS JSONB), :emotion)"
                ),
                {
                    "task_id": task.id,
                    "message_id": str(uuid4()),
                    "content": text,
                    "blocks": json.dumps(blocks),
                    "emotion": emotion,
                },
            )
            return
        ChatMessageRepository.append(
            session,
            chat_session_id=task.chat_session_id,
            role="assistant",
            content=text,
            linked_task_id=task.id,
            blocks=blocks,
            emotion=emotion,
        )
        chat_session = ChatSessionRepository.get(session, task.chat_session_id)
        if chat_session is not None:
            chat_session.updated_at = datetime.now(UTC)

    @staticmethod
    def _has_active_claim(
        task: TaskModel,
        claim_id: str,
        *,
        worker_id: str | None = None,
    ) -> bool:
        lease = task.lease_expires_at
        if lease is not None and lease.tzinfo is None:
            lease = lease.replace(tzinfo=UTC)
        return bool(
            claim_id
            and task.claim_id == claim_id
            and (worker_id is None or task.claimed_by == worker_id)
            and not task.cancel_requested
            and lease is not None
            and lease >= datetime.now(UTC)
        )

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
