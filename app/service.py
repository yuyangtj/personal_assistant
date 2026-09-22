from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.capabilities.models import IDENTIFIER_PATTERN
from app.domain.enums import EventType, ExecutionStatus, TaskStatus
from app.domain.proposals import TaskProposal, propose_task
from app.domain.task_context import build_task_context, render_task_context
from app.domain.transitions import TERMINAL_STATUSES, ensure_transition
from app.execution.actions import validate_action
from app.execution.base import ConversationTurn
from app.persistence.database import Database
from app.persistence.models import (
    ChatMessageModel,
    ChatSessionModel,
    ExecutionModel,
    TaskEventModel,
    TaskModel,
)
from app.persistence.repository import ChatMessageRepository, ChatSessionRepository, TaskRepository


class TaskNotFoundError(LookupError):
    pass


class ChatSessionNotFoundError(LookupError):
    pass


class ChatMessageNotFoundError(LookupError):
    pass


MAX_REPLY_CHARACTERS = 4000
DEFAULT_FAILURE_REPLY = "Sorry, I couldn't finish that request."
CANCELLED_REPLY = "Okay, I've stopped working on that."
REPLY_EMOTIONS = {"Warm", "Curious", "Excited", "Concerned", "Neutral"}
MAX_HISTORY_TURNS = 6
DEFAULT_CHAT_TITLE = "New conversation"


def _reply_payload(text: str, *, emotion: str, intensity: float, outcome: str) -> dict[str, Any]:
    """User-facing speech for clients such as the Android avatar."""
    normalized = " ".join(text.split())[:MAX_REPLY_CHARACTERS]
    return {"text": normalized, "emotion": emotion, "intensity": intensity, "outcome": outcome}


