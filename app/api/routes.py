from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import text

from app.api.schemas import (
    AddMessageRequest,
    CapabilityListResponse,
    CreateTaskRequest,
    EventListResponse,
    EventResponse,
    HealthResponse,
    TaskListResponse,
    TaskResponse,
)
from app.capabilities.models import CapabilityKind
from app.domain.enums import TaskStatus
from app.service import TaskNotFoundError, TaskService

router = APIRouter()


def _service(request: Request) -> TaskService:
    return request.app.state.task_service


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
