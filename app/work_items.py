"""Work items, chat focus, and the timeline that merges their history with their runs.

The module-level functions take an open session so the task service can link runs inside
its own transactions; :class:`WorkItemService` wraps them for the API.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import EventType, TaskStatus
from app.domain.task_context import (
    _artifact,
    _artifact_label,
    _clean,
    build_task_context,
    render_task_context,
)
from app.domain.work_items import (
    DEFAULT_SPACES,
    MAX_CONTEXT_ACTIVITY,
    MAX_CONTEXT_CHECKLIST,
    MAX_CONTEXT_ITEMS,
    SLUG_PATTERN,
    Brief,
    ChecklistEntry,
    WorkItemEventType,
    WorkItemKind,
    WorkItemLink,
    WorkItemStatus,
    slugify,
)
from app.persistence.database import Database
from app.persistence.models import (
    ChatMessageModel,
    ChatSessionModel,
    ChatWorkItemModel,
    CodingRunModel,
    SpaceModel,
    TaskEventModel,
    TaskModel,
    WorkflowRunModel,
    WorkItemEventModel,
    WorkItemModel,
    utc_now,
)
from app.persistence.repository import ChatMessageRepository, TaskRepository

logger = logging.getLogger(__name__)

MAX_TITLE_CHARACTERS = 160
#: Statuses new work may join; done or archived items get a fresh item instead.
RESUMABLE_STATUSES = frozenset(
    {WorkItemStatus.OPEN.value, WorkItemStatus.ACTIVE.value, WorkItemStatus.BLOCKED.value}
)

#: Run events that belong on a work item's timeline. Everything else (polling, raw tool
#: output, plans, leases) stays in the task's own event stream.
TIMELINE_TASK_EVENTS = frozenset(
    {
        EventType.TASK_CREATED.value,
        EventType.ASSISTANT_REPLY.value,
        EventType.ARTIFACT_CREATED.value,
        EventType.APPROVAL_REQUESTED.value,
        EventType.APPROVAL_GRANTED.value,
        EventType.APPROVAL_REJECTED.value,
        EventType.VALIDATION_FAILED.value,
        EventType.TASK_COMPLETED.value,
        EventType.TASK_FAILED.value,
        EventType.TASK_CANCELLED.value,
    }
)


class WorkItemNotFoundError(LookupError):
    pass


class SpaceNotFoundError(LookupError):
    pass


class WorkItemConflictError(ValueError):
    pass


def item_title(text: str) -> str:
    normalized = " ".join(text.split()) or "Untitled"
    if len(normalized) <= 80:
        return normalized
    return normalized[:77].rstrip() + "…"


def get_space(session: Session, slug: str) -> SpaceModel:
    space = session.scalar(select(SpaceModel).where(SpaceModel.slug == slug))
    if space is not None:
        return space
    defaults = {default_slug: (name, kind) for default_slug, name, kind in DEFAULT_SPACES}
    if slug not in defaults:
        raise SpaceNotFoundError(slug)
    name, kind = defaults[slug]
    space = SpaceModel(id=str(uuid4()), slug=slug, name=name, kind=kind)
    session.add(space)
    session.flush()
    return space


def get_work_item(
    session: Session, reference: str, *, for_update: bool = False
) -> WorkItemModel:
    """Resolve a work item by id or slug."""
    statement = select(WorkItemModel).where(
        (WorkItemModel.id == reference) | (WorkItemModel.slug == reference)
    )
    if for_update:
        statement = statement.with_for_update()
    item = session.scalar(statement)
    if item is None:
        raise WorkItemNotFoundError(reference)
    return item


def append_event(
    session: Session,
    item: WorkItemModel,
    event_type: WorkItemEventType,
    payload: dict[str, Any] | None = None,
    *,
    chat_session_id: str | None = None,
    task_id: str | None = None,
) -> WorkItemEventModel:
    last_sequence = session.scalar(
        select(func.max(WorkItemEventModel.sequence)).where(
            WorkItemEventModel.work_item_id == item.id
        )
    )
    event = WorkItemEventModel(
        work_item_id=item.id,
        sequence=(last_sequence or 0) + 1,
        event_type=event_type.value,
        payload=payload or {},
        chat_session_id=chat_session_id,
        task_id=task_id,
        created_at=utc_now(),
    )
    session.add(event)
    return event


def _unique_slug(session: Session, title: str) -> str:
    base = slugify(title)
    taken = set(
        session.scalars(
            select(WorkItemModel.slug).where(
                (WorkItemModel.slug == base) | WorkItemModel.slug.like(f"{base}-%")
            )
        )
    )
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"


def create_work_item(
    session: Session,
    *,
    title: str,
    kind: WorkItemKind = WorkItemKind.GOAL,
    space_slug: str = "general",
    brief: Brief | None = None,
    links: Sequence[WorkItemLink] = (),
    status: WorkItemStatus = WorkItemStatus.OPEN,
    chat_session_id: str | None = None,
) -> WorkItemModel:
    normalized_title = " ".join(title.split())
    if not normalized_title:
        raise ValueError("Work item title cannot be empty")
    if len(normalized_title) > MAX_TITLE_CHARACTERS:
        raise ValueError(f"Work item title cannot exceed {MAX_TITLE_CHARACTERS} characters")
    space = get_space(session, space_slug)
    item = WorkItemModel(
        id=str(uuid4()),
        space_id=space.id,
        slug=_unique_slug(session, normalized_title),
        kind=WorkItemKind(kind).value,
        title=normalized_title,
        status=WorkItemStatus(status).value,
        brief=(brief or Brief(goal=normalized_title)).model_dump(mode="json"),
        checklist=[],
        links=[link.model_dump(mode="json") for link in links],
        version=1,
    )
    session.add(item)
    session.flush()
    append_event(
        session,
        item,
        WorkItemEventType.WORK_ITEM_CREATED,
        {"title": item.title, "kind": item.kind, "space": space.slug},
        chat_session_id=chat_session_id,
    )
    if chat_session_id is not None:
        focus_chat(session, chat_session_id=chat_session_id, item=item)
    return item


def focus_chat(session: Session, *, chat_session_id: str, item: WorkItemModel) -> bool:
    """Attach a chat to a work item; returns False when it already was."""
    if session.get(ChatSessionModel, chat_session_id) is None:
        raise LookupError(chat_session_id)
    if session.get(ChatWorkItemModel, (chat_session_id, item.id)) is not None:
        return False
    session.add(ChatWorkItemModel(chat_session_id=chat_session_id, work_item_id=item.id))
    append_event(
        session,
        item,
        WorkItemEventType.CHAT_FOCUSED,
        {},
        chat_session_id=chat_session_id,
    )
    return True


def focused_items(session: Session, chat_session_id: str) -> list[WorkItemModel]:
    return list(
        session.scalars(
            select(WorkItemModel)
            .join(ChatWorkItemModel, ChatWorkItemModel.work_item_id == WorkItemModel.id)
            .where(ChatWorkItemModel.chat_session_id == chat_session_id)
            .order_by(ChatWorkItemModel.focused_at)
        )
    )


def link_task(session: Session, *, task: TaskModel, item: WorkItemModel) -> None:
    if task.work_item_id == item.id:
        return
    task.work_item_id = item.id
    item.updated_at = utc_now()
    if item.status == WorkItemStatus.OPEN.value:
        # Work started on it.
        append_event(
            session,
            item,
            WorkItemEventType.STATUS_CHANGED,
            {"from": item.status, "to": WorkItemStatus.ACTIVE.value, "source": "run"},
            task_id=task.id,
        )
        item.status = WorkItemStatus.ACTIVE.value
        item.version += 1
    append_event(
        session,
        item,
        WorkItemEventType.RUN_LINKED,
        {"request": _clean(task.original_request, limit=200) or ""},
        chat_session_id=task.chat_session_id,
        task_id=task.id,
    )


def resolve_for_run(
    session: Session,
    *,
    chat_session_id: str | None,
    title: str,
    space_slug: str,
    links: Sequence[WorkItemLink] = (),
) -> WorkItemModel:
    """The work item new work belongs to: the chat's only unfinished focus, or a new one."""
    if chat_session_id is not None:
        focused = [
            item
            for item in focused_items(session, chat_session_id)
            if item.status in RESUMABLE_STATUSES
        ]
        if len(focused) == 1:
            return focused[0]
    return create_work_item(
        session,
        title=item_title(title),
        space_slug=space_slug,
        links=links,
        chat_session_id=chat_session_id,
    )


