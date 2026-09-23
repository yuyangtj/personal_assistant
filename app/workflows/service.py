from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from app.deployments import DeploymentRegistry
from app.domain.enums import TaskStatus
from app.persistence.database import Database
from app.persistence.models import (
    ChatMessageModel,
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
    """Persists workflow authority and advances state only from trusted evidence."""

    def __init__(
        self,
        database: Database,
        registry: WorkflowRegistry,
        repository_registry: RepositoryRegistry,
        deployment_registry: DeploymentRegistry | None = None,
    ):
        self.database = database
        self.registry = registry
        self.repository_registry = repository_registry
        self.deployment_registry = deployment_registry

    def create_run(
        self,
        *,
        workflow_id: str,
        workflow_input: dict[str, Any],
        chat_session_id: str | None = None,
        task_id: str | None = None,
        origin_message_id: str | None = None,
    ) -> WorkflowRunModel:
        manifest = self.registry.get(workflow_id)
        normalized_input = dict(workflow_input)
        missing = [name for name in manifest.required_inputs if not normalized_input.get(name)]
        if missing:
            raise ValueError("Missing required workflow input: " + ", ".join(missing))
        if len(json.dumps(normalized_input, default=str)) > 50_000:
            raise ValueError("Workflow input is too large")
        repository_id = normalized_input.get("repository_id")
        if repository_id:
            repository = self.repository_registry.get(str(repository_id))
            normalized_input["repository_id"] = repository.id
        github_repository = normalized_input.get("github_repository")
        if github_repository and not GITHUB_REPOSITORY_PATTERN.fullmatch(str(github_repository)):
            raise ValueError("github_repository must be in owner/repository format")
        if manifest.id == "assistant-deployment":
            if self.deployment_registry is None:
                raise ValueError("Deployment registry is not configured")
            target = self.deployment_registry.get(str(normalized_input["deployment_target_id"]))
            if not target.enabled:
                raise ValueError("Deployment target is disabled")
            self.repository_registry.get(target.repository_id)
            commit_sha = str(normalized_input["commit_sha"])
            if not re.fullmatch(r"[0-9a-f]{40,64}", commit_sha):
                raise ValueError("commit_sha must be an immutable lowercase Git SHA")
            normalized_input["deployment_target_id"] = target.id
            normalized_input["repository_id"] = target.repository_id

        with self.database.session() as session, session.begin():
            if chat_session_id and session.get(ChatSessionModel, chat_session_id) is None:
                raise ValueError("Chat session does not exist")
            if task_id and session.get(TaskModel, task_id) is None:
                raise ValueError("Task does not exist")
            if origin_message_id:
                message = session.get(ChatMessageModel, origin_message_id)
                if message is None or message.chat_session_id != chat_session_id:
                    raise ValueError("Origin message does not belong to the chat session")
                existing = session.scalar(
                    select(WorkflowRunModel).where(
                        WorkflowRunModel.origin_message_id == origin_message_id
                    )
                )
                if existing is not None:
                    if (
                        existing.workflow_id != manifest.id
                        or existing.workflow_input != normalized_input
                    ):
                        raise WorkflowRunConflictError(
                            "Origin message already has a different workflow proposal"
                        )
                    return existing
            run = WorkflowRunModel(
                id=str(uuid4()),
                workflow_id=manifest.id,
                status=WorkflowRunStatus.PROPOSED.value,
                workflow_input=normalized_input,
                current_stage=None,
                chat_session_id=chat_session_id,
                task_id=task_id,
                origin_message_id=origin_message_id,
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
            self.registry.get(run.workflow_id)
            if approve:
                run.status = WorkflowRunStatus.APPROVED.value
                run.current_stage = None
                event_type = "WORKFLOW_APPROVED"
            else:
                run.status = WorkflowRunStatus.REJECTED.value
                event_type = "WORKFLOW_REJECTED"
            run.updated_at = utc_now()
            self._append_event(session, run, event_type, {})
            return run

    def attach_coding_task(self, workflow_run_id: str, *, task_id: str) -> WorkflowRunModel:
        with self.database.session() as session, session.begin():
            run = session.scalar(
                select(WorkflowRunModel)
                .where(WorkflowRunModel.id == workflow_run_id)
                .with_for_update()
            )
            if run is None:
                raise WorkflowRunNotFoundError(workflow_run_id)
            if run.workflow_id != "coding-change":
                raise WorkflowRunConflictError(
                    f"Workflow {run.workflow_id} has no trusted execution adapter"
                )
            if run.task_id is not None:
                if run.task_id != task_id:
                    raise WorkflowRunConflictError("Workflow run is linked to another task")
                return run
            if run.status != WorkflowRunStatus.APPROVED.value:
                raise WorkflowRunConflictError("Workflow run must be approved before it can start")
            if session.get(TaskModel, task_id) is None:
                raise ValueError("Task does not exist")
            run.task_id = task_id
            run.status = WorkflowRunStatus.RUNNING.value
            run.current_stage = "implement"
            run.updated_at = utc_now()
            self._append_event(
                session,
                run,
                "WORKFLOW_STARTED",
                {"task_id": task_id, "stage": run.current_stage},
            )
            return run

    def sync_run(self, workflow_run_id: str) -> WorkflowRunModel:
        with self.database.session() as session, session.begin():
            run = session.scalar(
                select(WorkflowRunModel)
                .where(WorkflowRunModel.id == workflow_run_id)
                .with_for_update()
            )
            if run is None:
                raise WorkflowRunNotFoundError(workflow_run_id)
            if run.status in {
                WorkflowRunStatus.COMPLETED.value,
                WorkflowRunStatus.FAILED.value,
                WorkflowRunStatus.CANCELLED.value,
                WorkflowRunStatus.REJECTED.value,
            }:
                return run
            if run.status != WorkflowRunStatus.RUNNING.value or run.task_id is None:
                raise WorkflowRunConflictError("Workflow run has not started")
            task = session.get(TaskModel, run.task_id)
            if task is None:
                raise WorkflowRunConflictError("Linked workflow task no longer exists")
            task_status = TaskStatus(task.status)
            target_status, target_stage, event_type = self._state_from_task(task_status)
            changed = run.status != target_status.value or run.current_stage != target_stage
            if changed:
                run.status = target_status.value
                run.current_stage = target_stage
                run.updated_at = utc_now()
                self._append_event(
                    session,
                    run,
                    event_type,
                    {
                        "task_id": task.id,
                        "task_status": task_status.value,
                        "stage": target_stage,
                    },
                )
            return run

    def sync_for_task(self, task_id: str) -> WorkflowRunModel | None:
        with self.database.session() as session:
            run_id = session.scalar(
                select(WorkflowRunModel.id).where(WorkflowRunModel.task_id == task_id)
            )
        return self.sync_run(run_id) if run_id else None

    @staticmethod
    def _state_from_task(
        task_status: TaskStatus,
    ) -> tuple[WorkflowRunStatus, str, str]:
        if task_status == TaskStatus.VALIDATING:
            return WorkflowRunStatus.RUNNING, "validate", "WORKFLOW_STAGE_CHANGED"
        if task_status == TaskStatus.WAITING_FOR_APPROVAL:
            return WorkflowRunStatus.RUNNING, "review", "WORKFLOW_STAGE_CHANGED"
        if task_status == TaskStatus.COMPLETED:
            return WorkflowRunStatus.COMPLETED, "merge", "WORKFLOW_COMPLETED"
        if task_status == TaskStatus.FAILED:
            return WorkflowRunStatus.FAILED, "implement", "WORKFLOW_FAILED"
        if task_status == TaskStatus.CANCELLED:
            return WorkflowRunStatus.CANCELLED, "implement", "WORKFLOW_CANCELLED"
        if task_status == TaskStatus.SUPERSEDED:
            return WorkflowRunStatus.RUNNING, "implement", "WORKFLOW_REVISION_REQUESTED"
        return WorkflowRunStatus.RUNNING, "implement", "WORKFLOW_STAGE_CHANGED"

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
