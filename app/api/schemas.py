from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.capabilities.models import CapabilityManifest
from app.decision import CodingRunnerManifest
from app.deployments import DeploymentTarget
from app.domain.enums import EventType, TaskStatus
from app.domain.work_items import (
    Brief,
    WorkItemKind,
    WorkItemLink,
    WorkItemStatus,
)
from app.persistence.models import (
    ChatMessageModel,
    SpaceModel,
    TaskEventModel,
    TaskModel,
    WorkflowRunEventModel,
    WorkflowRunModel,
    WorkItemModel,
)
from app.validation import ValidationProfile
from app.workflows.models import WorkflowManifest, WorkflowRunStatus


class CreateTaskRequest(BaseModel):
    request: str = Field(min_length=1, max_length=20_000)
    goal: str | None = Field(default=None, min_length=1, max_length=20_000)
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)
    repository_id: str | None = Field(default=None, min_length=1, max_length=120)
    source_context: dict[str, Any] = Field(default_factory=dict)
    chat_session_id: str | None = Field(default=None, min_length=36, max_length=36)
    external_source: str | None = Field(default=None, max_length=32)
    external_key: str | None = Field(default=None, max_length=255)
    work_item_id: str | None = Field(default=None, min_length=1, max_length=80)


class AddMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)


class AppendChatMessageRequest(BaseModel):
    """A turn of conversation. Posting one never launches work."""

    content: str = Field(min_length=1, max_length=20_000)
    #: The client's repository picker; triage treats it as an override, not an intent.
    repository_id: str | None = Field(default=None, min_length=1, max_length=120)
    #: The user's clock, so "tomorrow at 9" resolves in their timezone.
    local_time: str | None = Field(default=None, max_length=40)
    timezone: str | None = Field(default=None, max_length=64)


class CreateTaskFromMessageRequest(BaseModel):
    """Confirmation that a message the user already sent should become a task."""

    goal: str | None = Field(default=None, min_length=1, max_length=20_000)
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)
    repository_id: str | None = Field(default=None, min_length=1, max_length=120)
    source_context: dict[str, Any] = Field(default_factory=dict)
    work_item_id: str | None = Field(default=None, min_length=1, max_length=80)
    create_work_item: bool = False
    #: Space for a newly created work item (validated against the space packs).
    space: str | None = Field(default=None, min_length=1, max_length=64)


class FollowUpTaskRequest(BaseModel):
    request: str = Field(min_length=1, max_length=20_000)
    goal: str | None = Field(default=None, min_length=1, max_length=20_000)
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)
    repository_id: str | None = Field(default=None, min_length=1, max_length=120)
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


class TriageOptionResponse(BaseModel):
    label: str
    intent: str
    repository_id: str | None = None


class TriageDecisionResponse(BaseModel):
    intent: str
    goal: str
    reason: str
    confidence: float
    repository_id: str | None = None
    question: str | None = None
    options: list[TriageOptionResponse] = Field(default_factory=list)
    action: dict[str, Any] | None = None
    capabilities: list[str] = Field(default_factory=list)
    space: str | None = None
    #: For direct actions, what was done (also posted as the assistant's reply).
    result: str | None = None
    source: str


class AppendChatMessageResponse(BaseModel):
    message: ChatMessageResponse
    #: Keyword-rule proposal, kept for older clients; new clients render ``decision``.
    proposal: TaskProposalResponse | None = None
    #: What the server decided this message needs (answer, propose work, or ask).
    decision: TriageDecisionResponse | None = None
    #: Slugs of work items this message focused the chat on through #mentions.
    focused_work_items: list[str] = Field(default_factory=list)


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
    superseded_by_task_id: str | None = None
    final_answer: str | None
    summary: str | None
    validation: str
    artifacts: list[TaskArtifactReference]
    error: str | None
    created_at: datetime | None
    updated_at: datetime | None
    coding_checkpoint: dict[str, Any] | None = None


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


class PullRequestReadyRequest(BaseModel):
    expected_head_sha: str = Field(
        min_length=40,
        max_length=64,
        pattern=r"^[0-9a-f]+$",
    )


class PullRequestRevisionRequest(BaseModel):
    instructions: str = Field(min_length=1, max_length=20_000)
    expected_head_sha: str = Field(min_length=40, max_length=64, pattern=r"^[0-9a-f]+$")


class PendingApprovalResponse(BaseModel):
    type: Literal["github_pull_request_merge"]
    repository: str
    number: int
    url: str
    head_branch: str | None = None
    expected_head_sha: str
    draft: bool
    execution_id: str
    reason: str | None = None


class PullRequestCheckResponse(BaseModel):
    name: str
    status: str
    conclusion: str | None
    url: str | None
    required: bool


class PullRequestStatusResponse(BaseModel):
    repository: str
    number: int
    url: str
    expected_head_sha: str
    current_head_sha: str
    head_matches: bool
    state: str
    draft: bool
    mergeable: bool | None
    required_checks_state: Literal["passed", "pending", "failed", "missing"]
    checks: list[PullRequestCheckResponse]
    unresolved_thread_count: int


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
    superseded_by_task_id: str | None
    work_item_id: str | None
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


class RepositoryResponse(BaseModel):
    id: str
    name: str
    description: str
    aliases: list[str]
    github_repository: str
    base_branch: str
    default: bool
    configured: bool
    required_checks: list[str]


class RepositoryListResponse(BaseModel):
    repositories: list[RepositoryResponse]


class ValidationProfileListResponse(BaseModel):
    profiles: list[ValidationProfile]


class DeploymentTargetListResponse(BaseModel):
    targets: list[DeploymentTarget]


