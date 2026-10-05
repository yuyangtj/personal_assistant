"""Claiming, running and finishing tasks: the worker's side."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.domain.enums import EventType, ExecutionStatus, TaskStatus
from app.domain.transitions import TERMINAL_STATUSES, ensure_transition
from app.execution.actions import validate_action
from app.persistence.models import ExecutionModel, TaskModel
from app.persistence.repository import TaskRepository
from app.services.base import ServiceBase
from app.services.common import (
    CANCELLED_REPLY,
    DEFAULT_FAILURE_REPLY,
    MALFORMED_REPOSITORY_REPLY,
    REPLY_EMOTIONS,
    _reply_payload,
)


class ExecutionOperations(ServiceBase):
    """Claiming, running and finishing tasks: the worker's side."""

    def claim_next_task(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        supports_coding: bool = True,
        coding_only: bool = False,
    ) -> TaskModel | None:
        with self.database.session() as session, session.begin():
            # Such tasks were skipped on every poll and stayed queued forever.
            for malformed in TaskRepository.malformed_queued(session):
                ensure_transition(malformed.status, TaskStatus.FAILED)
                self._record_assistant_reply(
                    session,
                    malformed,
                    _reply_payload(
                        MALFORMED_REPOSITORY_REPLY,
                        emotion="Concerned",
                        intensity=0.6,
                        outcome="failed",
                    ),
                )
                malformed.status = TaskStatus.FAILED.value
                TaskRepository.append_event(
                    session,
                    malformed,
                    EventType.TASK_FAILED,
                    {"error": "Task names a repository but requires no coding capability"},
                )
            return TaskRepository.claim_next(
                session,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                supports_coding=supports_coding,
                coding_only=coding_only,
            )

    def recover_expired_tasks(self) -> list[str]:
        with self.database.session() as session, session.begin():
            return TaskRepository.recover_expired(session, now=datetime.now(UTC))

    def renew_lease(self, task_id: str, *, worker_id: str, lease_seconds: int) -> bool:
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            if task.claimed_by != worker_id or TaskStatus(task.status) in TERMINAL_STATUSES:
                return False
            task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
            task.updated_at = datetime.now(UTC)
            return True

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

    def complete_task(
        self,
        task_id: str,
        execution_id: str,
        *,
        reply: str | None = None,
        emotion: str = "Warm",
        action: dict[str, Any] | None = None,
        blocks: list[dict[str, Any]] | None = None,
    ) -> TaskModel:
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
            if reply and reply.strip():
                self._record_assistant_reply(
                    session,
                    task,
                    {
                        **_reply_payload(
                            reply,
                            emotion=emotion if emotion in REPLY_EMOTIONS else "Warm",
                            intensity=0.7,
                            outcome="completed",
                        ),
                        # A proposed phone action; clients execute it only after confirmation.
                        "action": validate_action(action),
                        **({"blocks": blocks} if blocks else {}),
                    },
                )
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
        reply: str = DEFAULT_FAILURE_REPLY,
    ) -> None:
        """Fail a task. ``error`` is internal; ``reply`` is the user-facing sentence."""
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
            self._record_assistant_reply(
                session,
                task,
                _reply_payload(reply, emotion="Concerned", intensity=0.6, outcome="failed"),
            )
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
            self._record_assistant_reply(
                session,
                task,
                _reply_payload(
                    CANCELLED_REPLY, emotion="Neutral", intensity=0.5, outcome="cancelled"
                ),
            )
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
