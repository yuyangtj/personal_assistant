from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import text

from app.api.schemas import (
    AddMessageRequest,
    AppendChatMessageRequest,
    AppendChatMessageResponse,
    CapabilityListResponse,
    ChatMessageListResponse,
    ChatMessageResponse,
    ChatSessionListResponse,
    ChatSessionResponse,
    CreateChatSessionRequest,
    CreateTaskFromMessageRequest,
    CreateTaskRequest,
    CreateWorkflowRunRequest,
    EventListResponse,
    EventResponse,
    FollowUpTaskRequest,
    HealthResponse,
    PendingApprovalResponse,
    PullRequestApprovalRequest,
    RepositoryListResponse,
    RepositoryResponse,
    SpeechRequest,
    TaskContextResponse,
    TaskListResponse,
    TaskProposalResponse,
    TaskResponse,
    WorkflowDecisionRequest,
    WorkflowListResponse,
    WorkflowRunEventListResponse,
    WorkflowRunEventResponse,
    WorkflowRunListResponse,
    WorkflowRunResponse,
)
from app.capabilities.models import CapabilityKind
from app.domain.enums import TaskStatus
from app.integrations.chat import ProviderError
from app.integrations.github import GitHubError
from app.service import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ChatMessageNotFoundError,
    ChatSessionNotFoundError,
    TaskNotFoundError,
    TaskService,
)
from app.workflows import WorkflowRunConflictError, WorkflowRunNotFoundError

router = APIRouter()

WEB_INDEX = Path(__file__).resolve().parents[1] / "web" / "index.html"


def _service(request: Request) -> TaskService:
    return request.app.state.task_service


def _require_approval_authorization(request: Request) -> None:
    configured_token = request.app.state.approval_token
    supplied_token = request.headers.get("X-Assistant-Approval-Token")
    if not configured_token:
        raise HTTPException(status_code=503, detail="Approval authorization is not configured")
    if not supplied_token or not hmac.compare_digest(supplied_token, configured_token):
        raise HTTPException(status_code=401, detail="Invalid approval authorization")


def _repository_context(
    request: Request,
    source_context: dict[str, object],
    repository_id: str | None,
) -> dict[str, object]:
    context = dict(source_context)
    if repository_id:
        try:
            manifest = request.app.state.repository_registry.get(repository_id)
        except LookupError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        context["repository_id"] = manifest.id
    return context