def _work_item_summary(event: WorkItemEventModel) -> str:
    payload = event.payload or {}
    kind = event.event_type
    if kind == WorkItemEventType.WORK_ITEM_CREATED.value:
        return f"Created “{payload.get('title', '')}”"
    if kind == WorkItemEventType.STATUS_CHANGED.value:
        return f"Status: {payload.get('from')} → {payload.get('to')}"
    if kind == WorkItemEventType.TITLE_CHANGED.value:
        return f"Renamed to “{payload.get('to', '')}”"
    if kind == WorkItemEventType.SLUG_CHANGED.value:
        return f"Tag changed to #{payload.get('to', '')}"
    if kind == WorkItemEventType.BRIEF_UPDATED.value:
        fields = ", ".join(payload.get("fields", [])) or "brief"
        return f"Brief updated ({fields})"
    if kind == WorkItemEventType.BRIEF_UPDATE_FAILED.value:
        return "Brief update failed"
    if kind == WorkItemEventType.LINKS_CHANGED.value:
        return "Links updated"
    if kind == WorkItemEventType.CHECKLIST_CHANGED.value:
        return f"Checklist: {payload.get('action', 'changed')} “{payload.get('text', '')}”"
    if kind == WorkItemEventType.RUN_LINKED.value:
        return f"Run linked: {payload.get('request', '')}"
    if kind == WorkItemEventType.CHAT_FOCUSED.value:
        return "A chat started working on this"
    if kind == WorkItemEventType.CHAT_UNFOCUSED.value:
        return "A chat stopped working on this"
    return kind.replace("_", " ").capitalize()


