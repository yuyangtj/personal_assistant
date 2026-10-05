"""Chat sessions and their messages, and the tasks they launch."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.chat.blocks import validate_blocks
from app.domain.enums import EventType, TaskStatus
from app.domain.proposals import propose_task
from app.domain.task_context import build_task_context, render_task_context
from app.execution.base import ConversationTurn
from app.persistence.models import (
    ChatMessageModel,
    ChatSessionModel,
    TaskEventModel,
    TaskModel,
    WorkflowRunModel,
)
from app.persistence.repository import ChatMessageRepository, ChatSessionRepository, TaskRepository
from app.services.base import ServiceBase
from app.services.common import (
    DEFAULT_CHAT_TITLE,
    MAX_HISTORY_TURNS,
    ChatMessageNotFoundError,
    ChatSessionNotFoundError,
    MessageHasWorkflowError,
    PostedMessage,
    _chat_title,
    _focus_mentions,
)


class ChatOperations(ServiceBase):
    """Chat sessions and their messages, and the tasks they launch."""

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
        blocks: list[dict[str, Any]] | None = None,
    ) -> PostedMessage:
        """Record a turn of conversation. No task is launched and nothing is executed.

        Returns the stored message and, for user turns that read as a request for work,
        a proposal the client can offer as "create a task?". The proposal is advisory:
        only :meth:`create_task_from_message` actually starts work. ``#slug`` mentions of
        existing work items focus the chat on them.
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
                blocks=validate_blocks(blocks) if blocks else None,
            )
            if role == "user" and chat_session.title == DEFAULT_CHAT_TITLE:
                chat_session.title = _chat_title(normalized_content)
            chat_session.updated_at = datetime.now(UTC)
            session.flush()
            focused = (
                _focus_mentions(session, chat_session.id, normalized_content)
                if role == "user"
                else []
            )
            proposal = propose_task(normalized_content) if role == "user" else None
            return PostedMessage(message=message, proposal=proposal, focused_work_items=focused)

    def open_upload(self, chat_session_id: str, message_id: str, block_id: str) -> dict[str, Any]:
        """The still-open upload block an assistant message in this chat offered."""
        with self.database.session() as session:
            message = session.get(ChatMessageModel, message_id)
            if (
                message is None
                or message.chat_session_id != chat_session_id
                or message.role != "assistant"
            ):
                raise ChatMessageNotFoundError(message_id)
            for block in message.blocks or []:
                if block.get("type") == "upload" and block.get("id") == block_id:
                    if block.get("state") != "open":
                        raise ValueError("This upload was already used; ask for a new one")
                    return dict(block)
            raise ChatMessageNotFoundError(block_id)

    def complete_upload(self, message_id: str, block_id: str, result: str) -> None:
        with self.database.session() as session, session.begin():
            message = session.get(ChatMessageModel, message_id, with_for_update=True)
            if message is None:
                raise ChatMessageNotFoundError(message_id)
            message.blocks = validate_blocks(
                [
                    {**block, "state": "done", "result": result[:300]}
                    if block.get("id") == block_id
                    else block
                    for block in message.blocks or []
                ]
            )

    def set_message_blocks(self, message_id: str, blocks: list[dict[str, Any]]) -> None:
        """Replace a message's blocks, e.g. to mark its choices answered."""
        with self.database.session() as session, session.begin():
            message = session.get(ChatMessageModel, message_id)
            if message is None:
                raise ChatMessageNotFoundError(message_id)
            message.blocks = validate_blocks(blocks) or None

    def create_task_from_message(
        self,
        chat_session_id: str,
        message_id: str,
        *,
        goal: str | None = None,
        required_capabilities: list[str] | None = None,
        source_context: dict[str, Any] | None = None,
        work_item_id: str | None = None,
        create_work_item: bool = False,
        space: str | None = None,
    ) -> TaskModel:
        """Launch work from a message the user already sent, on their explicit confirmation.

        Ordinary chat turns stay unlinked. With ``create_work_item`` (the client's "make this
        a task"), the run joins the chat's only focused work item or a new focused one.
        """
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
            workflow_run_id = session.scalar(
                select(WorkflowRunModel.id).where(WorkflowRunModel.origin_message_id == message_id)
            )
            if workflow_run_id is not None:
                raise MessageHasWorkflowError(
                    f"This message already proposed coding workflow {workflow_run_id}; "
                    "approve or start that workflow instead"
                )
            content = message.content

        repository_id = (source_context or {}).get("repository_id")
        return self.create_task(
            request=content,
            goal=goal,
            required_capabilities=required_capabilities,
            source_context=source_context,
            chat_session_id=chat_session_id,
            origin_message_id=message_id,
            work_item_id=work_item_id,
            work_item_space=(
                (space or ("coding" if repository_id else "general")) if create_work_item else None
            ),
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

    def routine_results(self, task: TaskModel, *, limit: int = 3) -> list[dict[str, str]]:
        """What earlier runs of this task's routine answered, newest first."""
        schedule_id = (task.source_context or {}).get("schedule_id")
        if not schedule_id:
            return []
        with self.database.session() as session:
            earlier = session.scalars(
                select(TaskModel)
                .where(TaskModel.status == TaskStatus.COMPLETED.value)
                .where(TaskModel.id != task.id)
                .where(TaskModel.source_context["schedule_id"].as_string() == str(schedule_id))
                .order_by(TaskModel.created_at.desc())
                .limit(limit)
            ).all()
            results = []
            for previous in earlier:
                reply = session.scalar(
                    select(TaskEventModel.payload)
                    .where(TaskEventModel.task_id == previous.id)
                    .where(TaskEventModel.event_type == EventType.ASSISTANT_REPLY.value)
                    .order_by(TaskEventModel.sequence.desc())
                    .limit(1)
                )
                if reply and reply.get("text"):
                    results.append(
                        {
                            "date": previous.created_at.date().isoformat(),
                            "result": str(reply["text"])[:1500],
                        }
                    )
            return results

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
