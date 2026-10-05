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
RECENT = timedelta(hours=48)


@dataclass(frozen=True)
class RunView:
    run_id: str
    task_id: str | None
    repository_id: str
    goal: str
    state: str
    progress: str | None = None
    plan: str | None = None
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


def _state(run: WorkflowRunModel, task: TaskModel | None, checkpoint: CodingRunModel | None) -> str:
    if run.status == "proposed":
        return "waiting for the user's approval to start"
    if task is None:
        return run.status
    status = TaskStatus(task.status)
    number = checkpoint.pull_request_number if checkpoint else None
    if status == TaskStatus.WAITING_FOR_APPROVAL and number:
        return f"draft PR #{number} is ready and waiting for the user's review"
    if status == TaskStatus.COMPLETED:
        return f"done; PR #{number} merged" if number else "done"
    if status == TaskStatus.FAILED:
        reason = (checkpoint.last_error if checkpoint else None) or "see the run"
        return f"failed: {reason[:160]}"
    if status == TaskStatus.CANCELLED:
        return "stopped"
    if status == TaskStatus.CREATED:
        return "queued for a coding agent"
    phase = checkpoint.phase if checkpoint else "starting"
    return {
        "preparing": "preparing the branch",
        "agent_running": "the agent is working",
        "validated": "validated; committing",
        "committed": "pushing the branch",
        "pushed": "opening the pull request",
    }.get(phase, f"working ({phase})")


def coding_runs(
    database: Database, *, chat_session_id: str | None = None, limit: int = 8
) -> list[RunView]:
    """Active coding runs, then ones that changed in the last two days; this chat first."""
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
        task_ids = [run.task_id for run in runs if run.task_id]
        tasks = {
            task.id: task
            for task in session.scalars(select(TaskModel).where(TaskModel.id.in_(task_ids)))
        }
        checkpoints = {
            row.task_id: row
            for row in session.scalars(
                select(CodingRunModel).where(CodingRunModel.task_id.in_(task_ids))
            )
        }
        progress: dict[str, dict[str, str]] = {}
        for event in session.scalars(
            select(TaskEventModel)
            .where(TaskEventModel.task_id.in_(task_ids))
            .where(TaskEventModel.event_type == EventType.TASK_PROGRESS.value)
            .order_by(TaskEventModel.task_id, TaskEventModel.sequence.desc())
        ):
            entry = progress.setdefault(event.task_id, {})
            key = "plan" if event.payload.get("kind") == "plan" else "progress"
            entry.setdefault(key, str(event.payload.get("text", "")))
        views = []
        for run in runs:
            task = tasks.get(run.task_id) if run.task_id else None
            checkpoint = checkpoints.get(run.task_id) if run.task_id else None
            reported = progress.get(run.task_id or "", {})
            active = task is None or TaskStatus(task.status) in (
                TaskStatus.CREATED,
                TaskStatus.PLANNING,
                TaskStatus.EXECUTING,
                TaskStatus.VALIDATING,
            )
            views.append(
                RunView(
                    run_id=run.id,
                    task_id=run.task_id,
                    repository_id=str(run.workflow_input.get("repository_id", "")),
                    goal=_goal(str(run.workflow_input.get("request", ""))),
                    state=_state(run, task, checkpoint),
                    progress=reported.get("progress") if active else None,
                    plan=reported.get("plan") if active else None,
                    pull_request_url=checkpoint.pull_request_url if checkpoint else None,
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


def render_overview(views: list[RunView], *, now: datetime | None = None) -> str:
    if not views:
        return "No coding runs are active or recent."
    lines = []
    for view in views:
        line = f"- run {view.ref} [{view.repository_id}] “{view.goal}”: {view.state}"
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