def _task_summary(event: TaskEventModel, task: TaskModel) -> str:
    payload = event.payload or {}
    kind = event.event_type
    if kind == EventType.TASK_CREATED.value:
        return f"Run started: {_clean(task.original_request, limit=200) or ''}"
    if kind == EventType.ASSISTANT_REPLY.value:
        return _clean(payload.get("text"), limit=300) or "Assistant replied"
    if kind == EventType.ARTIFACT_CREATED.value:
        artifact = _artifact(payload)
        url = artifact.get("url")
        return _artifact_label(artifact) + (f" — {url}" if url else "")
    if kind == EventType.APPROVAL_REQUESTED.value:
        return "Waiting for your approval"
    if kind == EventType.APPROVAL_GRANTED.value:
        return "Approved"
    if kind == EventType.APPROVAL_REJECTED.value:
        return "Rejected"
    if kind == EventType.VALIDATION_FAILED.value:
        return "Validation failed"
    if kind == EventType.TASK_COMPLETED.value:
        return "Run completed"
    if kind == EventType.TASK_FAILED.value:
        return "Run failed"
    return "Run cancelled"


def timeline(
    session: Session,
    *,
    work_item_id: str | None = None,
    limit: int = 100,
    before: datetime | None = None,
) -> list[dict[str, Any]]:
    """Newest-first history of work items and their linked runs, whitelisted for display."""
    item_events = select(WorkItemEventModel)
    task_rows = (
        select(TaskEventModel, TaskModel)
        .join(TaskModel, TaskModel.id == TaskEventModel.task_id)
        .where(TaskModel.work_item_id.is_not(None))
        .where(TaskEventModel.event_type.in_(TIMELINE_TASK_EVENTS))
    )
    if work_item_id is not None:
        item_events = item_events.where(WorkItemEventModel.work_item_id == work_item_id)
        task_rows = task_rows.where(TaskModel.work_item_id == work_item_id)
    if before is not None:
        item_events = item_events.where(WorkItemEventModel.created_at < before)
        task_rows = task_rows.where(TaskEventModel.created_at < before)
    entries: list[dict[str, Any]] = []
    for event in session.scalars(
        item_events.order_by(WorkItemEventModel.created_at.desc()).limit(limit)
    ):
        entries.append(
            {
                "id": f"work_item:{event.id}",
                "source": "work_item",
                "event_type": event.event_type,
                "at": event.created_at,
                "work_item_id": event.work_item_id,
                "task_id": event.task_id,
                "chat_session_id": event.chat_session_id,
                "summary": _work_item_summary(event),
            }
        )
    for event, task in session.execute(
        task_rows.order_by(TaskEventModel.created_at.desc()).limit(limit)
    ):
        entries.append(
            {
                "id": f"task:{event.id}",
                "source": "task",
                "event_type": event.event_type,
                "at": event.created_at,
                "work_item_id": task.work_item_id,
                "task_id": task.id,
                "chat_session_id": task.chat_session_id,
                "summary": _task_summary(event, task),
            }
        )
    entries.sort(key=lambda entry: _aware(entry["at"]), reverse=True)
    return entries[:limit]


