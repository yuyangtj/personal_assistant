from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from app.persistence.database import Database
from app.persistence.models import (
    ChatSessionModel,
    TaskModel,
    WorkflowRunEventModel,
    WorkflowRunModel,
    utc_now,
)
from app.repositories import RepositoryRegistry
from app.workflows.models import WorkflowRunStatus
from app.workflows.registry import WorkflowRegistry

GITHUB_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")


class WorkflowRunNotFoundError(LookupError):
    pass


class WorkflowRunConflictError(ValueError):
    pass


class WorkflowService:
    """Persists proposals and approvals; it intentionally executes no privileged action."""

    def __init__(
        self,
        database: Database,
        registry: WorkflowRegistry,
        repository_registry: RepositoryRegistry,
    ):
        self.database = database
        self.registry = registry
        self.repository_registry = repository_registry

    def create_run(
        self,
        *,
        workflow_id: str,
        workflow_input: dict[str, Any],
        chat_session_id: str | None = None,
        task_id: str | None = None,
    ) -> WorkflowRunModel:
        manifest = self.registry.get(workflow_id)
        normalized_input = dict(workflow_input)
        missing = [
            name for name in manifest.required_inputs if not normalized_input.get(name)
        ]
        if missing:
            raise ValueError("Missing required workflow input: " + ", ".join(missing))
        if len(json.dumps(normalized_input, default=str)) > 50_000:
            raise ValueError("Workflow input is too large")
        repository_id = normalized_input.get("repository_id")
        if repository_id:
            repository = self.repository_registry.get(str(repository_id))
            normalized_input["repository_id"] = repository.id
        github_repository = normalized_input.get("github_repository")
        if github_repository and not GITHUB_REPOSITORY_PATTERN.fullmatch(
            str(github_repository)
        ):
            raise ValueError("github_repository must be in owner/repository format")

        with self.database.session() as session, session.begin():
            if chat_session_id and session.get(ChatSessionModel, chat_session_id) is None:
                raise ValueError("Chat session does not exist")
            if task_id and session.get(TaskModel, task_id) is None:
                raise ValueError("Task does not exist")
            run = WorkflowRunModel(
                id=str(uuid4()),
                workflow_id=manifest.id,
                status=WorkflowRunStatus.PROPOSED.value,
                workflow_input=normalized_input,
                current_stage=None,
                chat_session_id=chat_session_id,
                task_id=task_id,
            )
            session.add(run)
            session.flush()
            self._append_event(
                session,
                run,
                "WORKFLOW_PROPOSED",
                {
                    "workflow_id": manifest.id,
                    "stages": [stage.id for stage in manifest.stages],
                },
            )
            return run

    def get_run(self, workflow_run_id: str) -> WorkflowRunModel:
        with self.database.session() as session:
            run = session.get(WorkflowRunModel, workflow_run_id)
            if run is None:
                raise WorkflowRunNotFoundError(workflow_run_id)
            return run

    def list_runs(self, *, limit: int = 100) -> list[WorkflowRunModel]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(WorkflowRunModel)
                    .order_by(WorkflowRunModel.created_at.desc())
                    .limit(limit)
                )
            )

    def list_events(self, workflow_run_id: str) -> list[WorkflowRunEventModel]:
        with self.database.session() as session:
            if session.get(WorkflowRunModel, workflow_run_id) is None:
                raise WorkflowRunNotFoundError(workflow_run_id)
            return list(
                session.scalars(
                    select(WorkflowRunEventModel)
                    .where(WorkflowRunEventModel.workflow_run_id == workflow_run_id)
                    .order_by(WorkflowRunEventModel.sequence)
                )
            )

    def decide(self, workflow_run_id: str, *, approve: bool) -> WorkflowRunModel:
        with self.database.session() as session, session.begin():
            run = session.scalar(
                select(WorkflowRunModel)
                .where(WorkflowRunModel.id == workflow_run_id)
                .with_for_update()
            )
            if run is None:
                raise WorkflowRunNotFoundError(workflow_run_id)
            if run.status != WorkflowRunStatus.PROPOSED.value:
                raise WorkflowRunConflictError(
                    f"Workflow run is already {run.status}; only proposed runs can be decided"
                )
            manifest = self.registry.get(run.workflow_id)
            if approve:
                run.status = WorkflowRunStatus.APPROVED.value
                run.current_stage = manifest.stages[0].id
                event_type = "WORKFLOW_APPROVED"
            else:
                run.status = WorkflowRunStatus.REJECTED.value
                event_type = "WORKFLOW_REJECTED"
            run.updated_at = utc_now()
            self._append_event(session, run, event_type, {})
            return run

    @staticmethod
    def _append_event(
        session,
        run: WorkflowRunModel,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        sequence = session.scalar(
            select(func.max(WorkflowRunEventModel.sequence)).where(
                WorkflowRunEventModel.workflow_run_id == run.id
            )
        )
        session.add(
            WorkflowRunEventModel(
                workflow_run_id=run.id,
                sequence=(sequence or 0) + 1,
                event_type=event_type,
                payload=payload,
            )
        )
