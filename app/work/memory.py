from __future__ import annotations

import re
from uuid import uuid4

from sqlalchemy import select

from app.persistence.database import Database
from app.persistence.models import ChatSessionModel, MemoryModel, TaskModel, utc_now

KINDS = {"fact", "preference", "project"}
#: Memories with this tag are also given to the grocery agent.
FOOD_TAG = "food"
#: How much the chat model is given: every memory up to this size, newest first.
MODEL_CHARACTER_BUDGET = 6_000

_WORD = re.compile(r"\w{3,}")
_FOOD = re.compile(
    r"\b(vegetarian|vegan|pescatarian|allerg\w*|intoleran\w*|gluten|lactose|laktos\w*|"
    r"dairy|diet\w*|food|eat\w*|meals?|cook\w*|groceries|grocery|breakfast|lunch|dinner|"
    r"snacks?|coffee|tea|milk|cheese|meat|chicken|beef|pork|fish|seafood|nuts?|peanuts?|"
    r"eggs?|bread|sugar|fruit|vegetables?|organic|halal|kosher|"
    r"mat|äter|frukost|middag|kaffe|mjölk|ost|kött|fisk|nötter|ägg|bröd|socker|frukt|"
    r"grönsaker|ekologisk\w*|willys|lidl)\b",
    re.IGNORECASE,
)


_FILLER = {
    "the",
    "and",
    "that",
    "this",
    "about",
    "with",
    "for",
    "you",
    "your",
    "are",
    "was",
    "have",
    "has",
    "what",
    "which",
    "memory",
    "remember",
    "forget",
    "please",
    "och",
    "att",
    "det",
    "som",
    "för",
    "med",
    "jag",
    "min",
    "mitt",
    "mina",
}


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower())) - _FILLER


def _tags(content: str, tags: list[str] | None) -> list[str]:
    """Normalized tags, with the food tag added when the memory is plainly about food."""
    normalized = [tag.strip().lower().lstrip("#") for tag in tags or [] if tag.strip()]
    if _FOOD.search(content):
        normalized.append(FOOD_TAG)
    return list(dict.fromkeys(normalized))


class MemoryService:
    """Explicit, inspectable long-term memory; nothing is saved implicitly.

    Private memories stay on the server: they are listed in the console but never given
    to a chat model, the grocery agent, or anything else.
    """

    def __init__(self, database: Database):
        self.database = database

    def create(
        self,
        *,
        kind: str,
        content: str,
        tags: list[str] | None = None,
        private: bool = False,
        chat_session_id: str | None = None,
        task_id: str | None = None,
    ) -> MemoryModel:
        normalized = content.strip()
        if not normalized:
            raise ValueError("Memory content cannot be empty")
        if kind not in KINDS:
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
                tags=_tags(normalized, tags),
                private=private,
                source_chat_session_id=chat_session_id,
                source_task_id=task_id,
                active=True,
            )
            session.add(memory)
            session.flush()
            return memory

    def update(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        kind: str | None = None,
        tags: list[str] | None = None,
        private: bool | None = None,
    ) -> MemoryModel:
        """Edit a memory; tags given replace the old ones exactly (no food guess)."""
        with self.database.session() as session, session.begin():
            memory = session.get(MemoryModel, memory_id)
            if memory is None or not memory.active:
                raise LookupError(memory_id)
            if content is not None:
                if not content.strip():
                    raise ValueError("Memory content cannot be empty")
                memory.content = content.strip()
            if kind is not None:
                if kind not in KINDS:
                    raise ValueError("Memory kind must be fact, preference, or project")
                memory.kind = kind
            if tags is not None:
                memory.tags = list(
                    dict.fromkeys(t.strip().lower().lstrip("#") for t in tags if t.strip())
                )
            if private is not None:
                memory.private = private
            memory.updated_at = utc_now()
            session.flush()
            return memory

    def list(self, *, active_only: bool = True, limit: int = 500) -> list[MemoryModel]:
        with self.database.session() as session:
            query = select(MemoryModel)
            if active_only:
                query = query.where(MemoryModel.active.is_(True))
            return list(session.scalars(query.order_by(MemoryModel.created_at.desc()).limit(limit)))

    def for_model(self, *, budget: int = MODEL_CHARACTER_BUDGET) -> list[str]:
        """What the chat model may see: every shared memory, newest first, within budget.

        The model decides what is relevant, so "my wife" finds "my partner Anna" and
        Swedish works; private memories are never included.
        """
        chosen, used = [], 0
        for memory in self.list():
            if memory.private:
                continue
            if used + len(memory.content) > budget:
                break
            chosen.append(memory.content)
            used += len(memory.content)
        return chosen

    def for_groceries(self) -> list[str]:
        """Food preferences for the grocery agent; private memories are never included."""
        return [
            memory.content
            for memory in self.list()
            if not memory.private and FOOD_TAG in (memory.tags or [])
        ][:20]

    def find(self, text: str) -> list[MemoryModel]:
        """Shared memories matching a description ("my coffee order"), best first.

        Private ones are left out: what is found is repeated in the chat.
        """
        wanted = _words(text)
        scored = []
        for memory in self.list():
            if memory.private:
                continue
            overlap = len(wanted & _words(memory.content + " " + " ".join(memory.tags)))
            if overlap:
                scored.append((overlap, memory))
        if not scored:
            return []
        best = max(score for score, _ in scored)
        return [memory for score, memory in sorted(scored, key=lambda s: -s[0]) if score == best]

    def archive(self, memory_id: str) -> MemoryModel:
        with self.database.session() as session, session.begin():
            memory = session.get(MemoryModel, memory_id)
            if memory is None:
                raise LookupError(memory_id)
            memory.active = False
            memory.updated_at = utc_now()
            session.flush()
            return memory
