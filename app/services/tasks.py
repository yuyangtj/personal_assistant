"""Creating tasks and reading them back, with their curated context."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.capabilities.models import IDENTIFIER_PATTERN
from app.domain.enums import CODING_CAPABILITIES, EventType, TaskStatus
from app.domain.task_context import build_task_context
from app.domain.work_items import WorkItemLink
from app.persistence.models import ChatMessageModel, CodingRunModel, TaskEventModel, TaskModel
from app.persistence.repository import ChatMessageRepository, ChatSessionRepository, TaskRepository
from app.services.base import ServiceBase
from app.services.common import (
    DEFAULT_CHAT_TITLE,
    ChatMessageNotFoundError,
    ChatSessionNotFoundError,
    TaskNotFoundError,
    _chat_title,
    _focus_mentions,
)
from app.work.items import get_work_item, item_title, link_task, resolve_for_run, work_item_context


class TaskOperations(ServiceBase):
    """Creating tasks and reading them back, with their curated context."""

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
        work_item_id: str | None = None,
        work_item_space: str | None = None,
    ) -> TaskModel:
        """Create a run. ``work_item_space`` joins it to the chat's only focused work item,
        or to a new focused one in that space, inside the same transaction."""
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
                    # Externally keyed work (a workflow's coding task) never reuses a task
                    # the message launched for something else; it takes over the link.
                    existing_task = TaskRepository.get(session, origin_message.linked_task_id)
                    if existing_task is not None and (
                        external_source is None
                        or (existing_task.external_source, existing_task.external_key)
                        == (external_source, external_key)
                    ):
                        return existing_task
            if parent_task_id is not None and TaskRepository.get(session, parent_task_id) is None:
                raise TaskNotFoundError(parent_task_id)
            work_item = get_work_item(session, work_item_id) if work_item_id is not None else None
            if work_item is None and work_item_space is not None:
                repository_id = normalized_context.get("repository_id")
                work_item = resolve_for_run(
                    session,
                    chat_session_id=chat_session_id,
                    title=item_title(goal or normalized_request),
                    space_slug=work_item_space,
                    links=(
                        [
                            WorkItemLink(
                                kind="repository",
                                label=str(repository_id),
                                ref=str(repository_id),
                            )
                        ]
                        if repository_id
                        else []
                    ),
                )

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
                # Clients that send a turn as a task (voice, the app) mention items too.
                _focus_mentions(session, chat_session.id, normalized_request)
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
            if work_item is not None:
                link_task(session, task=task, item=work_item)
            session.flush()
            return task

    def work_item_context(self, task: TaskModel) -> list[dict[str, Any]]:
        """Briefs of the work items a run is about, for its model prompt."""
        with self.database.session() as session:
            return work_item_context(session, task)

    def task_context(self, task_id: str) -> dict[str, Any]:
        """The curated, whitelisted summary of a task — safe to put in a model prompt."""
        with self.database.session() as session:
            task = self._require_task(session, task_id)
            events = TaskRepository.list_events(session, task.id)
            context = build_task_context(task, events)
            checkpoint = session.get(CodingRunModel, task.id)
            if checkpoint is not None:
                context["coding_checkpoint"] = {
                    "phase": checkpoint.phase,
                    "branch": checkpoint.branch,
                    "commit_sha": checkpoint.commit_sha,
                    "pull_request_number": checkpoint.pull_request_number,
                    "pull_request_head_sha": checkpoint.pull_request_head_sha,
                    "validation": checkpoint.validation_results,
                    "runner_attempts": checkpoint.runner_attempts,
                }
            return context

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
            parent_work_item_id = parent.work_item_id
            parent_repository_id = (parent.source_context or {}).get("repository_id")

        next_source_context = {**(source_context or {}), "parent_task": context}
        # Only coding work (a PR revision) carries the repository on; a plain follow-up is
        # a conversation about the parent, which no coding worker should pick up.
        wants_coding = bool(CODING_CAPABILITIES.intersection(required_capabilities or []))
        if wants_coding and parent_repository_id and "repository_id" not in next_source_context:
            next_source_context["repository_id"] = parent_repository_id

        return self.create_task(
            request=request,
            goal=goal,
            required_capabilities=required_capabilities,
            source_context=next_source_context,
            chat_session_id=chat_session_id,
            parent_task_id=task_id,
            work_item_id=parent_work_item_id,
        )

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
