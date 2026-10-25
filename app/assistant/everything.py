"""Everything going on, across every chat: for "what's going on?" from anywhere.

The coding runs have their own overview (``overview.py``); this adds the rest of the
background work (research, routine runs), what finished lately, the routines' next runs,
and the conversations themselves. It only reads, and never Notes or private memories.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from sqlalchemy import select

from app.chat.routines import describe
from app.domain.enums import TaskStatus
from app.persistence.database import Database
from app.persistence.models import (
    ChatMessageModel,
    ChatSessionModel,
    ScheduleModel,
    TaskModel,
    utc_now,
)
from app.persistence.repository import TaskRepository
from app.schedules import ScheduleKind

FINISHED_WITHIN = timedelta(hours=24)
ACTIVE = (
    TaskStatus.CREATED,
    TaskStatus.PLANNING,
    TaskStatus.WAITING,
    TaskStatus.EXECUTING,
    TaskStatus.VALIDATING,
)
FINISHED = (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
_WORD = re.compile(r"[\w']+")


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=utc_now().tzinfo)


def _ago(moment: datetime, now: datetime) -> str:
    minutes = int((now - _aware(moment)).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // (24 * 60)} days ago"


def _clip(text: str, limit: int = 160) -> str:
    line = " ".join((text or "").split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _kind(task: TaskModel) -> str:
    if (task.source_context or {}).get("schedule_id"):
        return "routine"
    if "web_research" in (task.required_capabilities or []):
        return "research"
    return "task"


def background_work(database: Database, *, now: datetime | None = None) -> str:
    """Research and routine runs in progress or finished lately, with where they report."""
    now = now or utc_now()
    since = now - FINISHED_WITHIN
    with database.session() as session:
        tasks = [
            task
            for task in session.scalars(
                select(TaskModel)
                .where(TaskModel.updated_at >= now - timedelta(days=7))
                .order_by(TaskModel.updated_at.desc())
                .limit(200)
            )
            if not TaskRepository.is_reply(task) and not TaskRepository._is_coding_task(task)
        ]
        chat_ids = {task.chat_session_id for task in tasks if task.chat_session_id}
        titles = dict(
            session.execute(
                select(ChatSessionModel.id, ChatSessionModel.title).where(
                    ChatSessionModel.id.in_(chat_ids)
                )
            ).all()
        )
        results = {}
        for task in tasks:
            reply = session.scalars(
                select(ChatMessageModel.content)
                .where(ChatMessageModel.linked_task_id == task.id)
                .where(ChatMessageModel.role == "assistant")
                .order_by(ChatMessageModel.created_at.desc())
            ).first()
            results[task.id] = reply
    running = [t for t in tasks if TaskStatus(t.status) in ACTIVE]
    finished = [
        t for t in tasks if TaskStatus(t.status) in FINISHED and _aware(t.updated_at) >= since
    ]

    def where(task: TaskModel) -> str:
        title = titles.get(task.chat_session_id or "")
        return f" (in “{title}”)" if title else ""

    lines = ["IN PROGRESS"]
    lines += [
        f"- {_kind(t)}: “{_clip(t.current_goal, 100)}”{where(t)}, started {_ago(t.created_at, now)}"
        for t in running
    ] or ["- nothing besides coding runs"]
    lines.append("FINISHED IN THE LAST 24 HOURS")
    lines += [
        f"- {_kind(t)} {t.status}: “{_clip(t.current_goal, 80)}”{where(t)}, "
        f"{_ago(t.updated_at, now)}" + (f" — {_clip(results[t.id])}" if results.get(t.id) else "")
        for t in finished
    ] or ["- nothing"]
    return "\n".join(lines)


def routines(database: Database) -> str:
    with database.session() as session:
        found = list(
            session.scalars(
                select(ScheduleModel)
                .where(ScheduleModel.active.is_(True))
                .where(ScheduleModel.kind == ScheduleKind.ROUTINE.value)
            )
        )
    return "ROUTINES\n" + ("\n".join(f"- {describe(r)}" for r in found) or "- none")


def recent_chats(database: Database, *, limit: int = 12) -> str:
    """The latest conversations: title, when, and the assistant's last line in each."""
    now = utc_now()
    with database.session() as session:
        chats = list(
            session.scalars(
                select(ChatSessionModel)
                .where(ChatSessionModel.archived.is_(False))
                .order_by(ChatSessionModel.updated_at.desc())
                .limit(limit)
            )
        )
        lines = []
        for chat in chats:
            last = session.scalars(
                select(ChatMessageModel.content)
                .where(ChatMessageModel.chat_session_id == chat.id)
                .where(ChatMessageModel.role == "assistant")
                .order_by(ChatMessageModel.created_at.desc())
            ).first()
            label = "Assistant (this main conversation)" if chat.home else f"“{chat.title}”"
            lines.append(
                f"- {label}, {_ago(chat.updated_at, now)}"
                + (f": last reply “{_clip(last, 120)}”" if last else ": no reply yet")
            )
    return "\n".join(lines) or "No conversations yet."


def read_chat(database: Database, wanted: str, *, messages: int = 12) -> str | None:
    """The last messages of the conversation whose title best matches ``wanted``."""
    words = set(_WORD.findall(wanted.lower()))
    with database.session() as session:
        chats = list(
            session.scalars(
                select(ChatSessionModel).order_by(ChatSessionModel.updated_at.desc()).limit(200)
            )
        )
        scored = [(len(words & set(_WORD.findall(chat.title.lower()))), chat) for chat in chats]
        best = max((score for score, _ in scored), default=0)
        if not best:
            return None
        chat = next(chat for score, chat in scored if score == best)
        rows = list(
            session.scalars(
                select(ChatMessageModel)
                .where(ChatMessageModel.chat_session_id == chat.id)
                .where(ChatMessageModel.role.in_(("user", "assistant")))
                .order_by(ChatMessageModel.created_at.desc())
                .limit(messages)
            )
        )[::-1]
    said = "\n".join(f"{row.role}: {_clip(row.content, 400)}" for row in rows)
    return f"“{chat.title}”, last {len(rows)} messages:\n{said or '(empty)'}"