def work_item_context(session: Session, task: TaskModel) -> list[dict[str, Any]]:
    """The work items a run is about: its own item plus its chat's focus, at most three."""
    candidates: list[WorkItemModel] = []
    if task.work_item_id:
        candidates.append(get_work_item(session, task.work_item_id))
    if task.chat_session_id:
        candidates.extend(focused_items(session, task.chat_session_id))
    seen: set[str] = set()
    context: list[dict[str, Any]] = []
    for item in candidates:
        if item.id in seen or item.status == WorkItemStatus.ARCHIVED.value:
            continue
        seen.add(item.id)
        context.append(
            {
                "slug": item.slug,
                "title": item.title,
                "kind": item.kind,
                "status": item.status,
                "brief": item.brief or {},
                "open_checklist": [
                    entry["text"] for entry in item.checklist or [] if not entry.get("done")
                ][:MAX_CONTEXT_CHECKLIST],
                "recent_activity": [
                    entry["summary"]
                    for entry in timeline(
                        session, work_item_id=item.id, limit=MAX_CONTEXT_ACTIVITY
                    )
                ],
            }
        )
        if len(context) == MAX_CONTEXT_ITEMS:
            break
    return context


MAX_MATERIAL_MESSAGES = 40
TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}
)


def _newer(value: datetime | None, since: datetime | None) -> bool:
    return value is not None and (since is None or _aware(value) > _aware(since))


def brief_material(
    session: Session,
    item: WorkItemModel,
    *,
    now: datetime,
    idle_seconds: int,
) -> str | None:
    """New conversation and run results since the brief was last written, or None.

    Conversation counts only once its chats have been quiet for ``idle_seconds``, so a
    brief is not rewritten mid-discussion; a finished run counts immediately.
    """
    since = item.brief_synced_at
    chat_ids = list(
        session.scalars(
            select(ChatWorkItemModel.chat_session_id).where(
                ChatWorkItemModel.work_item_id == item.id
            )
        )
    )
    messages: list[ChatMessageModel] = []
    if chat_ids:
        messages = [
            message
            for message in session.scalars(
                select(ChatMessageModel)
                .where(ChatMessageModel.chat_session_id.in_(chat_ids))
                .where(ChatMessageModel.role.in_(("user", "assistant")))
                .order_by(ChatMessageModel.created_at.desc())
                .limit(200)
            )
            if _newer(message.created_at, since)
        ]
    if messages and (now - _aware(messages[0].created_at)).total_seconds() < idle_seconds:
        messages = []
    runs = [
        task
        for task in session.scalars(
            select(TaskModel)
            .where(TaskModel.work_item_id == item.id)
            .where(TaskModel.status.in_(TERMINAL_TASK_STATUSES))
            .order_by(TaskModel.updated_at.desc())
            .limit(20)
        )
        if _newer(task.updated_at, since)
    ]
    if not messages and not runs:
        return None
    parts: list[str] = []
    if messages:
        parts.append("CONVERSATION (oldest first)")
        for message in reversed(messages[:MAX_MATERIAL_MESSAGES]):
            parts.append(f"{message.role}: {_clean(message.content, limit=800) or ''}")
    for task in reversed(runs):
        context = build_task_context(task, TaskRepository.list_events(session, task.id))
        parts.append("RUN RESULT\n" + render_task_context(context))
    return "\n".join(parts)


def record_writeback(
    session: Session,
    item_id: str,
    *,
    expected_version: int,
    synced_at: datetime,
    brief: Brief | None = None,
    failure: str | None = None,
) -> bool:
    """Apply a generated brief and advance the watermark.

    Returns False without changes when the item was edited meanwhile, so a person's
    edit is never overwritten by a brief written from older state; it is retried.
    """
    item = get_work_item(session, item_id, for_update=True)
    if item.version != expected_version:
        return False
    if brief is not None:
        new_brief = brief.model_dump(mode="json")
        fields = sorted(key for key in new_brief if new_brief[key] != (item.brief or {}).get(key))
        if fields:
            append_event(
                session,
                item,
                WorkItemEventType.BRIEF_UPDATED,
                {"fields": fields, "source": "writeback"},
            )
            item.brief = new_brief
            item.version += 1
            item.updated_at = utc_now()
    elif failure is not None:
        # Recorded without the raw model output; the watermark still advances so a
        # persistently failing item cannot loop.
        append_event(
            session,
            item,
            WorkItemEventType.BRIEF_UPDATE_FAILED,
            {"reason": _clean(failure, limit=200) or "unknown"},
        )
    item.brief_synced_at = synced_at
    return True


