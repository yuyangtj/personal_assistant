from __future__ import annotations

import re
from uuid import uuid4

from sqlalchemy import select

from app.persistence.database import Database
from app.persistence.models import ChatSessionModel, MemoryModel, TaskModel, utc_now


class MemoryService:
    """Explicit, inspectable long-term memory; nothing is saved implicitly."""

    def __init__(self, database: Database):
        self.database = database

    def create(
        self,
        *,
        kind: str,
        content: str,
        tags: list[str] | None = None,
        chat_session_id: str | None = None,
        task_id: str | None = None,
    ) -> MemoryModel:
        normalized = content.strip()
        if not normalized:
            raise ValueError("Memory content cannot be empty")
        if kind not in {"fact", "preference", "project"}:
            raise ValueError("Memory kind must be fact, preference, or project")
        with self.database.session() as session, session.begin():
            if chat_session_id and session.get(ChatSessionModel, chat_session_id) is None:
                raise ValueError("Source chat session does not exist")
            if task_id and session.get(TaskModel, task_id) is None:
                raise ValueError("Source task does not exist")
            memory = MemoryModel(
                id=str(uuid4()),
                kind=kind,
                content=normalized,
                tags=list(dict.fromkeys(tags or [])),
                source_chat_session_id=chat_session_id,
                source_task_id=task_id,
                active=True,
            )
            session.add(memory)
            session.flush()
            return memory

    def list(self, *, active_only: bool = True, limit: int = 100) -> list[MemoryModel]:
        with self.database.session() as session:
            query = select(MemoryModel)
            if active_only:
                query = query.where(MemoryModel.active.is_(True))
            return list(session.scalars(query.order_by(MemoryModel.created_at.desc()).limit(limit)))

    def relevant(self, text: str, *, limit: int = 8) -> list[MemoryModel]:
        terms = set(re.findall(r"[a-z0-9]{3,}", text.lower()))
        candidates = self.list(limit=500)

        def score(memory: MemoryModel) -> int:
            searchable = (memory.content + " " + " ".join(memory.tags)).lower()
            return len(terms & set(re.findall(r"[a-z0-9]{3,}", searchable)))

        matched = [memory for memory in candidates if score(memory) > 0]
        return sorted(matched, key=score, reverse=True)[:limit]

    def archive(self, memory_id: str) -> MemoryModel:
        with self.database.session() as session, session.begin():
            memory = session.get(MemoryModel, memory_id)
            if memory is None:
                raise LookupError(memory_id)
            memory.active = False
            memory.updated_at = utc_now()
            session.flush()
            return memory
