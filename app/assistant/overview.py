"""What is going on right now: the assistant's view of its coding runs and other work.

The supervisor reads this before every reply, so a question like "how's the icon
change going?" is answered from the real state of the runs, not from memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select

from app.domain.enums import EventType, TaskStatus
from app.persistence.database import Database
from app.persistence.models import (
    CodingRunModel,
    TaskEventModel,
    TaskModel,
    WorkflowRunModel,
    utc_now,
)

ACTIVE_RUN_STATUSES = ("proposed", "approved", "running")
RECENT = timedelta(days=7)

# What a run means for the user, shown first on each line and in the summary.
WAITING_TO_START = "WAITING TO START"
QUEUED = "QUEUED"
WORKING = "WORKING"
NEEDS_REVIEW = "NEEDS YOUR REVIEW"
MERGED = "MERGED"
DONE = "DONE"
FAILED = "FAILED"
STOPPED = "STOPPED"


@dataclass(frozen=True)
class RunView:
    run_id: str
    task_id: str | None
    repository_id: str
    goal: str
    status: str
    state: str
    progress: str | None = None
    plan: str | None = None
    pull_request_number: int | None = None
    pull_request_url: str | None = None
    in_this_chat: bool = False
    #: The console page where the user reviews (and approves) this run.
    review_href: str | None = None

    @property
    def ref(self) -> str:
        return self.run_id[:8]


def _goal(request: str) -> str:
    first = next((line.strip() for line in request.splitlines() if line.strip()), "")
    return first[:160]


@dataclass(frozen=True)
class _History:
    """A run's tasks, newest first: a revision continues the task it superseded."""

    tasks: list[TaskModel]
    checkpoints: dict[str, CodingRunModel]
    merged: set[str]
    failures: dict[str, str]

    @property
    def current(self) -> TaskModel | None:
        return self.tasks[0] if self.tasks else None

    def pull_request(self) -> tuple[int | None, str | None]:
        for task in self.tasks:
            checkpoint = self.checkpoints.get(task.id)
            if checkpoint and checkpoint.pull_request_number:
                return checkpoint.pull_request_number, checkpoint.pull_request_url
        return None, None

    def ever_merged(self) -> bool:
        return any(task.id in self.merged for task in self.tasks)


def _status(run: WorkflowRunModel, history: _History) -> tuple[str, str]:
    """(status, what it means in a few words) for one run."""
    number, _ = history.pull_request()
    task = history.current
    if run.status == "proposed":
        return WAITING_TO_START, "proposed; waiting for the user's approval to start"
    if run.status == "rejected":
        return STOPPED, "declined before it started"
    if history.ever_merged():
        state = f"PR #{number} merged" if number else "merged"
        if task is not None and TaskStatus(task.status) in (
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ):
            reason = history.failures.get(task.id, "")
            state += "; a later revision " + (f"failed: {reason}" if reason else "stopped")
        return MERGED, state
    if task is None:
        return QUEUED if run.status == "approved" else STOPPED, run.status
    status = TaskStatus(task.status)
    if status == TaskStatus.WAITING_FOR_APPROVAL:
        return (
            NEEDS_REVIEW,
            f"draft PR #{number} is ready for review" if number else "ready for review",
        )
    if run.status == "cancelled" or status in (TaskStatus.CANCELLED, TaskStatus.SUPERSEDED):
        return STOPPED, "stopped"
    if status == TaskStatus.COMPLETED:
        return DONE, "finished" + ("" if number else " without a pull request")
    if status == TaskStatus.FAILED:
        reason = history.failures.get(task.id) or "no reason recorded"
        return FAILED, f"failed: {reason}"
    if status == TaskStatus.CREATED:
        return QUEUED, "queued for a coding agent"
    checkpoint = history.checkpoints.get(task.id)
    phase = checkpoint.phase if checkpoint else "starting"
    return WORKING, {
        "preparing": "preparing the branch",
        "agent_running": "the agent is working",
        "validated": "validated; committing",
        "committed": "pushing the branch",
        "pushed": "opening the pull request",
    }.get(phase, f"working ({phase})")


