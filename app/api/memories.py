"""Facts and preferences the assistant remembers."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.api.schemas import (
    CreateMemoryRequest,
    MemoryListResponse,
    MemoryResponse,
    UpdateMemoryRequest,
)

router = APIRouter()


@router.post("/memories", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
def create_memory(body: CreateMemoryRequest, request: Request) -> MemoryResponse:
    try:
        memory = request.app.state.memory_service.create(
            kind=body.kind,
            content=body.content,
            tags=body.tags,
            private=body.private,
            chat_session_id=body.chat_session_id,
            task_id=body.task_id,
        )
        return MemoryResponse.model_validate(memory)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/memories", response_model=MemoryListResponse)
def list_memories(request: Request, include_archived: bool = False) -> MemoryListResponse:
    memories = request.app.state.memory_service.list(active_only=not include_archived)
    return MemoryListResponse(memories=[MemoryResponse.model_validate(item) for item in memories])


@router.patch("/memories/{memory_id}", response_model=MemoryResponse)
def update_memory(memory_id: str, body: UpdateMemoryRequest, request: Request) -> MemoryResponse:
    try:
        memory = request.app.state.memory_service.update(
            memory_id,
            content=body.content,
            kind=body.kind,
            tags=body.tags,
            private=body.private,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Memory not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return MemoryResponse.model_validate(memory)


@router.delete("/memories/{memory_id}", response_model=MemoryResponse)
def archive_memory(memory_id: str, request: Request) -> MemoryResponse:
    try:
        return MemoryResponse.model_validate(request.app.state.memory_service.archive(memory_id))
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Memory not found") from error