def _aware(value: datetime) -> datetime:
    # SQLite returns naive datetimes; PostgreSQL returns aware ones.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class WorkItemService:
    def __init__(self, database: Database):
        self.database = database

    def list_spaces(self) -> list[SpaceModel]:
        with self.database.session() as session, session.begin():
            for slug, _name, _kind in DEFAULT_SPACES:
                get_space(session, slug)
            return list(session.scalars(select(SpaceModel).order_by(SpaceModel.slug)))

    def create(
        self,
        *,
        title: str,
        kind: WorkItemKind = WorkItemKind.GOAL,
        space_slug: str = "general",
        brief: Brief | None = None,
        links: Sequence[WorkItemLink] = (),
        chat_session_id: str | None = None,
    ) -> WorkItemModel:
        with self.database.session() as session, session.begin():
            return create_work_item(
                session,
                title=title,
                kind=kind,
                space_slug=space_slug,
                brief=brief,
                links=links,
                chat_session_id=chat_session_id,
            )

    def get(self, reference: str) -> WorkItemModel:
        with self.database.session() as session:
            return get_work_item(session, reference)

    def list(
        self,
        *,
        space_slug: str | None = None,
        status: WorkItemStatus | None = None,
        kind: WorkItemKind | None = None,
        limit: int = 100,
    ) -> list[WorkItemModel]:
        statement = select(WorkItemModel)
        with self.database.session() as session:
            if space_slug is not None:
                space = session.scalar(select(SpaceModel).where(SpaceModel.slug == space_slug))
                if space is None:
                    return []
                statement = statement.where(WorkItemModel.space_id == space.id)
            if status is not None:
                statement = statement.where(WorkItemModel.status == WorkItemStatus(status).value)
            else:
                statement = statement.where(
                    WorkItemModel.status != WorkItemStatus.ARCHIVED.value
                )
            if kind is not None:
                statement = statement.where(WorkItemModel.kind == WorkItemKind(kind).value)
            return list(
                session.scalars(statement.order_by(WorkItemModel.updated_at.desc()).limit(limit))
            )

    def space_slugs(self, items: Iterable[WorkItemModel]) -> dict[str, str]:
        space_ids = {item.space_id for item in items}
        if not space_ids:
            return {}
        with self.database.session() as session:
            return {
                space.id: space.slug
                for space in session.scalars(select(SpaceModel).where(SpaceModel.id.in_(space_ids)))
            }

    def update(
        self,
        reference: str,
        *,
        expected_version: int,
        title: str | None = None,
        slug: str | None = None,
        status: WorkItemStatus | None = None,
        brief: Brief | None = None,
        links: Sequence[WorkItemLink] | None = None,
        source: str = "user",
    ) -> WorkItemModel:
        with self.database.session() as session, session.begin():
            item = get_work_item(session, reference, for_update=True)
            if item.version != expected_version:
                raise WorkItemConflictError(
                    f"Work item changed (version {item.version}, expected {expected_version})"
                )
            changed = False
            if title is not None:
                normalized = " ".join(title.split())
                if not normalized or len(normalized) > MAX_TITLE_CHARACTERS:
                    raise ValueError("Work item title must be 1 to 160 characters")
                if normalized != item.title:
                    append_event(
                        session,
                        item,
                        WorkItemEventType.TITLE_CHANGED,
                        {"from": item.title, "to": normalized, "source": source},
                    )
                    item.title = normalized
                    changed = True
            if slug is not None and slug != item.slug:
                if not SLUG_PATTERN.fullmatch(slug):
                    raise ValueError("slug must be lowercase letters, digits, and hyphens")
                taken = session.scalar(select(WorkItemModel.id).where(WorkItemModel.slug == slug))
                if taken is not None:
                    raise ValueError(f"slug #{slug} is already used by another work item")
                append_event(
                    session,
                    item,
                    WorkItemEventType.SLUG_CHANGED,
                    {"from": item.slug, "to": slug, "source": source},
                )
                item.slug = slug
                changed = True
            if status is not None:
                status = WorkItemStatus(status)
            if status is not None and status.value != item.status:
                append_event(
                    session,
                    item,
                    WorkItemEventType.STATUS_CHANGED,
                    {"from": item.status, "to": status.value, "source": source},
                )
                item.status = status.value
                changed = True
            if brief is not None:
                new_brief = brief.model_dump(mode="json")
                fields = sorted(
                    key for key in new_brief if new_brief[key] != (item.brief or {}).get(key)
                )
                if fields:
                    append_event(
                        session,
                        item,
                        WorkItemEventType.BRIEF_UPDATED,
                        {"fields": fields, "source": source},
                    )
                    item.brief = new_brief
                    changed = True
            if links is not None:
                new_links = [link.model_dump(mode="json") for link in links]
                if new_links != list(item.links or []):
                    append_event(
                        session, item, WorkItemEventType.LINKS_CHANGED, {"source": source}
                    )
                    item.links = new_links
                    changed = True
            if changed:
                item.version += 1
                item.updated_at = utc_now()
            return item

    def add_checklist_entry(self, reference: str, text: str) -> WorkItemModel:
        normalized = " ".join(text.split())
        entry = ChecklistEntry(id=str(uuid4()), text=normalized, added_at=utc_now())
        with self.database.session() as session, session.begin():
            item = get_work_item(session, reference, for_update=True)
            item.checklist = [*(item.checklist or []), entry.model_dump(mode="json")]
            self._checklist_changed(session, item, "added", entry.text)
            return item

    def set_checklist_entry(self, reference: str, entry_id: str, *, done: bool) -> WorkItemModel:
        with self.database.session() as session, session.begin():
            item = get_work_item(session, reference, for_update=True)
            entries = [dict(entry) for entry in item.checklist or []]
            entry = next((entry for entry in entries if entry["id"] == entry_id), None)
            if entry is None:
                raise WorkItemNotFoundError(f"{reference}/checklist/{entry_id}")
            if entry["done"] != done:
                entry["done"] = done
                item.checklist = entries
                self._checklist_changed(
                    session, item, "checked" if done else "unchecked", entry["text"]
                )
            return item

    def remove_checklist_entry(self, reference: str, entry_id: str) -> WorkItemModel:
        with self.database.session() as session, session.begin():
            item = get_work_item(session, reference, for_update=True)
            entries = list(item.checklist or [])
            removed = next((entry for entry in entries if entry["id"] == entry_id), None)
            if removed is None:
                raise WorkItemNotFoundError(f"{reference}/checklist/{entry_id}")
            item.checklist = [entry for entry in entries if entry["id"] != entry_id]
            self._checklist_changed(session, item, "removed", removed["text"])
            return item

    @staticmethod
    def _checklist_changed(
        session: Session, item: WorkItemModel, action: str, text: str
    ) -> None:
        append_event(
            session,
            item,
            WorkItemEventType.CHECKLIST_CHANGED,
            {"action": action, "text": _clean(text, limit=120) or ""},
        )
        item.version += 1
        item.updated_at = utc_now()

    def focus(self, chat_session_id: str, reference: str) -> WorkItemModel:
        with self.database.session() as session, session.begin():
            item = get_work_item(session, reference)
            focus_chat(session, chat_session_id=chat_session_id, item=item)
            return item

    def unfocus(self, chat_session_id: str, reference: str) -> None:
        with self.database.session() as session, session.begin():
            item = get_work_item(session, reference)
            link = session.get(ChatWorkItemModel, (chat_session_id, item.id))
            if link is None:
                return
            session.delete(link)
            append_event(
                session,
                item,
                WorkItemEventType.CHAT_UNFOCUSED,
                {},
                chat_session_id=chat_session_id,
            )

    def focused(self, chat_session_id: str) -> list[WorkItemModel]:
        with self.database.session() as session:
            if session.get(ChatSessionModel, chat_session_id) is None:
                raise LookupError(chat_session_id)
            return focused_items(session, chat_session_id)

    def runs(self, reference: str, *, limit: int = 100) -> list[TaskModel]:
        with self.database.session() as session:
            item = get_work_item(session, reference)
            return list(
                session.scalars(
                    select(TaskModel)
                    .where(TaskModel.work_item_id == item.id)
                    .order_by(TaskModel.created_at.desc())
                    .limit(limit)
                )
            )

    def timeline(
        self,
        reference: str | None = None,
        *,
        limit: int = 100,
        before: datetime | None = None,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            work_item_id = get_work_item(session, reference).id if reference else None
            return timeline(session, work_item_id=work_item_id, limit=limit, before=before)

    def discuss(self, reference: str, *, about: str | None = None) -> ChatSessionModel:
        """Open a new chat focused on the item, optionally about one timeline entry."""
        with self.database.session() as session, session.begin():
            item = get_work_item(session, reference)
            chat = ChatSessionModel(id=str(uuid4()), title=item.title[:160], archived=False)
            session.add(chat)
            session.flush()
            focus_chat(session, chat_session_id=chat.id, item=item)
            note = f"Discussing work item “{item.title}” (#{item.slug})."
            if about_text := _clean(about, limit=300):
                note += f" About: {about_text}"
            ChatMessageRepository.append(
                session, chat_session_id=chat.id, role="system", content=note
            )
            return chat

    def resolve_for_run(
        self,
        *,
        chat_session_id: str | None,
        title: str,
        space_slug: str,
        links: Sequence[WorkItemLink] = (),
    ) -> WorkItemModel:
        with self.database.session() as session, session.begin():
            return resolve_for_run(
                session,
                chat_session_id=chat_session_id,
                title=title,
                space_slug=space_slug,
                links=links,
            )

    def backfill_from_tasks(self) -> int:
        """Create one coding work item per existing task lineage that did coding work.

        Idempotent: only lineages whose root has no work item yet are considered, and
        each lineage is committed in its own transaction.
        """
        with self.database.session() as session:
            roots = list(
                session.scalars(
                    select(TaskModel.id)
                    .where(TaskModel.parent_task_id.is_(None))
                    .where(TaskModel.work_item_id.is_(None))
                    .order_by(TaskModel.created_at)
                )
            )
        created = 0
        for root_id in roots:
            with self.database.session() as session, session.begin():
                if self._backfill_lineage(session, root_id):
                    created += 1
        return created

    @staticmethod
    def _backfill_lineage(session: Session, root_id: str) -> bool:
        root = TaskRepository.get(session, root_id, for_update=True)
        if root is None or root.work_item_id is not None:
            return False
        lineage: list[TaskModel] = []
        pending = [root]
        while pending:
            task = pending.pop()
            lineage.append(task)
            pending.extend(
                session.scalars(select(TaskModel).where(TaskModel.parent_task_id == task.id))
            )
        ids = [task.id for task in lineage]
        coding_runs = list(
            session.scalars(select(CodingRunModel).where(CodingRunModel.task_id.in_(ids)))
        )
        has_workflow = (
            session.scalar(
                select(func.count())
                .select_from(WorkflowRunModel)
                .where(WorkflowRunModel.task_id.in_(ids))
            )
            > 0
        )
        if not coding_runs and not has_workflow:
            return False
        lineage.sort(key=lambda task: _aware(task.created_at))
        latest = lineage[-1]
        context = build_task_context(latest, TaskRepository.list_events(session, latest.id))
        links: list[WorkItemLink] = []
        repository_id = (root.source_context or {}).get("repository_id")
        if repository_id:
            links.append(
                WorkItemLink(kind="repository", label=str(repository_id), ref=str(repository_id))
            )
        for run in coding_runs:
            if run.pull_request_url and run.pull_request_number:
                links.append(
                    WorkItemLink(
                        kind="pull_request",
                        label=f"Pull request #{run.pull_request_number}",
                        url=run.pull_request_url,
                    )
                )
        done = latest.status == TaskStatus.COMPLETED.value
        item = create_work_item(
            session,
            title=item_title(root.current_goal or root.original_request),
            space_slug="coding",
            brief=Brief(
                goal=_clean(root.current_goal, limit=600) or "",
                status_summary=(
                    _clean(context.get("final_answer") or context.get("summary"), limit=600)
                    or ""
                ),
            ),
            links=links,
            status=WorkItemStatus.DONE if done else WorkItemStatus.OPEN,
            chat_session_id=root.chat_session_id,
        )
        for task in lineage:
            link_task(session, task=task, item=item)
        return True


def main(argv: Sequence[str] | None = None) -> None:
    from app.config import Settings

    parser = argparse.ArgumentParser(prog="python -m app.work_items")
    parser.add_argument("command", choices=["backfill"])
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    database = Database(Settings.from_env().database_url)
    try:
        created = WorkItemService(database).backfill_from_tasks()
    finally:
        database.dispose()
    logger.info("Created %d work items from existing coding tasks", created)


if __name__ == "__main__":
    main()
