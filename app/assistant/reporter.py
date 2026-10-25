"""Workers report to the Assistant: finished background work, gathered into one message.

Research, routine runs and coding runs still post their full result in the chat that
started them. The reporter notices what finished, waits a moment so things finishing
together arrive as one message, and starts a report in the Assistant conversation: the
chat agent tells the user, in a few lines, what came back and what needs them. The worker
then adds links to the details and decides on a push (``needs_push``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.domain.enums import EventType, TaskStatus
from app.persistence.database import Database
from app.persistence.models import ChatSessionModel, TaskEventModel, TaskModel, utc_now
from app.persistence.repository import TaskRepository

#: A batch waits until nothing new has finished for this long...
QUIET_FOR = timedelta(seconds=30)
#: ...but never longer than this after its first item finished.
AT_MOST = timedelta(minutes=2)
FINISHED = (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.WAITING_FOR_APPROVAL)
NEVER_REPORTED = (TaskStatus.CANCELLED, TaskStatus.SUPERSEDED)
RESULT_CHARACTERS = 600


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=utc_now().tzinfo)


def _kind(task: TaskModel) -> str:
    context = task.source_context or {}
    if context.get("schedule_id"):
        return "routine"
    if TaskRepository._is_coding_task(task):
        return "coding run"
    if "web_research" in (task.required_capabilities or []):
        return "research"
    return "task"


@dataclass(frozen=True)
class Item:
    task_id: str
    kind: str
    goal: str
    status: str
    chat_id: str | None
    chat_title: str | None
    result: str
    pull_request: int | None
    must_push: bool

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _item(session, task: TaskModel, titles: dict[str, str]) -> Item:
    events = list(
        session.scalars(
            select(TaskEventModel)
            .where(TaskEventModel.task_id == task.id)
            .where(
                TaskEventModel.event_type.in_(
                    (EventType.ASSISTANT_REPLY.value, EventType.APPROVAL_REQUESTED.value)
                )
            )
            .order_by(TaskEventModel.sequence.desc())
        )
    )
    reply = next((e.payload for e in events if e.event_type == EventType.ASSISTANT_REPLY), {})
    approval = next((e.payload for e in events if e.event_type == EventType.APPROVAL_REQUESTED), {})
    status = TaskStatus(task.status)
    number = approval.get("number") if status == TaskStatus.WAITING_FOR_APPROVAL else None
    return Item(
        task_id=task.id,
        kind=_kind(task),
        goal=" ".join(task.current_goal.split())[:200],
        status=status.value,
        chat_id=task.chat_session_id,
        chat_title=titles.get(task.chat_session_id or ""),
        result=str(reply.get("text") or "")[:RESULT_CHARACTERS],
        pull_request=int(number) if isinstance(number, int) else None,
        # The must-knows: a PR to review, anything that failed, a watch that triggered.
        must_push=status in (TaskStatus.WAITING_FOR_APPROVAL, TaskStatus.FAILED)
        or bool(reply.get("notable")),
    )


class Reporter:
    def __init__(self, tasks: Any, database: Database):
        self.tasks = tasks
        self.database = database

    def run_due(self, *, now: datetime | None = None) -> str | None:
        """Start a report for finished work once its batch has settled; the new task's id."""
        now = now or utc_now()
        home = self.tasks.home_chat()
        with self.database.session() as session, session.begin():
            pending = list(
                session.scalars(
                    select(TaskModel)
                    .where(TaskModel.reported_at.is_(None))
                    .where(TaskModel.status.in_([s.value for s in (*FINISHED, *NEVER_REPORTED)]))
                    .order_by(TaskModel.updated_at)
                    .with_for_update(skip_locked=True)
                )
            )
            batch = []
            for task in pending:
                context = task.source_context or {}
                if (
                    TaskStatus(task.status) in NEVER_REPORTED
                    or TaskRepository.is_reply(task)
                    or context.get("report")
                    or task.chat_session_id == home.id  # its result is already in Assistant
                ):
                    task.reported_at = now  # nothing to tell; don't look at it again
                else:
                    batch.append(task)
            if not batch:
                return None
            newest = max(_aware(t.updated_at) for t in batch)
            oldest = min(_aware(t.updated_at) for t in batch)
            if now - newest < QUIET_FOR and now - oldest < AT_MOST:
                return None  # more may finish in a moment; tell them together
            chat_ids = {t.chat_session_id for t in batch if t.chat_session_id}
            titles = dict(
                session.execute(
                    select(ChatSessionModel.id, ChatSessionModel.title).where(
                        ChatSessionModel.id.in_(chat_ids)
                    )
                ).all()
            )
            items = [_item(session, task, titles) for task in batch]
            for task in batch:
                task.reported_at = now
        report = self.tasks.create_task(
            request=report_request(items),
            chat_session_id=home.id,
            post_request=False,
            source_context={"report": [item.as_dict() for item in items]},
        )
        return report.id


def report_request(items: list[Item]) -> str:
    """What the report task is asked: the finished work, as data for the chat agent."""
    lines = ["Finished background work to tell the user about:"]
    for item in items:
        where = f" (in “{item.chat_title}”)" if item.chat_title else ""
        state = {
            "completed": "done",
            "failed": "FAILED",
            "waiting_for_approval": f"draft PR #{item.pull_request} ready for review"
            if item.pull_request
            else "waiting for the user's approval",
        }[item.status]
        lines.append(f"- {item.kind}: “{item.goal}”{where} — {state}")
        if item.result:
            lines.append(f"  result: {' '.join(item.result.split())}")
    return "\n".join(lines)


def fallback_text(items: list[dict[str, Any]]) -> str:
    """The report in plain words, when no model could write it."""
    lines = []
    for item in items:
        if item["status"] == "waiting_for_approval":
            pr = f"PR #{item['pull_request']}" if item.get("pull_request") else "Something"
            lines.append(f"Ready for your review: {pr}, “{item['goal']}”.")
        elif item["status"] == "failed":
            lines.append(f"This {item['kind']} failed: “{item['goal']}”.")
        else:
            lines.append(f"Back: {item['kind']} “{item['goal']}”.")
    return "\n".join(lines)


def links(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    """One link per item: the PR's task panel to review it, or the chat with the result."""
    found = []
    for item in items:
        if not item.get("chat_id"):
            continue
        if item["status"] == "waiting_for_approval":
            label = f"Review PR #{item['pull_request']}" if item.get("pull_request") else "Review"
            href = f"#/chats/{item['chat_id']}?task={item['task_id']}"
        else:
            label = item.get("chat_title") or item["kind"].capitalize()
            href = f"#/chats/{item['chat_id']}"
        found.append({"type": "link", "label": label[:80], "href": href})
    return found


def needs_push(items: list[dict[str, Any]], *, notable: bool) -> int | None:
    """The push priority for a report, or None when it can wait in Assistant."""
    urgent = any(item["status"] in ("waiting_for_approval", "failed") for item in items)
    if urgent:
        return 4
    if notable or any(item.get("must_push") for item in items):
        return 3
    return None
