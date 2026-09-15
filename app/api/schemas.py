from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.capabilities.models import CapabilityManifest
from app.domain.enums import EventType, TaskStatus
from app.persistence.models import TaskEventModel, TaskModel


class CreateTaskRequest(BaseModel):
    request: str = Field(min_length=1, max_length=20_000)
    goal: str | None = Field(default=None, min_length=1, max_length=20_000)
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)
    source_context: dict[str, Any] = Field(default_factory=dict)
    external_source: str | None = Field(default=None, max_length=32)
    external_key: str | None = Field(default=None, max_length=255)


class AddMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)


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


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_request: str
    current_goal: str
    status: TaskStatus
    cancel_requested: bool
    required_capabilities: list[str]
    source_context: dict[str, Any]
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


class EventListResponse(BaseModel):
    events: list[EventResponse]


class HealthResponse(BaseModel):
    status: str


class CapabilityListResponse(BaseModel):
    capabilities: list[CapabilityManifest]