def coding_runs(
    database: Database, *, chat_session_id: str | None = None, limit: int = 8
) -> list[RunView]:
    """Active coding runs, then ones that changed in the last week; this chat first."""
    since = utc_now() - RECENT
    with database.session() as session:
        runs = list(
            session.scalars(
                select(WorkflowRunModel)
                .where(WorkflowRunModel.workflow_id == "coding-change")
                .where(
                    or_(
                        WorkflowRunModel.status.in_(ACTIVE_RUN_STATUSES),
                        WorkflowRunModel.updated_at >= since,
                    )
                )
                .order_by(WorkflowRunModel.updated_at.desc())
                .limit(limit * 3)
            )
        )
        histories: dict[str, list[TaskModel]] = {}
        for run in runs:
            chain, task_id = [], run.task_id
            while task_id and len(chain) < 10:  # follow revisions back to the first task
                task = session.get(TaskModel, task_id)
                if task is None:
                    break
                chain.append(task)
                task_id = task.parent_task_id
            histories[run.id] = chain
        task_ids = [task.id for chain in histories.values() for task in chain]
        checkpoints = {
            row.task_id: row
            for row in session.scalars(
                select(CodingRunModel).where(CodingRunModel.task_id.in_(task_ids))
            )
        }
        merged: set[str] = set()
        failures: dict[str, str] = {}
        progress: dict[str, dict[str, str]] = {}
        for event in session.scalars(
            select(TaskEventModel)
            .where(TaskEventModel.task_id.in_(task_ids))
            .where(
                TaskEventModel.event_type.in_(
                    [
                        EventType.TASK_PROGRESS.value,
                        EventType.ARTIFACT_CREATED.value,
                        EventType.TASK_FAILED.value,
                    ]
                )
            )
            .order_by(TaskEventModel.task_id, TaskEventModel.sequence.desc())
        ):
            if event.event_type == EventType.ARTIFACT_CREATED.value:
                if event.payload.get("type") == "github_pull_request_merge":
                    merged.add(event.task_id)
            elif event.event_type == EventType.TASK_FAILED.value:
                failures.setdefault(event.task_id, str(event.payload.get("error") or "")[:160])
            else:
                entry = progress.setdefault(event.task_id, {})
                key = "plan" if event.payload.get("kind") == "plan" else "progress"
                entry.setdefault(key, str(event.payload.get("text", "")))
        for task_id in task_ids:  # a checkpoint's error is the most specific reason
            checkpoint = checkpoints.get(task_id)
            if checkpoint is not None and checkpoint.last_error:
                failures[task_id] = checkpoint.last_error[:160]
        views = []
        for run in runs:
            history = _History(histories[run.id], checkpoints, merged, failures)
            status, state = _status(run, history)
            number, url = history.pull_request()
            reported = progress.get(run.task_id or "", {})
            active = status in (QUEUED, WORKING)
            views.append(
                RunView(
                    run_id=run.id,
                    task_id=run.task_id,
                    repository_id=str(run.workflow_input.get("repository_id", "")),
                    goal=_goal(str(run.workflow_input.get("request", ""))),
                    status=status,
                    state=state,
                    progress=reported.get("progress") if active else None,
                    plan=reported.get("plan") if active else None,
                    pull_request_number=number,
                    pull_request_url=url,
                    in_this_chat=bool(chat_session_id) and run.chat_session_id == chat_session_id,
                    review_href=(
                        f"#/chats/{run.chat_session_id}?task={run.task_id}"
                        if run.chat_session_id and run.task_id
                        else None
                    ),
                )
            )
    views.sort(key=lambda view: not view.in_this_chat)  # stable: keeps recency within groups
    return views[:limit]


def _name(view: RunView) -> str:
    return (
        f"PR #{view.pull_request_number} (run {view.ref})"
        if view.pull_request_number
        else f"run {view.ref}"
    )


def render_overview(views: list[RunView], *, now: datetime | None = None) -> str:
    """A summary of what needs the user, then one line per run; every fact comes from data."""
    if not views:
        return "No coding runs in the last week."

    def group(*statuses: str) -> str:
        names = [_name(view) for view in views if view.status in statuses]
        return ", ".join(names) if names else "none"

    lines = [
        f"Needs your review: {group(NEEDS_REVIEW)}",
        f"Waiting to start: {group(WAITING_TO_START)}",
        f"Working now: {group(WORKING, QUEUED)}",
        f"Failed: {group(FAILED)}",
        f"Merged: {group(MERGED)}",
        "",
        "Runs, newest first:",
    ]
    for view in views:
        line = (
            f"- [{view.status}] run {view.ref} [{view.repository_id}] “{view.goal}”: {view.state}"
        )
        if view.progress:
            line += f"; latest step: {view.progress}"
        if view.pull_request_url:
            line += f"; PR: {view.pull_request_url}"
        if view.review_href:
            line += f"; review in the console: {view.review_href}"
        if view.in_this_chat:
            line += " (started from this chat)"
        lines.append(line)
        if view.plan:
            lines.extend("    " + entry for entry in view.plan.splitlines()[:8])
    return "\n".join(lines)
