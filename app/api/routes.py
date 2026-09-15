from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy import text

from app.api.schemas import (
    AddMessageRequest,
    CapabilityListResponse,
    CreateTaskRequest,
    EventListResponse,
    EventResponse,
    HealthResponse,
    PullRequestApprovalRequest,
    SpeechRequest,
    TaskListResponse,
    TaskResponse,
)
from app.capabilities.models import CapabilityKind
from app.domain.enums import TaskStatus
from app.integrations.chat import ProviderError
from app.integrations.github import GitHubError
from app.service import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    TaskNotFoundError,
    TaskService,
)

router = APIRouter()


def _service(request: Request) -> TaskService:
    return request.app.state.task_service


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
    configured_token = request.app.state.approval_token
    supplied_token = request.headers.get("X-Assistant-Approval-Token")
    if not configured_token:
        raise HTTPException(status_code=503, detail="Approval authorization is not configured")
    if not supplied_token or not hmac.compare_digest(supplied_token, configured_token):
        raise HTTPException(status_code=401, detail="Invalid approval authorization")
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
            source_context=body.source_context,
            external_source=body.external_source,
            external_key=body.external_key,
        )
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


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: str, request: Request) -> TaskResponse:
    try:
        return TaskResponse.from_model(_service(request).get_task(task_id))
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error


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
