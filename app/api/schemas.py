from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.capabilities.models import CapabilityManifest
from app.domain.enums import EventType, TaskStatus
from app.persistence.models import ChatMessageModel, TaskEventModel, TaskModel


class CreateTaskRequest(BaseModel):
    request: str = Field(min_length=1, max_length=20_000)
    goal: str | None = Field(default=None, min_length=1, max_length=20_000)
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)
    source_context: dict[str, Any] = Field(default_factory=dict)
    chat_session_id: str | None = Field(default=None, min_length=36, max_length=36)
    external_source: str | None = Field(default=None, max_length=32)
    external_key: str | None = Field(default=None, max_length=255)


class AddMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)


class AppendChatMessageRequest(BaseModel):
    """A turn of conversation. Posting one never launches work."""

    content: str = Field(min_length=1, max_length=20_000)


class CreateTaskFromMessageRequest(BaseModel):
    """Confirmation that a message the user already sent should become a task."""

    goal: str | None = Field(default=None, min_length=1, max_length=20_000)
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)
    source_context: dict[str, Any] = Field(default_factory=dict)


class FollowUpTaskRequest(BaseModel):
    request: str = Field(min_length=1, max_length=20_000)
    goal: str | None = Field(default=None, min_length=1, max_length=20_000)
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)
    source_context: dict[str, Any] = Field(default_factory=dict)


class CreateChatSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=160)


class ChatSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    archived: bool
    created_at: datetime
    updated_at: datetime


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chat_session_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    linked_task_id: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, message: ChatMessageModel) -> ChatMessageResponse:
        return cls.model_validate(message)


class TaskProposalResponse(BaseModel):
    """A suggested task the client should confirm before anything runs."""

    reason: str
    suggested_goal: str
    required_capabilities: list[str]
    consequential: bool


class AppendChatMessageResponse(BaseModel):
    message: ChatMessageResponse
    proposal: TaskProposalResponse | None = None


class TaskArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    url: str | None = None
    repository: str | None = None
    number: int | None = None
    head_branch: str | None = None
    head_sha: str | None = None
    merge_sha: str | None = None


class TaskContextResponse(BaseModel):
    """The curated view of a task: what a follow-up conversation is allowed to see."""

    task_id: str
    status: TaskStatus
    request: str
    goal: str
    chat_session_id: str | None
    origin_message_id: str | None
    parent_task_id: str | None
    final_answer: str | None
    summary: str | None
    validation: str
    artifacts: list[TaskArtifactReference]
    error: str | None
    created_at: datetime | None
    updated_at: datetime | None


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    emotion: Literal["Warm", "Curious", "Excited", "Concerned", "Neutral"] = "Warm"


class PullRequestApprovalRequest(BaseModel):
    decision: Literal["approve", "reject"]
    expected_head_sha: str | None = Field(
        default=None,
        min_length=40,
        max_length=64,
        pattern=r"^[0-9a-f]+$",
    )
    merge_method: Literal["merge", "squash", "rebase"] = "squash"


class PendingApprovalResponse(BaseModel):
    type: Literal["github_pull_request_merge"]
    repository: str
    number: int
    url: str
    expected_head_sha: str
    draft: bool
    execution_id: str
    reason: str | None = None


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_request: str
    current_goal: str
    status: TaskStatus
    cancel_requested: bool
    required_capabilities: list[str]
    source_context: dict[str, Any]
    chat_session_id: str | None
    origin_message_id: str | None
    parent_task_id: str | None
    claimed_by: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, task: TaskModel) -> TaskResponse:
        return cls.model_validate(task)


class EventResponse(BaseModel):
    id: int
    task_id: str
    sequence: int
    event_type: EventType
    payload: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_model(cls, event: TaskEventModel) -> EventResponse:
        return cls(
            id=event.id,
            task_id=event.task_id,
            sequence=event.sequence,
            event_type=EventType(event.event_type),
            payload=event.payload,
            created_at=event.created_at,
        )


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]


class ChatSessionListResponse(BaseModel):
    sessions: list[ChatSessionResponse]


class ChatMessageListResponse(BaseModel):
    messages: list[ChatMessageResponse]


class EventListResponse(BaseModel):
    events: list[EventResponse]


class HealthResponse(BaseModel):
    status: str


class CapabilityListResponse(BaseModel):
    capabilities: list[CapabilityManifest]