class CreateMemoryRequest(BaseModel):
    kind: Literal["fact", "preference", "project"]
    content: str = Field(min_length=1, max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    chat_session_id: str | None = None
    task_id: str | None = None


class MemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: str
    content: str
    tags: list[str]
    source_chat_session_id: str | None
    source_task_id: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class MemoryListResponse(BaseModel):
    memories: list[MemoryResponse]


class ProviderRuntimeStateResponse(BaseModel):
    provider_key: str
    available: bool
    cooldown_until: datetime | None
    last_error_category: str | None
    consecutive_failures: int
    total_successes: int
    total_failures: int
    last_success_at: datetime | None
    last_failure_at: datetime | None
    updated_at: datetime | None


class ProviderRuntimeStateListResponse(BaseModel):
    providers: list[ProviderRuntimeStateResponse]


class CodingRunnerListResponse(BaseModel):
    runners: list[CodingRunnerManifest]


class WorkflowListResponse(BaseModel):
    workflows: list[WorkflowManifest]


class CreateWorkflowRunRequest(BaseModel):
    workflow_id: str = Field(min_length=1, max_length=128)
    input: dict[str, Any] = Field(default_factory=dict)
    chat_session_id: str | None = Field(default=None, min_length=36, max_length=36)
    task_id: str | None = Field(default=None, min_length=36, max_length=36)


class CreateCodingWorkflowFromMessageRequest(BaseModel):
    repository_id: str = Field(min_length=1, max_length=120)


class CreateDeploymentWorkflowRequest(BaseModel):
    deployment_target_id: str = Field(min_length=1, max_length=120)


class WorkflowDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]


class WorkflowRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    status: WorkflowRunStatus
    input: dict[str, Any]
    current_stage: str | None
    chat_session_id: str | None
    task_id: str | None
    origin_message_id: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, run: WorkflowRunModel) -> WorkflowRunResponse:
        return cls(
            id=run.id,
            workflow_id=run.workflow_id,
            status=WorkflowRunStatus(run.status),
            input=run.workflow_input,
            current_stage=run.current_stage,
            chat_session_id=run.chat_session_id,
            task_id=run.task_id,
            origin_message_id=run.origin_message_id,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )


class WorkflowRunListResponse(BaseModel):
    runs: list[WorkflowRunResponse]


class WorkflowRunEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workflow_run_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_model(cls, event: WorkflowRunEventModel) -> WorkflowRunEventResponse:
        return cls.model_validate(event)


class WorkflowRunEventListResponse(BaseModel):
    events: list[WorkflowRunEventResponse]


class SpaceResponse(BaseModel):
    id: str
    slug: str
    name: str
    kind: str

    @classmethod
    def from_model(cls, space: SpaceModel) -> SpaceResponse:
        return cls(id=space.id, slug=space.slug, name=space.name, kind=space.kind)


class SpaceListResponse(BaseModel):
    spaces: list[SpaceResponse]


class CreateWorkItemRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    kind: WorkItemKind = WorkItemKind.GOAL
    space: str = Field(default="general", min_length=1, max_length=64)
    brief: Brief | None = None
    links: list[WorkItemLink] = Field(default_factory=list, max_length=50)
    chat_session_id: str | None = Field(default=None, min_length=36, max_length=36)


class UpdateWorkItemRequest(BaseModel):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    slug: str | None = Field(default=None, min_length=1, max_length=80)
    status: WorkItemStatus | None = None
    brief: Brief | None = None
    links: list[WorkItemLink] | None = Field(default=None, max_length=50)


class AddChecklistEntryRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)


class UpdateChecklistEntryRequest(BaseModel):
    done: bool


class DiscussWorkItemRequest(BaseModel):
    about: str | None = Field(default=None, max_length=1000)


class WorkItemResponse(BaseModel):
    id: str
    slug: str
    space: str
    kind: WorkItemKind
    title: str
    status: WorkItemStatus
    brief: dict[str, Any]
    checklist: list[dict[str, Any]]
    links: list[dict[str, Any]]
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, item: WorkItemModel, *, space: str) -> WorkItemResponse:
        return cls(
            id=item.id,
            slug=item.slug,
            space=space,
            kind=WorkItemKind(item.kind),
            title=item.title,
            status=WorkItemStatus(item.status),
            brief=item.brief or {},
            checklist=list(item.checklist or []),
            links=list(item.links or []),
            version=item.version,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class WorkItemListResponse(BaseModel):
    work_items: list[WorkItemResponse]


class TimelineEntryResponse(BaseModel):
    id: str
    source: Literal["work_item", "task"]
    event_type: str
    at: datetime
    work_item_id: str | None
    task_id: str | None
    chat_session_id: str | None
    summary: str


class TimelineResponse(BaseModel):
    entries: list[TimelineEntryResponse]


class CreateScheduleRequest(BaseModel):
    kind: Literal["reminder", "routine"] = "reminder"
    message: str = Field(min_length=1, max_length=500)
    run_at: datetime
    recurrence: Literal["none", "daily", "weekdays", "weekly", "monthly"] = "none"
    timezone: str = Field(default="UTC", max_length=64)
    work_item_id: str | None = Field(default=None, max_length=80)
    chat_session_id: str | None = Field(default=None, min_length=36, max_length=36)


class ScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    message: str
    work_item_id: str | None
    chat_session_id: str | None
    next_run_at: datetime
    recurrence: str
    timezone: str
    active: bool
    last_run_at: datetime | None


class ScheduleListResponse(BaseModel):
    schedules: list[ScheduleResponse]
