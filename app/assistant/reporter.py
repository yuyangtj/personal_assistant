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

from sqlalchemy import func, select

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
#: A failed research or routine run is tried once more, this long after it failed.
RETRY_AFTER = timedelta(seconds=60)
RETRIED_KINDS = ("research", "routine")
#: Work still queued this long after it was asked for: something isn't picking it up.
NOT_STARTED_AFTER = timedelta(minutes=15)
#: Work running with no news for this long (coding agents get longer).
STALLED_AFTER = {"coding run": timedelta(minutes=45)}
STALLED_DEFAULT = timedelta(minutes=20)
#: A PR waiting for review is mentioned again once a day.
REVIEW_REMINDER_EVERY = timedelta(hours=24)
RUNNING = (TaskStatus.PLANNING, TaskStatus.EXECUTING, TaskStatus.VALIDATING)


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
    #: For a failure: it was already a retry, so it failed twice.
    tried_twice: bool = False
    #: For stuck work: when it last showed a sign of life.
    since: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _item(
    session,
    task: TaskModel,
    titles: dict[str, str],
    *,
    status: str | None = None,
    since: datetime | None = None,
) -> Item:
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
    task_status = TaskStatus(task.status)
    number = approval.get("number") if task_status == TaskStatus.WAITING_FOR_APPROVAL else None
    shown = status or task_status.value
    return Item(
        task_id=task.id,
        kind=_kind(task),
        goal=" ".join(task.current_goal.split())[:200],
        status=shown,
        chat_id=task.chat_session_id,
        chat_title=titles.get(task.chat_session_id or ""),
        result=str(reply.get("text") or "")[:RESULT_CHARACTERS],
        pull_request=int(number) if isinstance(number, int) else None,
        # The must-knows: a PR to review, anything that failed or is stuck, a watch that
        # triggered. A reminder about a PR still waiting is not one: its first report was.
        must_push=shown in ("waiting_for_approval", "failed", "not_started", "stalled")
        or bool(reply.get("notable")),
        tried_twice=bool((task.source_context or {}).get("retry_of")),
        since=_aware(since).strftime("%H:%M UTC") if since else None,
    )


class Reporter:
    def __init__(self, tasks: Any, database: Database):
        self.tasks = tasks
        self.database = database

    def run_due(self, *, now: datetime | None = None) -> str | None:
        """Report finished or stuck work once its batch has settled; the report task's id.

        A failed research or routine run is first tried once more on its own.
        """
        now = now or utc_now()
        home = self.tasks.home_chat()
        retries: list[dict[str, Any]] = []
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
                if TaskStatus(task.status) in NEVER_REPORTED or not _ours(task, home.id):
                    task.reported_at = now  # nothing to tell; don't look at it again
                elif _retryable(task):
                    if now - _aware(task.updated_at) < RETRY_AFTER:
                        continue  # a moment's pause before trying again
                    retries.append(_retry_spec(task))
                    task.reported_at = now  # its retry reports, whichever way it goes
                else:
                    batch.append(task)
            stuck = _stuck(session, home.id, now)
            ready = bool(stuck)
            if batch:
                newest = max(_aware(t.updated_at) for t in batch)
                oldest = min(_aware(t.updated_at) for t in batch)
                ready = ready or now - newest >= QUIET_FOR or now - oldest >= AT_MOST
            items: list[Item] = []
            if ready:
                every = [*batch, *(task for task, _, _ in stuck)]
                chat_ids = {t.chat_session_id for t in every if t.chat_session_id}
                titles = dict(
                    session.execute(
                        select(ChatSessionModel.id, ChatSessionModel.title).where(
                            ChatSessionModel.id.in_(chat_ids)
                        )
                    ).all()
                )
                items = [_item(session, task, titles) for task in batch]
                items += [
                    _item(session, task, titles, status=state, since=at)
                    for task, state, at in stuck
                ]
                for task in batch:
                    task.reported_at = now
                for task, _, _ in stuck:
                    task.followed_up_at = now
        for spec in retries:
            self.tasks.create_task(**spec)
        if not items:
            return None  # nothing yet, or more may finish in a moment: tell them together
        report = self.tasks.create_task(
            request=report_request(items),
            chat_session_id=home.id,
            post_request=False,
            source_context={"report": [item.as_dict() for item in items]},
        )
        return report.id


def _ours(task: TaskModel, home_id: str) -> bool:
    """Background work that started outside Assistant (results from there show there)."""
    return not (
        TaskRepository.is_reply(task)
        or (task.source_context or {}).get("report")
        or task.chat_session_id == home_id
    )


def _retryable(task: TaskModel) -> bool:
    return (
        TaskStatus(task.status) == TaskStatus.FAILED
        and _kind(task) in RETRIED_KINDS
        and not (task.source_context or {}).get("retry_of")
    )


