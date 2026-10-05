"""Helpers shared by the route modules."""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from app.api.schemas import TaskListResponse, TaskResponse
from app.domain.enums import CODING_CAPABILITIES, TaskStatus
from app.domain.transitions import TERMINAL_STATUSES
from app.services import TaskService


def task_service(request: Request) -> TaskService:
    return request.app.state.task_service


def require_approval_token(request: Request) -> None:
    configured_token = request.app.state.approval_token
    supplied_token = request.headers.get("X-Assistant-Approval-Token")
    if not configured_token:
        raise HTTPException(status_code=503, detail="Approval authorization is not configured")
    if not supplied_token or not hmac.compare_digest(supplied_token, configured_token):
        raise HTTPException(status_code=401, detail="Invalid approval authorization")


def task_list_with_progress(request: Request, tasks: list) -> TaskListResponse:
    """Tasks with what their agents last reported, for live cards."""
    progress = task_service(request).task_progress(
        [task.id for task in tasks if active_status(task)]
    )
    return TaskListResponse(
        tasks=[TaskResponse.from_model(task, progress.get(task.id)) for task in tasks]
    )


def active_status(task) -> bool:
    return TaskStatus(task.status) not in TERMINAL_STATUSES


CODING_WORKFLOW_REQUIRED = (
    "Coding work starts from a coding workflow (propose → approve → start), not from a plain task"
)


def reject_direct_coding(
    required_capabilities: list[str],
    repository_id: str | None,
    source_context: dict[str, object],
) -> None:
    """Keep the approval gate the only way to start coding work from the API."""
    if (
        CODING_CAPABILITIES.intersection(required_capabilities)
        or repository_id
        or source_context.get("repository_id")
    ):
        raise HTTPException(status_code=422, detail=CODING_WORKFLOW_REQUIRED)


def repository_context(
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
