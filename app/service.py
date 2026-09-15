from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.capabilities.models import IDENTIFIER_PATTERN
from app.domain.enums import EventType, ExecutionStatus, TaskStatus
from app.domain.transitions import TERMINAL_STATUSES, ensure_transition
from app.execution.actions import validate_action
from app.execution.base import ConversationTurn
from app.persistence.database import Database
from app.persistence.models import ExecutionModel, TaskEventModel, TaskModel
from app.persistence.repository import TaskRepository


class TaskNotFoundError(LookupError):
    pass


MAX_REPLY_CHARACTERS = 4000
DEFAULT_FAILURE_REPLY = "Sorry, I couldn't finish that request."
CANCELLED_REPLY = "Okay, I've stopped working on that."
REPLY_EMOTIONS = {"Warm", "Curious", "Excited", "Concerned", "Neutral"}
MAX_HISTORY_TURNS = 6


def _reply_payload(text: str, *, emotion: str, intensity: float, outcome: str) -> dict[str, Any]:
    """User-facing speech for clients such as the Android avatar."""
    normalized = " ".join(text.split())[:MAX_REPLY_CHARACTERS]
    return {"text": normalized, "emotion": emotion, "intensity": intensity, "outcome": outcome}


class ExecutionNotFoundError(LookupError):
    pass


class ApprovalNotFoundError(LookupError):
    pass


