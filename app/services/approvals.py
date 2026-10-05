"""Pull-request approvals: review gates, merges, readiness and revisions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.domain.enums import EventType, ExecutionStatus, TaskStatus
from app.domain.transitions import ensure_transition
from app.persistence.models import (
    TaskEventModel,
    TaskModel,
    WorkflowRunEventModel,
    WorkflowRunModel,
    utc_now,
)
from app.persistence.repository import TaskRepository
from app.services.base import ServiceBase
from app.services.common import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ExecutionNotFoundError,
    _reply_payload,
)
from app.workflows.models import WorkflowRunStatus


class ApprovalOperations(ServiceBase):
    """Pull-request approvals: review gates, merges, readiness and revisions."""

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
            self._report_pull_request_ready(session, task, approval)
            task.status = TaskStatus.WAITING_FOR_APPROVAL.value
            task.claimed_by = None
            task.lease_expires_at = None
            return task

    def _report_pull_request_ready(
        self, session, task: TaskModel, approval: dict[str, Any]
    ) -> None:
        """Tell the chat that started the run that its pull request is ready to review."""
        number = approval.get("number")
        if not task.chat_session_id or not number:
            return
        blocks: list[dict[str, Any]] = [
            {
                "type": "link",
                "label": "Review and merge",
                "href": f"#/chats/{task.chat_session_id}?task={task.id}",
            }
        ]
        url = str(approval.get("url") or "")
        if url.startswith("https://"):
            blocks.append({"type": "link", "label": f"PR #{number} on GitHub", "href": url})
        self._record_assistant_reply(
            session,
            task,
            {
                **_reply_payload(
                    f"Draft PR #{number} is ready for your review: {task.current_goal[:200]}",
                    emotion="Warm",
                    intensity=0.7,
                    outcome="waiting_for_approval",
                ),
                "blocks": blocks,
            },
        )

    def get_pending_approval(self, task_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            task = self._require_task(session, task_id)
            if TaskStatus(task.status) != TaskStatus.WAITING_FOR_APPROVAL:
                raise ApprovalNotFoundError(task_id)
            approval = session.scalar(
                select(TaskEventModel)
                .where(TaskEventModel.task_id == task.id)
                .where(TaskEventModel.event_type == EventType.APPROVAL_REQUESTED.value)
                .order_by(TaskEventModel.sequence.desc())
                .limit(1)
            )
            if approval is None:
                raise ApprovalNotFoundError(task_id)
            return dict(approval.payload)

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

    def record_pull_request_ready_call(
        self,
        task_id: str,
        *,
        repository: str,
        number: int,
        expected_head_sha: str,
    ) -> None:
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            if TaskStatus(task.status) != TaskStatus.WAITING_FOR_APPROVAL:
                raise ApprovalConflictError("Task is not waiting for pull request review")
            TaskRepository.append_event(
                session,
                task,
                EventType.TOOL_CALLED,
                {
                    "tool": "github",
                    "operation": "mark_pull_request_ready_for_review",
                    "repository": repository,
                    "number": number,
                    "expected_head_sha": expected_head_sha,
                },
            )

    def record_pull_request_ready_result(
        self,
        task_id: str,
        *,
        ok: bool,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            if TaskStatus(task.status) != TaskStatus.WAITING_FOR_APPROVAL:
                return None
            TaskRepository.append_event(
                session,
                task,
                EventType.TOOL_RESULT_RECEIVED,
                {
                    "tool": "github",
                    "operation": "mark_pull_request_ready_for_review",
                    "ok": ok,
                    **({"reason": reason} if reason else {}),
                },
            )
            if not ok:
                return None
            previous = session.scalar(
                select(TaskEventModel)
                .where(TaskEventModel.task_id == task.id)
                .where(TaskEventModel.event_type == EventType.APPROVAL_REQUESTED.value)
                .order_by(TaskEventModel.sequence.desc())
                .limit(1)
            )
            if previous is None:
                raise ApprovalNotFoundError(task_id)
            refreshed = dict(previous.payload)
            refreshed["draft"] = False
            refreshed["reason"] = "Pull request is ready for human review"
            TaskRepository.append_event(
                session,
                task,
                EventType.APPROVAL_REQUESTED,
                refreshed,
            )
            return refreshed

    def record_pull_request_status(
        self, task_id: str, *, operation: str, snapshot: dict[str, Any]
    ) -> None:
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            TaskRepository.append_event(
                session,
                task,
                EventType.TOOL_RESULT_RECEIVED,
                {
                    "tool": "github",
                    "operation": operation,
                    "ok": snapshot.get("head_matches") is True
                    and snapshot.get("required_checks_state") == "passed",
                    "status": snapshot,
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
            self._record_assistant_reply(
                session,
                task,
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
            self._record_assistant_reply(
                session,
                task,
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

    def supersede_pull_request_approval(self, task_id: str, *, revision_task_id: str) -> TaskModel:
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            revision = self._require_task(session, revision_task_id)
            if revision.parent_task_id != task.id:
                raise ApprovalConflictError("Revision task does not continue this task")
            if TaskStatus(task.status) != TaskStatus.WAITING_FOR_APPROVAL:
                raise ApprovalConflictError("Task is not waiting for approval")
            approval = session.scalar(
                select(TaskEventModel)
                .where(TaskEventModel.task_id == task.id)
                .where(TaskEventModel.event_type == EventType.APPROVAL_REQUESTED.value)
                .order_by(TaskEventModel.sequence.desc())
                .limit(1)
            )
            if approval is None:
                raise ApprovalNotFoundError(task_id)
            execution_id = approval.payload.get("execution_id")
            if not isinstance(execution_id, str):
                raise ApprovalNotFoundError(task_id)
            execution = self._require_execution(session, execution_id)
            ensure_transition(task.status, TaskStatus.SUPERSEDED)
            task.status = TaskStatus.SUPERSEDED.value
            task.superseded_by_task_id = revision.id
            task.claimed_by = None
            task.lease_expires_at = None
            self._mark_execution_cancelled(execution)
            TaskRepository.append_event(
                session,
                task,
                EventType.REVISION_REQUESTED,
                {
                    "revision_task_id": revision.id,
                    "previous_head_sha": approval.payload.get("expected_head_sha"),
                },
            )
            TaskRepository.append_event(
                session,
                task,
                EventType.TASK_SUPERSEDED,
                {"superseded_by_task_id": revision.id},
            )
            run = session.scalar(
                select(WorkflowRunModel)
                .where(WorkflowRunModel.task_id == task.id)
                .with_for_update()
            )
            if run is not None:
                run.task_id = revision.id
                run.status = WorkflowRunStatus.RUNNING.value
                run.current_stage = "implement"
                run.updated_at = utc_now()
                sequence = session.scalar(
                    select(func.max(WorkflowRunEventModel.sequence)).where(
                        WorkflowRunEventModel.workflow_run_id == run.id
                    )
                )
                session.add(
                    WorkflowRunEventModel(
                        workflow_run_id=run.id,
                        sequence=(sequence or 0) + 1,
                        event_type="WORKFLOW_REVISION_REQUESTED",
                        payload={
                            "previous_task_id": task.id,
                            "task_id": revision.id,
                            "stage": "implement",
                        },
                    )
                )
            return task