def _chat_title(request: str) -> str:
    normalized = " ".join(request.split())
    return normalized if len(normalized) <= 80 else normalized[:77].rstrip() + "…"


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
        chat_session_id: str | None = None,
        origin_message_id: str | None = None,
        parent_task_id: str | None = None,
        external_source: str | None = None,
        external_key: str | None = None,
    ) -> TaskModel:
        normalized_request = request.strip()
        if not normalized_request:
            raise ValueError("Task request cannot be empty")
        if (external_source is None) != (external_key is None):
            raise ValueError("external_source and external_key must be supplied together")
        if origin_message_id is not None and chat_session_id is None:
            raise ValueError("origin_message_id requires a chat_session_id")
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

            chat_session = None
            origin_message = None
            normalized_context = dict(source_context or {})
            if chat_session_id is not None:
                chat_session = ChatSessionRepository.get(session, chat_session_id)
                if chat_session is None or chat_session.archived:
                    raise ChatSessionNotFoundError(chat_session_id)
                normalized_context["conversation_id"] = chat_session.id
            if origin_message_id is not None:
                origin_message = session.get(ChatMessageModel, origin_message_id)
                if origin_message is None or origin_message.chat_session_id != chat_session_id:
                    raise ChatMessageNotFoundError(origin_message_id)
                if origin_message.linked_task_id is not None:
                    # One message launches one task; a repeated confirmation is a no-op.
                    existing_task = TaskRepository.get(session, origin_message.linked_task_id)
                    if existing_task is not None:
                        return existing_task
            if parent_task_id is not None and TaskRepository.get(session, parent_task_id) is None:
                raise TaskNotFoundError(parent_task_id)

            task = TaskModel(
                id=str(uuid4()),
                original_request=normalized_request,
                current_goal=(goal or normalized_request).strip(),
                status=TaskStatus.CREATED.value,
                required_capabilities=normalized_capabilities,
                source_context=normalized_context,
                chat_session_id=chat_session_id,
                parent_task_id=parent_task_id,
                external_source=external_source,
                external_key=external_key,
            )
            if chat_session is not None:
                if chat_session.title == DEFAULT_CHAT_TITLE:
                    chat_session.title = _chat_title(normalized_request)
                chat_session.updated_at = datetime.now(UTC)
            session.add(task)
            session.flush()
            if origin_message is not None:
                origin_message.linked_task_id = task.id
                task.origin_message_id = origin_message.id
            elif chat_session is not None:
                origin = ChatMessageRepository.append(
                    session,
                    chat_session_id=chat_session.id,
                    role="user",
                    content=normalized_request,
                    linked_task_id=task.id,
                )
                task.origin_message_id = origin.id
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

    def create_chat_session(self, *, title: str | None = None) -> ChatSessionModel:
        normalized_title = (title or DEFAULT_CHAT_TITLE).strip()
        if not normalized_title:
            normalized_title = DEFAULT_CHAT_TITLE
        if len(normalized_title) > 160:
            raise ValueError("Chat session title cannot exceed 160 characters")
        with self.database.session() as session, session.begin():
            chat_session = ChatSessionModel(
                id=str(uuid4()),
                title=normalized_title,
                archived=False,
            )
            session.add(chat_session)
            session.flush()
            return chat_session

    def get_chat_session(self, chat_session_id: str) -> ChatSessionModel:
        with self.database.session() as session:
            chat_session = ChatSessionRepository.get(session, chat_session_id)
            if chat_session is None:
                raise ChatSessionNotFoundError(chat_session_id)
            return chat_session

    def list_chat_sessions(self, *, limit: int = 100) -> list[ChatSessionModel]:
        with self.database.session() as session:
            return ChatSessionRepository.list(session, limit=limit)

    def list_chat_session_tasks(
        self,
        chat_session_id: str,
        *,
        limit: int = 100,
    ) -> list[TaskModel]:
        with self.database.session() as session:
            if ChatSessionRepository.get(session, chat_session_id) is None:
                raise ChatSessionNotFoundError(chat_session_id)
            return TaskRepository.list(
                session,
                chat_session_id=chat_session_id,
                limit=limit,
            )

    def list_chat_messages(
        self,
        chat_session_id: str,
        *,
        limit: int = 500,
    ) -> list[ChatMessageModel]:
        with self.database.session() as session:
            if ChatSessionRepository.get(session, chat_session_id) is None:
                raise ChatSessionNotFoundError(chat_session_id)
            return ChatMessageRepository.list(session, chat_session_id, limit=limit)

    def append_chat_message(
        self,
        chat_session_id: str,
        *,
        content: str,
        role: str = "user",
    ) -> tuple[ChatMessageModel, TaskProposal | None]:
        """Record a turn of conversation. No task is launched and nothing is executed.

        Returns the stored message and, for user turns that read as a request for work,
        a proposal the client can offer as "create a task?". The proposal is advisory:
        only :meth:`create_task_from_message` actually starts work.
        """
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("Message content cannot be empty")
        if role not in {"user", "assistant", "system"}:
            raise ValueError(f"Unsupported chat message role: {role}")

        with self.database.session() as session, session.begin():
            chat_session = ChatSessionRepository.get(session, chat_session_id)
            if chat_session is None or chat_session.archived:
                raise ChatSessionNotFoundError(chat_session_id)
            message = ChatMessageRepository.append(
                session,
                chat_session_id=chat_session.id,
                role=role,
                content=normalized_content,
            )
            if role == "user" and chat_session.title == DEFAULT_CHAT_TITLE:
                chat_session.title = _chat_title(normalized_content)
            chat_session.updated_at = datetime.now(UTC)
            session.flush()
            proposal = propose_task(normalized_content) if role == "user" else None
            return message, proposal

    def create_task_from_message(
        self,
        chat_session_id: str,
        message_id: str,
        *,
        goal: str | None = None,
        required_capabilities: list[str] | None = None,
        source_context: dict[str, Any] | None = None,
    ) -> TaskModel:
        """Launch work from a message the user already sent, on their explicit confirmation."""
        with self.database.session() as session:
            if ChatSessionRepository.get(session, chat_session_id) is None:
                raise ChatSessionNotFoundError(chat_session_id)
            message = session.get(ChatMessageModel, message_id)
            if message is None or message.chat_session_id != chat_session_id:
                raise ChatMessageNotFoundError(message_id)
            if message.role != "user":
                raise ValueError("Only a user message can launch a task")
            if message.linked_task_id is not None:
                existing = TaskRepository.get(session, message.linked_task_id)
                if existing is not None:
                    return existing
            content = message.content

        return self.create_task(
            request=content,
            goal=goal,
            required_capabilities=required_capabilities,
            source_context=source_context,
            chat_session_id=chat_session_id,
            origin_message_id=message_id,
        )

    def get_user_chat_message(
        self,
        chat_session_id: str,
        message_id: str,
    ) -> ChatMessageModel:
        with self.database.session() as session:
            if ChatSessionRepository.get(session, chat_session_id) is None:
                raise ChatSessionNotFoundError(chat_session_id)
            message = session.get(ChatMessageModel, message_id)
            if message is None or message.chat_session_id != chat_session_id:
                raise ChatMessageNotFoundError(message_id)
            if message.role != "user":
                raise ValueError("Only a user message can propose a workflow")
            return message

    def task_context(self, task_id: str) -> dict[str, Any]:
        """The curated, whitelisted summary of a task — safe to put in a model prompt."""
        with self.database.session() as session:
            task = self._require_task(session, task_id)
            events = TaskRepository.list_events(session, task.id)
            return build_task_context(task, events)

    def create_follow_up_task(
        self,
        task_id: str,
        *,
        request: str,
        goal: str | None = None,
        required_capabilities: list[str] | None = None,
        source_context: dict[str, Any] | None = None,
    ) -> TaskModel:
        """A new task that continues an earlier one, carrying only its curated context."""
        with self.database.session() as session:
            parent = self._require_task(session, task_id)
            events = TaskRepository.list_events(session, parent.id)
            context = build_task_context(parent, events)
            chat_session_id = parent.chat_session_id
            parent_repository_id = (parent.source_context or {}).get("repository_id")

        next_source_context = {**(source_context or {}), "parent_task": context}
        if parent_repository_id and "repository_id" not in next_source_context:
            next_source_context["repository_id"] = parent_repository_id

        return self.create_task(
            request=request,
            goal=goal,
            required_capabilities=required_capabilities,
            source_context=next_source_context,
            chat_session_id=chat_session_id,
            parent_task_id=task_id,
        )

    def ensure_task_chat_session(self, task_id: str) -> ChatSessionModel:
        """Open the task's conversation for discussion, backfilling one for older tasks.

        Entering from Task Details posts a reference to the task — its status and curated
        result — so the next turn has the context without copying the event stream in.
        """
        with self.database.session() as session, session.begin():
            task = self._require_task(session, task_id, for_update=True)
            if task.chat_session_id:
                existing = ChatSessionRepository.get(session, task.chat_session_id)
                if existing is not None:
                    self._reference_task(session, existing, task)
                    return existing

            chat_session = ChatSessionModel(
                id=str(uuid4()),
                title=_chat_title(task.original_request),
                archived=False,
            )
            session.add(chat_session)
            session.flush()
            task.chat_session_id = chat_session.id
            context = dict(task.source_context or {})
            context["conversation_id"] = chat_session.id
            task.source_context = context
            origin = ChatMessageRepository.append(
                session,
                chat_session_id=chat_session.id,
                role="user",
                content=task.original_request,
                linked_task_id=task.id,
            )
            task.origin_message_id = origin.id
            reply = session.scalar(
                select(TaskEventModel.payload)
                .where(TaskEventModel.task_id == task.id)
                .where(TaskEventModel.event_type == EventType.ASSISTANT_REPLY.value)
                .order_by(TaskEventModel.sequence.desc())
                .limit(1)
            )
            if reply and reply.get("text"):
                ChatMessageRepository.append(
                    session,
                    chat_session_id=chat_session.id,
                    role="assistant",
                    content=str(reply["text"]),
                    linked_task_id=task.id,
                )
            self._reference_task(session, chat_session, task)
            return chat_session

    @staticmethod
    def _reference_task(
        session,
        chat_session: ChatSessionModel,
        task: TaskModel,
    ) -> None:
        """Post the task's curated snapshot, unless the transcript already ends with it."""
        context = build_task_context(task, TaskRepository.list_events(session, task.id))
        content = f"Discussing task: {render_task_context(context)}"
        latest = ChatMessageRepository.latest(session, chat_session.id)
        if latest is not None and latest.role == "system" and latest.content == content:
            return
        ChatMessageRepository.append(
            session,
            chat_session_id=chat_session.id,
            role="system",
            content=content,
            linked_task_id=task.id,
        )
        chat_session.updated_at = datetime.now(UTC)

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
        """Earlier completed turns sharing the task's persistent chat session."""
        conversation_id = (task.source_context or {}).get("conversation_id")
        if not task.chat_session_id and not conversation_id:
            return []
        with self.database.session() as session:
            statement = (
                select(TaskModel)
                .where(TaskModel.status == TaskStatus.COMPLETED.value)
                .where(TaskModel.created_at <= task.created_at)
                .where(TaskModel.id != task.id)
                .order_by(TaskModel.created_at.desc())
                .limit(limit)
            )
            if task.chat_session_id:
                statement = statement.where(TaskModel.chat_session_id == task.chat_session_id)
            else:
                statement = statement.where(
                    TaskModel.source_context["conversation_id"].as_string() == str(conversation_id)
                )
            recent = session.scalars(statement).all()
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
                        .where(TaskEventModel.event_type == EventType.USER_MESSAGE_RECEIVED.value)
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
    def _record_assistant_reply(
        session,
        task: TaskModel,
        payload: dict[str, Any],
    ) -> None:
        TaskRepository.append_event(session, task, EventType.ASSISTANT_REPLY, payload)
        text = str(payload.get("text", "")).strip()
        if not task.chat_session_id or not text:
            return
        ChatMessageRepository.append(
            session,
            chat_session_id=task.chat_session_id,
            role="assistant",
            content=text,
            linked_task_id=task.id,
        )
        chat_session = ChatSessionRepository.get(session, task.chat_session_id)
        if chat_session is not None:
            chat_session.updated_at = datetime.now(UTC)

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
