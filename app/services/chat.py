"""Chat sessions and their messages, and the tasks they launch."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select, update

from app.chat.blocks import validate_blocks
from app.domain.enums import EventType, TaskStatus
from app.domain.proposals import propose_task
from app.domain.task_context import build_task_context, render_task_context
from app.execution.base import ConversationTurn
from app.persistence.models import (
    ChatMessageModel,
    ChatSessionModel,
    ChatWorkItemModel,
    MemoryModel,
    ScheduleModel,
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

#: How much of a chat each reply sees: the last messages, each cut to this length.
HISTORY_MESSAGES = 24
HISTORY_MESSAGE_CHARACTERS = 1_500

#: Agents that never get context the user attached from other apps (it is untrusted).
UNTRUSTED_CONTEXT_EXCLUDED = {
    "supervision",
    "coding",
    "pull_request_creation",
    "repository_analysis",
    "shell_execution",
}


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

    def list_chat_sessions(
        self, *, limit: int = 100, archived: bool = False
    ) -> list[ChatSessionModel]:
        with self.database.session() as session:
            return ChatSessionRepository.list(session, limit=limit, archived=archived)

    def update_chat_session(
        self, chat_session_id: str, *, pinned: bool | None = None, archived: bool | None = None
    ) -> ChatSessionModel:
        """Pin or archive a chat; neither changes its place in time (updated_at)."""
        changes: dict[str, Any] = {}
        if pinned is not None:
            changes["pinned"] = pinned
        if archived is not None:
            changes["archived"] = archived
            if archived:
                changes["pinned"] = False
        with self.database.session() as session, session.begin():
            if ChatSessionRepository.get(session, chat_session_id) is None:
                raise ChatSessionNotFoundError(chat_session_id)
            if changes:
                # Keeping updated_at as it is stops the column's onupdate from bumping it.
                session.execute(
                    update(ChatSessionModel)
                    .where(ChatSessionModel.id == chat_session_id)
                    .values(**changes, updated_at=ChatSessionModel.updated_at)
                )
        return self.get_chat_session(chat_session_id)

    def delete_chat_session(self, chat_session_id: str) -> None:
        """Delete a chat and its messages for good. What it started (tasks, workflow runs,
        routines, memories) is kept, no longer linked to a chat."""
        with self.database.session() as session, session.begin():
            chat_session = ChatSessionRepository.get(session, chat_session_id)
            if chat_session is None:
                raise ChatSessionNotFoundError(chat_session_id)
            for model in (TaskModel, WorkflowRunModel, MemoryModel, ScheduleModel):
                column = (
                    model.source_chat_session_id if model is MemoryModel else model.chat_session_id
                )
                session.execute(
                    update(model).where(column == chat_session_id).values({column.key: None})
                )
            session.execute(
                delete(ChatWorkItemModel).where(
                    ChatWorkItemModel.chat_session_id == chat_session_id
                )
            )
            session.execute(
                delete(ChatMessageModel).where(ChatMessageModel.chat_session_id == chat_session_id)
            )
            session.delete(chat_session)

    def chat_repositories(self, chat_session_ids: list[str]) -> dict[str, str]:
        """The repository each chat's latest coding work was in, for grouping the list."""
        if not chat_session_ids:
            return {}
        found: dict[str, tuple[Any, str]] = {}
        with self.database.session() as session:
            rows = session.execute(
                select(
                    TaskModel.chat_session_id, TaskModel.source_context, TaskModel.created_at
                ).where(TaskModel.chat_session_id.in_(chat_session_ids))
            ).all()
            rows += session.execute(
                select(
                    WorkflowRunModel.chat_session_id,
                    WorkflowRunModel.workflow_input,
                    WorkflowRunModel.created_at,
                ).where(WorkflowRunModel.chat_session_id.in_(chat_session_ids))
            ).all()
        for chat_id, context, created_at in rows:
            repository = (context or {}).get("repository_id")
            if repository and (chat_id not in found or created_at > found[chat_id][0]):
                found[chat_id] = (created_at, str(repository))
        return {chat_id: repository for chat_id, (_, repository) in found.items()}

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
            if chat_session is None:
                raise ChatSessionNotFoundError(chat_session_id)
            if role == "user":
                chat_session.archived = False  # writing in an archived chat brings it back
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
            attachments = [
                {key: block.get(key) for key in ("kind", "app", "text")}
                for block in message.blocks or []
                if block.get("type") == "attachment"
            ]

        if attachments and not UNTRUSTED_CONTEXT_EXCLUDED & set(required_capabilities or []):
            # Screen text from another app: for agents that only answer, never for agents
            # that change code or steer runs.
            source_context = {**(source_context or {}), "attachments": attachments}
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
        """Earlier turns of the task's conversation, oldest first.

        In a chat they come from the transcript itself, so everything said there counts:
        quick actions, uploads, offers and choices, and turns whose run failed. Phone-only
        conversations (no chat) fall back to their completed runs.
        """
        conversation_id = (task.source_context or {}).get("conversation_id")
        if task.chat_session_id:
            return self._transcript_turns(task)
        if not conversation_id:
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

    def _transcript_turns(self, task: TaskModel) -> list[ConversationTurn]:
        """The chat's recent messages before this task, grouped as user turn + replies."""
        with self.database.session() as session:
            statement = (
                select(ChatMessageModel)
                .where(ChatMessageModel.chat_session_id == task.chat_session_id)
                .where(ChatMessageModel.created_at <= task.created_at)
                .order_by(ChatMessageModel.created_at.desc(), ChatMessageModel.id.desc())
                .limit(HISTORY_MESSAGES)
            )
            if task.origin_message_id:
                statement = statement.where(ChatMessageModel.id != task.origin_message_id)
            messages = list(reversed(session.scalars(statement).all()))
            turns: list[list[Any]] = []  # [request, replies, linked task]
            for message in messages:
                text = message.content[:HISTORY_MESSAGE_CHARACTERS]
                if message.role == "user":
                    turns.append([text, [], message.linked_task_id])
                elif turns:
                    turns[-1][1].append(text)
                # An assistant message before the first user message in the window has
                # lost its question; it is left out rather than shown out of context.
            result = []
            for request, replies, linked_task_id in turns:
                outcome = None
                if linked_task_id:
                    notes = session.scalars(
                        select(TaskEventModel.payload)
                        .where(TaskEventModel.task_id == linked_task_id)
                        .where(TaskEventModel.event_type == EventType.USER_MESSAGE_RECEIVED.value)
                        .order_by(TaskEventModel.sequence.desc())
                    ).all()
                    outcome = next(
                        (t for n in notes if (t := self._trusted_action_outcome(n)) is not None),
                        None,
                    )
                result.append(
                    ConversationTurn(request, "\n".join(replies) or "(no reply)", outcome)
                )
            return result

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