def _retry_spec(task: TaskModel) -> dict[str, Any]:
    """The same work once more, marked as a retry so it is never retried again."""
    context = {
        key: value
        for key, value in (task.source_context or {}).items()
        if key not in ("report", "retry_of")
    }
    return {
        "request": task.original_request,
        "goal": task.current_goal,
        "required_capabilities": list(task.required_capabilities or []) or None,
        "chat_session_id": task.chat_session_id,
        "work_item_id": task.work_item_id,
        "post_request": False,
        "source_context": {**context, "retry_of": task.id},
    }


def _stuck(session, home_id: str, now: datetime) -> list[tuple[TaskModel, str, datetime]]:
    """Work that never started, stopped making progress, or still waits for review."""
    watched = (TaskStatus.CREATED, *RUNNING, TaskStatus.WAITING_FOR_APPROVAL)
    candidates = session.scalars(
        select(TaskModel)
        .where(TaskModel.status.in_([s.value for s in watched]))
        .with_for_update(skip_locked=True)
    )
    found: list[tuple[TaskModel, str, datetime]] = []
    for task in candidates:
        if not _ours(task, home_id):
            continue
        status = TaskStatus(task.status)
        raised = task.followed_up_at
        if status == TaskStatus.WAITING_FOR_APPROVAL:
            # Once a day while it waits; the first "ready" was in an earlier report.
            waited = now - _aware(task.updated_at) >= REVIEW_REMINDER_EVERY
            due = raised is None or now - _aware(raised) >= REVIEW_REMINDER_EVERY
            if waited and due:
                found.append((task, "review_waiting", task.updated_at))
            continue
        if raised is not None:
            continue  # stuck work is raised once
        if status == TaskStatus.CREATED:
            if now - _aware(task.created_at) >= NOT_STARTED_AFTER:
                found.append((task, "not_started", task.created_at))
            continue
        last = (
            session.scalar(
                select(func.max(TaskEventModel.created_at)).where(TaskEventModel.task_id == task.id)
            )
            or task.updated_at
        )
        if now - _aware(last) >= STALLED_AFTER.get(_kind(task), STALLED_DEFAULT):
            found.append((task, "stalled", last))
    return found


def report_request(items: list[Item]) -> str:
    """What the report task is asked: the finished work, as data for the chat agent."""
    lines = ["Finished background work to tell the user about:"]
    for item in items:
        where = f" (in “{item.chat_title}”)" if item.chat_title else ""
        pr = f"draft PR #{item.pull_request}" if item.pull_request else "it"
        state = {
            "completed": "done",
            "failed": "FAILED, after being tried twice" if item.tried_twice else "FAILED",
            "waiting_for_approval": f"{pr} ready for review"
            if item.pull_request
            else "waiting for the user's approval",
            "review_waiting": f"{pr} still waiting for the user's review, since {item.since}",
            "not_started": f"STILL NOT STARTED, queued since {item.since}: the worker for it "
            "may be down",
            "stalled": f"SEEMS STUCK: no progress since {item.since}; the user can stop it",
        }[item.status]
        lines.append(f"- {item.kind}: “{item.goal}”{where} — {state}")
        if item.result:
            lines.append(f"  result: {' '.join(item.result.split())}")
    return "\n".join(lines)


def fallback_text(items: list[dict[str, Any]]) -> str:
    """The report in plain words, when no model could write it."""
    lines = []
    for item in items:
        pr = f"PR #{item['pull_request']}" if item.get("pull_request") else "Something"
        if item["status"] == "waiting_for_approval":
            lines.append(f"Ready for your review: {pr}, “{item['goal']}”.")
        elif item["status"] == "review_waiting":
            lines.append(f"Still waiting for your review: {pr}, “{item['goal']}”.")
        elif item["status"] == "failed":
            twice = ", even on a second try" if item.get("tried_twice") else ""
            lines.append(f"This {item['kind']} failed{twice}: “{item['goal']}”.")
        elif item["status"] == "not_started":
            lines.append(f"Not started yet: {item['kind']} “{item['goal']}”.")
        elif item["status"] == "stalled":
            lines.append(f"Seems stuck: {item['kind']} “{item['goal']}”.")
        else:
            lines.append(f"Back: {item['kind']} “{item['goal']}”.")
    return "\n".join(lines)


def links(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    """One link per item: the PR's task panel to review it, or the chat with the result."""
    found = []
    for item in items:
        if not item.get("chat_id"):
            continue
        if item["status"] in ("waiting_for_approval", "review_waiting"):
            label = f"Review PR #{item['pull_request']}" if item.get("pull_request") else "Review"
            href = f"#/chats/{item['chat_id']}?task={item['task_id']}"
        elif item["status"] in ("not_started", "stalled"):
            label = f"Look at the {item['kind']}"
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