@router.post(
    "/chat-sessions",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_chat_session(
    body: CreateChatSessionRequest,
    request: Request,
) -> ChatSessionResponse:
    try:
        return ChatSessionResponse.model_validate(
            _service(request).create_chat_session(title=body.title)
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/chat-sessions", response_model=ChatSessionListResponse)
def list_chat_sessions(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ChatSessionListResponse:
    sessions = _service(request).list_chat_sessions(limit=limit)
    return ChatSessionListResponse(
        sessions=[ChatSessionResponse.model_validate(chat) for chat in sessions]
    )


@router.get("/chat-sessions/{chat_session_id}", response_model=ChatSessionResponse)
def get_chat_session(chat_session_id: str, request: Request) -> ChatSessionResponse:
    try:
        return ChatSessionResponse.model_validate(
            _service(request).get_chat_session(chat_session_id)
        )
    except ChatSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error


@router.get("/chat-sessions/{chat_session_id}/tasks", response_model=TaskListResponse)
def list_chat_session_tasks(
    chat_session_id: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> TaskListResponse:
    try:
        tasks = _service(request).list_chat_session_tasks(chat_session_id, limit=limit)
    except ChatSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error
    return TaskListResponse(tasks=[TaskResponse.from_model(task) for task in tasks])


@router.get(
    "/chat-sessions/{chat_session_id}/messages",
    response_model=ChatMessageListResponse,
)
def list_chat_session_messages(
    chat_session_id: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
) -> ChatMessageListResponse:
    try:
        messages = _service(request).list_chat_messages(chat_session_id, limit=limit)
    except ChatSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error
    return ChatMessageListResponse(
        messages=[ChatMessageResponse.from_model(message) for message in messages]
    )


@router.post(
    "/chat-sessions/{chat_session_id}/messages",
    response_model=AppendChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def append_chat_message(
    chat_session_id: str,
    body: AppendChatMessageRequest,
    request: Request,
) -> AppendChatMessageResponse:
    """Record what the user said. Nothing is executed until a task is confirmed."""
    try:
        message, proposal = _service(request).append_chat_message(
            chat_session_id,
            content=body.content,
        )
    except ChatSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return AppendChatMessageResponse(
        message=ChatMessageResponse.from_model(message),
        proposal=(
            TaskProposalResponse.model_validate(proposal.as_dict())
            if proposal is not None
            else None
        ),
    )


@router.post(
    "/chat-sessions/{chat_session_id}/messages/{message_id}/task",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_task_from_chat_message(
    chat_session_id: str,
    message_id: str,
    body: CreateTaskFromMessageRequest,
    request: Request,
) -> TaskResponse:
    """Launch the work a chat message asked for, on the user's explicit confirmation."""
    try:
        task = _service(request).create_task_from_message(
            chat_session_id,
            message_id,
            goal=body.goal,
            required_capabilities=body.required_capabilities,
            source_context=_repository_context(
                request, body.source_context, body.repository_id
            ),
        )
    except ChatSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error
    except ChatMessageNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat message not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return TaskResponse.from_model(task)


@router.post(
    "/tasks/{task_id}/chat-session",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def ensure_task_chat_session(task_id: str, request: Request) -> ChatSessionResponse:
    try:
        chat_session = _service(request).ensure_task_chat_session(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error
    return ChatSessionResponse.model_validate(chat_session)


@router.post("/speech", response_class=Response)
def synthesize_speech(body: SpeechRequest, request: Request) -> Response:
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Speech text cannot be empty")
    synthesizer = request.app.state.speech_synthesizer
    if synthesizer is None:
        raise HTTPException(status_code=503, detail="Cloud speech is not configured")
    try:
        result = synthesizer.synthesize(text, emotion=body.emotion)
    except ProviderError as error:
        raise HTTPException(status_code=502, detail="Cloud speech generation failed") from error
    return Response(
        content=result.wav_bytes,
        media_type="audio/wav",
        headers={
            "X-Speech-Provider": result.provider,
            "X-Speech-Model": result.model,
            "X-Speech-Voice": result.voice,
            "X-Speech-Duration-Ms": str(result.duration_ms),
            "X-Speech-Cache": "hit" if result.cache_hit else "miss",
        },
    )


@router.post(
    "/tasks/{task_id}/pull-request-approval",
    response_model=TaskResponse,
)
def decide_pull_request_approval(
    task_id: str,
    body: PullRequestApprovalRequest,
    request: Request,
) -> TaskResponse:
    _require_approval_authorization(request)
    service = _service(request)
    if body.decision == "reject":
        try:
            return TaskResponse.from_model(service.reject_approval(task_id))
        except TaskNotFoundError as error:
            raise HTTPException(status_code=404, detail="Task not found") from error
        except ApprovalNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail="Pull request approval not found",
            ) from error
        except ApprovalConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    if not body.expected_head_sha:
        raise HTTPException(status_code=422, detail="expected_head_sha is required for approval")
    github = request.app.state.github_client
    if github is None:
        raise HTTPException(status_code=503, detail="GitHub integration is not configured")
    try:
        approval = service.begin_pull_request_approval(
            task_id,
            expected_head_sha=body.expected_head_sha,
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error
    except ApprovalNotFoundError as error:
        raise HTTPException(status_code=404, detail="Pull request approval not found") from error
    except ApprovalConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    try:
        repository = str(approval["repository"])
        number = int(approval["number"])
        execution_id = str(approval["execution_id"])
        pull_request = github.get_pull_request(repository=repository, number=number)
        if pull_request.head_sha != body.expected_head_sha:
            service.return_to_pull_request_approval(
                task_id,
                reason="pull_request_head_changed",
                replacement_head_sha=pull_request.head_sha,
            )
            raise HTTPException(
                status_code=409,
                detail="Pull request changed; review the new head SHA before approving",
            )
        if pull_request.merged:
            return TaskResponse.from_model(
                service.complete_pull_request_merge(
                    task_id,
                    execution_id=execution_id,
                    repository=repository,
                    number=number,
                    url=pull_request.url,
                    merge_sha=pull_request.merge_commit_sha,
                )
            )
        if pull_request.state != "open" or pull_request.draft:
            reason = "pull_request_is_draft" if pull_request.draft else "pull_request_is_not_open"
            service.return_to_pull_request_approval(task_id, reason=reason)
            raise HTTPException(
                status_code=409,
                detail=(
                    "Mark the pull request ready for review before approving merge"
                    if pull_request.draft
                    else "Pull request is not open"
                ),
            )
        service.record_pull_request_merge_call(
            task_id,
            repository=repository,
            number=number,
        )
        result = github.merge_pull_request(
            repository=repository,
            number=number,
            expected_head_sha=body.expected_head_sha,
            method=body.merge_method,
            commit_title=f"Merge assistant task {task_id[:12]}",
        )
        if not result.merged:
            service.return_to_pull_request_approval(task_id, reason="github_declined_merge")
            raise HTTPException(status_code=409, detail="GitHub did not merge the pull request")
        return TaskResponse.from_model(
            service.complete_pull_request_merge(
                task_id,
                execution_id=execution_id,
                repository=repository,
                number=number,
                url=pull_request.url,
                merge_sha=result.sha,
            )
        )
    except HTTPException:
        raise
    except (GitHubError, KeyError, TypeError, ValueError) as error:
        service.return_to_pull_request_approval(task_id, reason="github_request_failed")
        raise HTTPException(status_code=502, detail="GitHub operation failed") from error


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(body: CreateTaskRequest, request: Request) -> TaskResponse:
    try:
        task = _service(request).create_task(
            request=body.request,
            goal=body.goal,
            required_capabilities=body.required_capabilities,
            source_context=_repository_context(
                request, body.source_context, body.repository_id
            ),
            chat_session_id=body.chat_session_id,
            external_source=body.external_source,
            external_key=body.external_key,
        )
    except ChatSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return TaskResponse.from_model(task)


@router.get("/tasks", response_model=TaskListResponse)
def list_tasks(
    request: Request,
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> TaskListResponse:
    tasks = _service(request).list_tasks(status=task_status, limit=limit)
    return TaskListResponse(tasks=[TaskResponse.from_model(task) for task in tasks])


@router.get(
    "/tasks/{task_id}/pending-approval",
    response_model=PendingApprovalResponse,
)
def get_pending_approval(task_id: str, request: Request) -> PendingApprovalResponse:
    try:
        approval = _service(request).get_pending_approval(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error
    except ApprovalNotFoundError as error:
        raise HTTPException(status_code=404, detail="Pending approval not found") from error
    return PendingApprovalResponse.model_validate(approval)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: str, request: Request) -> TaskResponse:
    try:
        return TaskResponse.from_model(_service(request).get_task(task_id))
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error


@router.get("/tasks/{task_id}/context", response_model=TaskContextResponse)
def get_task_context(task_id: str, request: Request) -> TaskContextResponse:
    """The curated summary a follow-up conversation is given, without the event stream."""
    try:
        return TaskContextResponse.model_validate(_service(request).task_context(task_id))
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error


@router.post(
    "/tasks/{task_id}/follow-up",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_follow_up_task(
    task_id: str,
    body: FollowUpTaskRequest,
    request: Request,
) -> TaskResponse:
    """A new task continuing an earlier one, carrying only its curated context."""
    try:
        task = _service(request).create_follow_up_task(
            task_id,
            request=body.request,
            goal=body.goal,
            required_capabilities=body.required_capabilities,
            source_context=_repository_context(
                request, body.source_context, body.repository_id
            ),
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error
    except ChatSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return TaskResponse.from_model(task)


@router.get("/tasks/{task_id}/events", response_model=EventListResponse)
def list_task_events(task_id: str, request: Request) -> EventListResponse:
    try:
        events = _service(request).list_events(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error
    return EventListResponse(events=[EventResponse.from_model(event) for event in events])


@router.post("/tasks/{task_id}/messages", status_code=status.HTTP_204_NO_CONTENT)
def add_task_message(task_id: str, body: AddMessageRequest, request: Request) -> None:
    try:
        _service(request).add_user_message(task_id, body.message)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error


@router.post("/tasks/{task_id}/cancel", response_model=TaskResponse)
def cancel_task(task_id: str, request: Request) -> TaskResponse:
    try:
        return TaskResponse.from_model(_service(request).cancel_task(task_id))
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error


@router.get("/ui", include_in_schema=False)
def web_console() -> FileResponse:
    """A local console for exercising the chat and task flow from a browser."""
    if not WEB_INDEX.is_file():
        raise HTTPException(status_code=404, detail="Web console is not installed")
    return FileResponse(WEB_INDEX, media_type="text/html")


@router.get("/", include_in_schema=False)
def web_console_root() -> RedirectResponse:
    return RedirectResponse(url="/ui", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    with request.app.state.database.session() as session:
        session.execute(text("SELECT 1"))
    return HealthResponse(status="ok")


@router.get("/capabilities", response_model=CapabilityListResponse)
def list_capabilities(
    request: Request,
    kind: CapabilityKind | None = None,
    include_disabled: bool = True,
) -> CapabilityListResponse:
    capabilities = request.app.state.capability_registry.list(
        kind=kind,
        include_disabled=include_disabled,
    )
    return CapabilityListResponse(capabilities=capabilities)


@router.get("/repositories", response_model=RepositoryListResponse)
def list_repositories(request: Request) -> RepositoryListResponse:
    settings = request.app.state.settings
    repositories = []
    for manifest in request.app.state.repository_registry.list():
        configured = bool(os.getenv(manifest.path_env))
        if manifest.id == "personal-assistant" and settings.code_repository_path is not None:
            configured = True
        repositories.append(
            RepositoryResponse(
                id=manifest.id,
                name=manifest.name,
                description=manifest.description,
                aliases=list(manifest.aliases),
                github_repository=manifest.github_repository,
                base_branch=manifest.base_branch,
                default=manifest.default,
                configured=configured,
            )
        )
    return RepositoryListResponse(repositories=repositories)


@router.get("/workflows", response_model=WorkflowListResponse)
def list_workflows(request: Request) -> WorkflowListResponse:
    return WorkflowListResponse(workflows=request.app.state.workflow_registry.list())


@router.post(
    "/workflow-runs",
    response_model=WorkflowRunResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_workflow_run(
    body: CreateWorkflowRunRequest,
    request: Request,
) -> WorkflowRunResponse:
    try:
        run = request.app.state.workflow_service.create_run(
            workflow_id=body.workflow_id,
            workflow_input=body.input,
            chat_session_id=body.chat_session_id,
            task_id=body.task_id,
        )
    except (LookupError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return WorkflowRunResponse.from_model(run)


@router.get("/workflow-runs", response_model=WorkflowRunListResponse)
def list_workflow_runs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> WorkflowRunListResponse:
    runs = request.app.state.workflow_service.list_runs(limit=limit)
    return WorkflowRunListResponse(runs=[WorkflowRunResponse.from_model(run) for run in runs])


@router.get("/workflow-runs/{workflow_run_id}", response_model=WorkflowRunResponse)
def get_workflow_run(workflow_run_id: str, request: Request) -> WorkflowRunResponse:
    try:
        run = request.app.state.workflow_service.get_run(workflow_run_id)
    except WorkflowRunNotFoundError as error:
        raise HTTPException(status_code=404, detail="Workflow run not found") from error
    return WorkflowRunResponse.from_model(run)


@router.get(
    "/workflow-runs/{workflow_run_id}/events",
    response_model=WorkflowRunEventListResponse,
)
def list_workflow_run_events(
    workflow_run_id: str,
    request: Request,
) -> WorkflowRunEventListResponse:
    try:
        events = request.app.state.workflow_service.list_events(workflow_run_id)
    except WorkflowRunNotFoundError as error:
        raise HTTPException(status_code=404, detail="Workflow run not found") from error
    return WorkflowRunEventListResponse(
        events=[WorkflowRunEventResponse.from_model(event) for event in events]
    )


@router.post(
    "/workflow-runs/{workflow_run_id}/decision",
    response_model=WorkflowRunResponse,
)
def decide_workflow_run(
    workflow_run_id: str,
    body: WorkflowDecisionRequest,
    request: Request,
) -> WorkflowRunResponse:
    _require_approval_authorization(request)
    try:
        run = request.app.state.workflow_service.decide(
            workflow_run_id,
            approve=body.decision == "approve",
        )
    except WorkflowRunNotFoundError as error:
        raise HTTPException(status_code=404, detail="Workflow run not found") from error
    except WorkflowRunConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return WorkflowRunResponse.from_model(run)