class ApprovalConflictError(ValueError):
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

    def conversation_history(
        self,
        task: TaskModel,
        *,
        limit: int = MAX_HISTORY_TURNS,
    ) -> list[ConversationTurn]:
        """Earlier completed turns sharing the task's `source_context.conversation_id`."""
        conversation_id = (task.source_context or {}).get("conversation_id")
        if not conversation_id:
            return []
        with self.database.session() as session:
            recent = session.scalars(
                select(TaskModel)
                .where(TaskModel.status == TaskStatus.COMPLETED.value)
                .where(TaskModel.created_at <= task.created_at)
                .where(TaskModel.id != task.id)
                .where(
                    TaskModel.source_context["conversation_id"].as_string()
                    == str(conversation_id)
                )
                .order_by(TaskModel.created_at.desc())
                .limit(limit)
            ).all()
            turns: list[ConversationTurn] = []
            for previous in recent:
                reply = session.scalar(
                    select(TaskEventModel.payload)
                    .where(TaskEventModel.task_id == previous.id)
                    .where(TaskEventModel.event_type == EventType.ASSISTANT_REPLY.value)
                    .order_by(TaskEventModel.sequence.desc())
                    .limit(1)
                )
                if reply and reply.get("text"):
                    action_notes = session.scalars(
                        select(TaskEventModel.payload)
                        .where(TaskEventModel.task_id == previous.id)
                        .where(
                            TaskEventModel.event_type
                            == EventType.USER_MESSAGE_RECEIVED.value
                        )
                        .order_by(TaskEventModel.sequence.desc())
                    ).all()
                    outcome = next(
                        (
                            trusted
                            for note in action_notes
                            if (trusted := self._trusted_action_outcome(note)) is not None
                        ),
                        None,
                    )
                    turns.append(
                        ConversationTurn(
                            previous.original_request,
                            reply["text"],
                            outcome,
                        )
                    )
            return list(reversed(turns))

    @staticmethod
    def _trusted_action_outcome(payload: dict[str, Any] | None) -> str | None:
        """Maps native action notes to fixed text; raw client messages never become prompts."""
        message = payload.get("message", "") if payload else ""
        if message.startswith("Phone action confirmed and started:"):
            return "The user confirmed and started the previous phone action."
        if message.startswith("Phone action dismissed:"):
            return "The user dismissed the previous phone action."
        if message.startswith("Phone action failed:"):
            return "The previous phone action could not be started."
        return None

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

    def request_approval(
        self,
        task_id: str,
        execution_id: str,
        *,
        artifacts: list[dict[str, Any]],
        approval: dict[str, Any],
    ) -> TaskModel:
        """Persists review artifacts and pauses the task before a destructive action."""
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            execution = self._require_execution(session, execution_id)
            if execution.task_id != task.id:
                raise ExecutionNotFoundError(execution_id)
            ensure_transition(task.status, TaskStatus.WAITING_FOR_APPROVAL)
            for artifact in artifacts:
                TaskRepository.append_event(session, task, EventType.ARTIFACT_CREATED, artifact)
            TaskRepository.append_event(
                session,
                task,
                EventType.APPROVAL_REQUESTED,
                {**approval, "execution_id": execution_id},
            )
            task.status = TaskStatus.WAITING_FOR_APPROVAL.value
            task.claimed_by = None
            task.lease_expires_at = None
            return task

    def begin_pull_request_approval(
        self,
        task_id: str,
        *,
        expected_head_sha: str,
    ) -> dict[str, Any]:
        """Atomically claims a pending PR merge approval and returns its trusted payload."""
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            if TaskStatus(task.status) != TaskStatus.WAITING_FOR_APPROVAL:
                raise ApprovalConflictError("Task is not waiting for approval")
            approval = session.scalar(
                select(TaskEventModel)
                .where(TaskEventModel.task_id == task.id)
                .where(TaskEventModel.event_type == EventType.APPROVAL_REQUESTED.value)
                .order_by(TaskEventModel.sequence.desc())
                .limit(1)
            )
            if approval is None or approval.payload.get("type") != "github_pull_request_merge":
                raise ApprovalNotFoundError(task_id)
            if approval.payload.get("expected_head_sha") != expected_head_sha:
                raise ApprovalConflictError("Reviewed head SHA does not match the pending approval")
            ensure_transition(task.status, TaskStatus.EXECUTING)
            task.status = TaskStatus.EXECUTING.value
            task.claimed_by = "approval-api"
            TaskRepository.append_event(
                session,
                task,
                EventType.APPROVAL_GRANTED,
                {
                    "type": "github_pull_request_merge",
                    "expected_head_sha": expected_head_sha,
                },
            )
            return dict(approval.payload)

    def return_to_pull_request_approval(
        self,
        task_id: str,
        *,
        reason: str,
        replacement_head_sha: str | None = None,
    ) -> None:
        """Re-opens the gate after a safe preflight or GitHub failure."""
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            if TaskStatus(task.status) != TaskStatus.EXECUTING:
                return
            ensure_transition(task.status, TaskStatus.WAITING)
            task.status = TaskStatus.WAITING.value
            ensure_transition(task.status, TaskStatus.WAITING_FOR_APPROVAL)
            task.status = TaskStatus.WAITING_FOR_APPROVAL.value
            task.claimed_by = None
            task.lease_expires_at = None
            TaskRepository.append_event(
                session,
                task,
                EventType.TOOL_RESULT_RECEIVED,
                {
                    "tool": "github",
                    "operation": "merge_pull_request",
                    "ok": False,
                    "reason": reason,
                },
            )
            if replacement_head_sha:
                previous = session.scalar(
                    select(TaskEventModel)
                    .where(TaskEventModel.task_id == task.id)
                    .where(TaskEventModel.event_type == EventType.APPROVAL_REQUESTED.value)
                    .order_by(TaskEventModel.sequence.desc())
                    .limit(1)
                )
                if previous is not None:
                    refreshed = dict(previous.payload)
                    refreshed["expected_head_sha"] = replacement_head_sha
                    refreshed["reason"] = (
                        "Pull request head changed; review the new commit before approval"
                    )
                    TaskRepository.append_event(
                        session,
                        task,
                        EventType.APPROVAL_REQUESTED,
                        refreshed,
                    )

    def record_pull_request_merge_call(self, task_id: str, *, repository: str, number: int) -> None:
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            if TaskStatus(task.status) != TaskStatus.EXECUTING:
                raise ApprovalConflictError("Approval is no longer active")
            TaskRepository.append_event(
                session,
                task,
                EventType.TOOL_CALLED,
                {
                    "tool": "github",
                    "operation": "merge_pull_request",
                    "repository": repository,
                    "number": number,
                },
            )

    def complete_pull_request_merge(
        self,
        task_id: str,
        *,
        execution_id: str,
        repository: str,
        number: int,
        url: str,
        merge_sha: str | None,
    ) -> TaskModel:
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            execution = self._require_execution(session, execution_id)
            if execution.task_id != task.id:
                raise ExecutionNotFoundError(execution_id)
            if TaskStatus(task.status) != TaskStatus.EXECUTING:
                raise ApprovalConflictError("Approval is no longer active")
            TaskRepository.append_event(
                session,
                task,
                EventType.TOOL_RESULT_RECEIVED,
                {
                    "tool": "github",
                    "operation": "merge_pull_request",
                    "ok": True,
                    "merge_sha": merge_sha,
                },
            )
            TaskRepository.append_event(
                session,
                task,
                EventType.ARTIFACT_CREATED,
                {
                    "type": "github_pull_request_merge",
                    "repository": repository,
                    "number": number,
                    "url": url,
                    "merge_sha": merge_sha,
                },
            )
            ensure_transition(task.status, TaskStatus.VALIDATING)
            task.status = TaskStatus.VALIDATING.value
            TaskRepository.append_event(
                session,
                task,
                EventType.VALIDATION_SUCCEEDED,
                {"execution_id": execution_id, "approved_action": "github_pull_request_merge"},
            )
            ensure_transition(task.status, TaskStatus.COMPLETED)
            TaskRepository.append_event(
                session,
                task,
                EventType.ASSISTANT_REPLY,
                _reply_payload(
                    f"Pull request #{number} was merged successfully.",
                    emotion="Warm",
                    intensity=0.7,
                    outcome="completed",
                ),
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

    def reject_approval(self, task_id: str) -> TaskModel:
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            if TaskStatus(task.status) != TaskStatus.WAITING_FOR_APPROVAL:
                raise ApprovalConflictError("Task is not waiting for approval")
            approval = session.scalar(
                select(TaskEventModel)
                .where(TaskEventModel.task_id == task.id)
                .where(TaskEventModel.event_type == EventType.APPROVAL_REQUESTED.value)
                .order_by(TaskEventModel.sequence.desc())
                .limit(1)
            )
            if approval is None or approval.payload.get("type") != "github_pull_request_merge":
                raise ApprovalNotFoundError(task_id)
            execution_id = approval.payload.get("execution_id")
            if not isinstance(execution_id, str):
                raise ApprovalNotFoundError(task_id)
            execution = self._require_execution(session, execution_id)
            if execution.task_id != task.id:
                raise ExecutionNotFoundError(execution_id)
            TaskRepository.append_event(
                session,
                task,
                EventType.APPROVAL_REJECTED,
                {
                    "type": "github_pull_request_merge",
                    "execution_id": execution_id,
                },
            )
            ensure_transition(task.status, TaskStatus.CANCELLED)
            TaskRepository.append_event(
                session,
                task,
                EventType.ASSISTANT_REPLY,
                _reply_payload(
                    "Okay, I left the pull request unmerged.",
                    emotion="Neutral",
                    intensity=0.5,
                    outcome="cancelled",
                ),
            )
            task.cancel_requested = True
            task.status = TaskStatus.CANCELLED.value
            task.claimed_by = None
            task.lease_expires_at = None
            self._mark_execution_cancelled(execution)
            TaskRepository.append_event(
                session,
                task,
                EventType.TASK_CANCELLED,
                {"previous_status": TaskStatus.WAITING_FOR_APPROVAL.value},
            )
            return task

    def complete_task(
        self,
        task_id: str,
        execution_id: str,
        *,
        reply: str | None = None,
        emotion: str = "Warm",
        action: dict[str, Any] | None = None,
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
                TaskRepository.append_event(
                    session,
                    task,
                    EventType.ASSISTANT_REPLY,
                    {
                        **_reply_payload(
                            reply,
                            emotion=emotion if emotion in REPLY_EMOTIONS else "Warm",
                            intensity=0.7,
                            outcome="completed",
                        ),
                        # A proposed phone action; clients execute it only after confirmation.
                        "action": validate_action(action),
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
            TaskRepository.append_event(
                session,
                task,
                EventType.ASSISTANT_REPLY,
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
            TaskRepository.append_event(
                session,
                task,
                EventType.ASSISTANT_REPLY,
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
