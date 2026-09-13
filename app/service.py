from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.capabilities.models import IDENTIFIER_PATTERN
from app.domain.enums import EventType, ExecutionStatus, TaskStatus
from app.domain.transitions import TERMINAL_STATUSES, ensure_transition
from app.persistence.database import Database
from app.persistence.models import ExecutionModel, TaskEventModel, TaskModel
from app.persistence.repository import TaskRepository


class TaskNotFoundError(LookupError):
    pass


class ExecutionNotFoundError(LookupError):
    pass


class TaskService:
    def __init__(self, database: Database):
        self.database = database

    def create_task(
        self,
        *,
        request: str,
        goal: str | None = None,
        required_capabilities: list[str] | None = None,
        source_context: dict[str, Any] | None = None,
        external_source: str | None = None,
        external_key: str | None = None,
    ) -> TaskModel:
        normalized_request = request.strip()
        if not normalized_request:
            raise ValueError("Task request cannot be empty")
        if (external_source is None) != (external_key is None):
            raise ValueError("external_source and external_key must be supplied together")
        normalized_capabilities = list(dict.fromkeys(required_capabilities or []))
        if len(normalized_capabilities) != len(required_capabilities or []):
            raise ValueError("required_capabilities must not contain duplicates")
        invalid_capabilities = [
            capability
            for capability in normalized_capabilities
            if not IDENTIFIER_PATTERN.fullmatch(capability)
        ]
        if invalid_capabilities:
            raise ValueError(f"Invalid required capability identifiers: {invalid_capabilities}")

        with self.database.session() as session, session.begin():
            if external_source and external_key:
                existing = TaskRepository.find_external(
                    session,
                    external_source=external_source,
                    external_key=external_key,
                )
                if existing is not None:
                    return existing

            task = TaskModel(
                id=str(uuid4()),
                original_request=normalized_request,
                current_goal=(goal or normalized_request).strip(),
                status=TaskStatus.CREATED.value,
                required_capabilities=normalized_capabilities,
                source_context=source_context or {},
                external_source=external_source,
                external_key=external_key,
            )
            session.add(task)
            TaskRepository.append_event(
                session,
                task,
                EventType.TASK_CREATED,
                {
                    "request": task.original_request,
                    "goal": task.current_goal,
                    "required_capabilities": task.required_capabilities,
                    "source": external_source,
                },
            )
            session.flush()
            return task

    def get_task(self, task_id: str) -> TaskModel:
        with self.database.session() as session:
            task = TaskRepository.get(session, task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            return task

    def list_tasks(self, *, status: TaskStatus | None = None, limit: int = 100) -> list[TaskModel]:
        with self.database.session() as session:
            return TaskRepository.list(session, status=status, limit=limit)

    def list_events(self, task_id: str) -> list[TaskEventModel]:
        with self.database.session() as session:
            if TaskRepository.get(session, task_id) is None:
                raise TaskNotFoundError(task_id)
            return TaskRepository.list_events(session, task_id)

    def claim_next_task(self, *, worker_id: str, lease_seconds: int) -> TaskModel | None:
        with self.database.session() as session, session.begin():
            return TaskRepository.claim_next(
                session,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )

    def record_plan(self, task_id: str, plan: list[str]) -> None:
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            if TaskStatus(task.status) != TaskStatus.PLANNING:
                return
            TaskRepository.append_event(
                session,
                task,
                EventType.PLAN_CREATED,
                {"steps": plan},
            )

    def record_manager_decision(self, task_id: str, decision: dict[str, Any]) -> None:
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            if TaskStatus(task.status) != TaskStatus.PLANNING:
                return
            TaskRepository.append_event(
                session,
                task,
                EventType.MANAGER_DECISION_CREATED,
                decision,
            )

    def record_task_analysis(self, task_id: str, result: dict[str, Any]) -> None:
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            if TaskStatus(task.status) != TaskStatus.PLANNING:
                return
            analysis = result["analysis"]
            task.current_goal = analysis["goal"]
            task.required_capabilities = analysis["required_capabilities"]
            TaskRepository.append_event(
                session,
                task,
                EventType.TASK_ANALYZED,
                result,
            )

    def record_task_analysis_failure(
        self,
        task_id: str,
        failure: dict[str, Any],
    ) -> None:
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            if TaskStatus(task.status) != TaskStatus.PLANNING:
                return
            TaskRepository.append_event(
                session,
                task,
                EventType.TASK_ANALYSIS_FAILED,
                failure,
            )

    def start_execution(
        self,
        task_id: str,
        *,
        capability_id: str,
        executor_id: str,
        execution_input: dict[str, Any],
    ) -> str | None:
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            if TaskStatus(task.status) == TaskStatus.CANCELLED:
                return None
            ensure_transition(task.status, TaskStatus.EXECUTING)
            task.status = TaskStatus.EXECUTING.value

            execution = ExecutionModel(
                id=str(uuid4()),
                task_id=task.id,
                executor_id=executor_id,
                status=ExecutionStatus.RUNNING.value,
                input=execution_input,
            )
            session.add(execution)
            TaskRepository.append_event(
                session,
                task,
                EventType.CAPABILITY_SELECTED,
                {"capability_id": capability_id, "executor_id": executor_id},
            )
            TaskRepository.append_event(
                session,
                task,
                EventType.EXECUTION_STARTED,
                {"execution_id": execution.id, "executor_id": executor_id},
            )
            return execution.id

    def start_validation(
        self,
        task_id: str,
        execution_id: str,
        *,
        output: dict[str, Any],
    ) -> bool:
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            execution = self._require_execution(session, execution_id)
            if TaskStatus(task.status) == TaskStatus.CANCELLED:
                self._mark_execution_cancelled(execution)
                return False

            ensure_transition(task.status, TaskStatus.VALIDATING)
            execution.output = output
            task.status = TaskStatus.VALIDATING.value
            TaskRepository.append_event(
                session,
                task,
                EventType.EXECUTION_OUTPUT_RECEIVED,
                {"execution_id": execution_id, "output": output},
            )
            TaskRepository.append_event(
                session,
                task,
                EventType.VALIDATION_STARTED,
                {"execution_id": execution_id},
            )
            return True

    def complete_task(self, task_id: str, execution_id: str) -> TaskModel:
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            execution = self._require_execution(session, execution_id)
            if TaskStatus(task.status) == TaskStatus.CANCELLED:
                self._mark_execution_cancelled(execution)
                return task

            TaskRepository.append_event(
                session,
                task,
                EventType.VALIDATION_SUCCEEDED,
                {"execution_id": execution_id},
            )
            ensure_transition(task.status, TaskStatus.COMPLETED)
            task.status = TaskStatus.COMPLETED.value
            task.claimed_by = None
            task.lease_expires_at = None
            execution.status = ExecutionStatus.SUCCEEDED.value
            execution.completed_at = datetime.now(UTC)
            TaskRepository.append_event(
                session,
                task,
                EventType.TASK_COMPLETED,
                {"execution_id": execution_id},
            )
            return task

    def fail_task(
        self,
        task_id: str,
        *,
        error: str,
        execution_id: str | None = None,
    ) -> None:
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            if execution_id:
                execution = self._require_execution(session, execution_id)
                execution.status = ExecutionStatus.FAILED.value
                execution.error = error
                execution.completed_at = datetime.now(UTC)
            if TaskStatus(task.status) in TERMINAL_STATUSES:
                return
            ensure_transition(task.status, TaskStatus.FAILED)
            task.status = TaskStatus.FAILED.value
            task.claimed_by = None
            task.lease_expires_at = None
            TaskRepository.append_event(
                session,
                task,
                EventType.TASK_FAILED,
                {"execution_id": execution_id, "error": error},
            )

    def cancel_task(self, task_id: str) -> TaskModel:
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            current_status = TaskStatus(task.status)
            if current_status in TERMINAL_STATUSES:
                return task
            ensure_transition(current_status, TaskStatus.CANCELLED)
            task.cancel_requested = True
            task.status = TaskStatus.CANCELLED.value
            task.claimed_by = None
            task.lease_expires_at = None
            TaskRepository.append_event(
                session,
                task,
                EventType.TASK_CANCELLED,
                {"previous_status": current_status.value},
            )
            return task

    def finish_cancelled_execution(self, execution_id: str) -> None:
        with self.database.session() as session, session.begin():
            execution = self._require_execution(session, execution_id)
            self._mark_execution_cancelled(execution)

    def is_cancelled(self, task_id: str) -> bool:
        with self.database.session() as session:
            task = TaskRepository.get(session, task_id)
            return (
                task is None or task.cancel_requested or task.status == TaskStatus.CANCELLED.value
            )

    def add_user_message(self, task_id: str, message: str) -> None:
        normalized_message = message.strip()
        if not normalized_message:
            raise ValueError("Message cannot be empty")
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            TaskRepository.append_event(
                session,
                task,
                EventType.USER_MESSAGE_RECEIVED,
                {"message": normalized_message},
            )

    @staticmethod
    def _require_task(session, task_id: str, *, for_update: bool = False) -> TaskModel:
        task = TaskRepository.get(session, task_id, for_update=for_update)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    @staticmethod
    def _require_execution(session, execution_id: str) -> ExecutionModel:
        execution = session.scalar(
            select(ExecutionModel).where(ExecutionModel.id == execution_id).with_for_update()
        )
        if execution is None:
            raise ExecutionNotFoundError(execution_id)
        return execution

    @staticmethod
    def _mark_execution_cancelled(execution: ExecutionModel) -> None:
        execution.status = ExecutionStatus.CANCELLED.value
        execution.completed_at = datetime.now(UTC)
