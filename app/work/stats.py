"""How the assistant is doing: counts, reply times and failures, from what is already
stored (tasks, their executions and the messages that started them). No extra tables."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text

from app.domain.enums import CODING_CAPABILITIES, TaskStatus
from app.persistence.database import Database
from app.persistence.models import (
    ChatMessageModel,
    ChatSessionModel,
    ExecutionModel,
    MemoryModel,
    NoteModel,
    TaskModel,
    WorkItemModel,
)

#: In the order they are shown.
KINDS = ("chat reply", "groceries", "research", "supervisor", "coding", "other")
_FINISHED = {TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}


@dataclass
class _Kind:
    tasks: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    unfinished: int = 0
    reply_seconds: list[float] = None  # type: ignore[assignment]
    wait_seconds: list[float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.reply_seconds, self.wait_seconds = [], []


def kind_of(capabilities: list[str]) -> str:
    """What sort of work a task is, from the capabilities it required."""
    if any(capability in CODING_CAPABILITIES for capability in capabilities):
        return "coding"
    for capability, kind in (
        ("supervision", "supervisor"),
        ("groceries", "groceries"),
        ("web_research", "research"),
    ):
        if capability in capabilities:
            return kind
    return "chat reply" if not capabilities or "conversation" in capabilities else "other"


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _aware(moment: datetime | None) -> datetime | None:
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def compute_stats(
    database: Database, *, days: int = 7, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    kinds = {name: _Kind() for name in KINDS}
    with database.session() as session:
        tasks = session.execute(
            select(
                TaskModel.id,
                TaskModel.status,
                TaskModel.created_at,
                TaskModel.required_capabilities,
                ChatMessageModel.created_at.label("asked_at"),
            )
            .join(
                ChatMessageModel, ChatMessageModel.id == TaskModel.origin_message_id, isouter=True
            )
            .where(TaskModel.created_at >= since)
        ).all()
        last_run: dict[str, Any] = {}
        for execution in session.execute(
            select(ExecutionModel.task_id, ExecutionModel.started_at, ExecutionModel.completed_at)
            .join(TaskModel, TaskModel.id == ExecutionModel.task_id)
            .where(TaskModel.created_at >= since)
            .order_by(ExecutionModel.started_at)
        ):
            last_run[execution.task_id] = execution  # the latest run of each task wins
        counts = {
            "chats": session.scalar(select(func.count()).select_from(ChatSessionModel)),
            "messages": session.scalar(
                select(func.count())
                .select_from(ChatMessageModel)
                .where(ChatMessageModel.created_at >= since)
            ),
            "notes": session.scalar(select(func.count()).select_from(NoteModel)),
            "memories": session.scalar(
                select(func.count()).select_from(MemoryModel).where(MemoryModel.active.is_(True))
            ),
            "work_items": session.scalar(select(func.count()).select_from(WorkItemModel)),
        }
        database_bytes = None
        if database.engine.dialect.name == "postgresql":
            database_bytes = session.scalar(text("select pg_database_size(current_database())"))

    for task in tasks:
        stats = kinds[kind_of(list(task.required_capabilities or []))]
        stats.tasks += 1
        if task.status == TaskStatus.COMPLETED.value:
            stats.completed += 1
        elif task.status == TaskStatus.FAILED.value:
            stats.failed += 1
        elif task.status == TaskStatus.CANCELLED.value:
            stats.cancelled += 1
        else:
            stats.unfinished += 1
        run = last_run.get(task.id)
        created = _aware(task.created_at)
        if run is not None:
            stats.wait_seconds.append(max(0.0, (_aware(run.started_at) - created).total_seconds()))
            finished = _aware(run.completed_at)
            if finished is not None and task.status in _FINISHED:
                # From the message being sent (or the task being made) to the end.
                begun = _aware(task.asked_at) or created
                stats.reply_seconds.append(max(0.0, (finished - begun).total_seconds()))

    usage = shutil.disk_usage("/")
    return {
        "days": days,
        "since": since.isoformat(),
        "kinds": [
            {
                "kind": name,
                "tasks": stats.tasks,
                "completed": stats.completed,
                "failed": stats.failed,
                "cancelled": stats.cancelled,
                "unfinished": stats.unfinished,
                "queue_wait_median_s": _rounded(percentile(stats.wait_seconds, 0.5)),
                "reply_median_s": _rounded(percentile(stats.reply_seconds, 0.5)),
                "reply_p90_s": _rounded(percentile(stats.reply_seconds, 0.9)),
            }
            for name, stats in kinds.items()
            if stats.tasks
        ],
        "counts": counts,
        "system": {
            "disk_total_gb": round(usage.total / 1e9, 1),
            "disk_free_gb": round(usage.free / 1e9, 1),
            "disk_used_percent": round(100 * usage.used / usage.total),
            "database_mb": None if database_bytes is None else round(database_bytes / 1e6, 1),
        },
    }
