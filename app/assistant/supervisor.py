"""The chat agent's view of its coding runs: overview, start, look closer, steer, stop.

The chat agent's coding tools (``coding_tools`` in ``app/assistant/tools.py``) call these.
The gates live here, not in a prompt: a coding agent only ever works on a new branch and
opens a draft pull request, and there is no tool to merge, mark ready or deploy. Those
stay behind the approval token in the console.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.assistant.overview import RunView, coding_runs, render_overview
from app.coding.flow import revise_pull_request, start_coding_run
from app.domain.enums import EventType, TaskStatus
from app.persistence.database import Database
from app.persistence.models import TaskEventModel
from app.repositories import RepositoryRegistry
from app.services import TaskService
from app.workflows import WorkflowService

logger = logging.getLogger(__name__)


class SupervisorTools:
    """What the supervisor can do; each returns plain data for the model."""

    def __init__(
        self,
        database: Database,
        tasks: TaskService,
        workflows: WorkflowService,
        repositories: RepositoryRegistry,
    ):
        self.database = database
        self.tasks = tasks
        self.workflows = workflows
        self.repositories = repositories

    def overview(self, chat_session_id: str | None) -> tuple[str, set[str]]:
        """The rendered overview and the URLs in it, the only ones a reply may link to."""
        views = coding_runs(self.database, chat_session_id=chat_session_id)
        return render_overview(views), {v.pull_request_url for v in views if v.pull_request_url}

    def repository_catalog(self) -> list[dict[str, Any]]:
        return [
            {"id": m.id, "name": m.name, "description": m.description, "aliases": list(m.aliases)}
            for m in self.repositories.list()
        ]

    def _find(self, ref: str | None, chat_session_id: str | None) -> RunView | None:
        ref = (ref or "").strip().lower()
        if not ref:
            return None
        for view in coding_runs(self.database, chat_session_id=chat_session_id, limit=20):
            if view.run_id.startswith(ref) or (view.task_id or "").startswith(ref):
                return view
        return None

    def run_detail(self, ref: str | None, chat_session_id: str | None) -> dict[str, Any]:
        view = self._find(ref, chat_session_id)
        if view is None:
            return {"error": f"No run matches {ref!r}; use a run id from ACTIVE WORK."}
        steps: list[str] = []
        if view.task_id:
            with self.database.session() as session:
                steps = [
                    str(event.payload.get("text", ""))
                    for event in session.scalars(
                        select(TaskEventModel)
                        .where(TaskEventModel.task_id == view.task_id)
                        .where(TaskEventModel.event_type == EventType.TASK_PROGRESS.value)
                        .order_by(TaskEventModel.sequence.desc())
                        .limit(8)
                    )
                    if event.payload.get("kind") != "plan"
                ][::-1]
        return {
            "run": view.ref,
            "repository": view.repository_id,
            "goal": view.goal,
            "state": view.state,
            "plan": view.plan,
            "recent_steps": steps,
            "pull_request": view.pull_request_url,
        }

    def start_coding(
        self,
        repository_id: str | None,
        goal: str | None,
        *,
        chat_session_id: str | None,
        origin_message_id: str | None,
    ) -> dict[str, Any]:
        if not goal or not goal.strip():
            return {"error": "A goal is required."}
        try:
            run = start_coding_run(
                self.tasks,
                self.workflows,
                self.repositories,
                repository_id=repository_id or "",
                request=goal,
                chat_session_id=chat_session_id,
                origin_message_id=origin_message_id,
                approved_by="supervisor",
            )
        except (LookupError, ValueError) as error:
            return {"error": str(error)}
        return {"started": run.id[:8], "workflow_run_id": run.id, "state": "queued"}

    def message_agent(
        self, ref: str | None, text: str | None, chat_session_id: str | None
    ) -> dict[str, Any]:
        view = self._find(ref, chat_session_id)
        if view is None or view.task_id is None:
            return {"error": f"No started run matches {ref!r}."}
        if not text or not text.strip():
            return {"error": "Say what the agent should do differently."}
        task = self.tasks.get_task(view.task_id)
        status = TaskStatus(task.status)
        if status == TaskStatus.WAITING_FOR_APPROVAL:
            try:
                revision = revise_pull_request(self.tasks, task.id, text.strip())
            except (LookupError, ValueError) as error:
                return {"error": str(error)}
            return {"revision_started": revision.id[:8], "on_pull_request": view.pull_request_url}
        if status in (TaskStatus.CREATED, TaskStatus.PLANNING, TaskStatus.EXECUTING):
            self.tasks.add_user_message(task.id, text.strip())
            return {
                "sent_to": view.ref,
                "note": "The agent picks this up within seconds and continues with it."
                if status == TaskStatus.EXECUTING
                else "The agent will start with this.",
            }
        if status == TaskStatus.VALIDATING:
            return {"error": "The run is finishing up; once its PR is open, I can revise it."}
        return {"error": f"The run is {view.state}; start a new run instead."}

    def stop_run(self, ref: str | None, chat_session_id: str | None) -> dict[str, Any]:
        view = self._find(ref, chat_session_id)
        if view is None:
            return {"error": f"No run matches {ref!r}."}
        try:
            if view.task_id:
                self.tasks.cancel_task(view.task_id)
            else:
                self.workflows.decide(view.run_id, approve=False, by="supervisor")
        except (LookupError, ValueError) as error:
            return {"error": str(error)}
        return {"stopped": view.ref}
