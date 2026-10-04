from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.schemas import (
    AddChecklistEntryRequest,
    ChatSessionResponse,
    CreateScheduleRequest,
    CreateWorkItemRequest,
    DiscussWorkItemRequest,
    ScheduleListResponse,
    ScheduleResponse,
    SpaceListResponse,
    SpaceResponse,
    TaskListResponse,
    TaskResponse,
    TimelineEntryResponse,
    TimelineResponse,
    UpdateChecklistEntryRequest,
    UpdateWorkItemRequest,
    WorkItemListResponse,
    WorkItemResponse,
)
from app.domain.work_items import WorkItemKind, WorkItemStatus
from app.persistence.models import WorkItemModel
from app.schedules import Recurrence, ScheduleKind, ScheduleNotFoundError
from app.work_items import (
    SpaceNotFoundError,
    WorkItemConflictError,
    WorkItemNotFoundError,
    WorkItemService,
)

router = APIRouter()


def _items(request: Request) -> WorkItemService:
    return request.app.state.work_item_service


def _responses(request: Request, items: list[WorkItemModel]) -> list[WorkItemResponse]:
    slugs = _items(request).space_slugs(items)
    return [WorkItemResponse.from_model(item, space=slugs.get(item.space_id, "")) for item in items]


def _response(request: Request, item: WorkItemModel) -> WorkItemResponse:
    return _responses(request, [item])[0]


def _not_found(error: LookupError) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Not found: {error}")


@router.get("/spaces", response_model=SpaceListResponse)
def list_spaces(request: Request) -> SpaceListResponse:
    return SpaceListResponse(
        spaces=[SpaceResponse.from_model(space) for space in _items(request).list_spaces()]
    )


@router.get("/work-items", response_model=WorkItemListResponse)
def list_work_items(
    request: Request,
    space: str | None = None,
    item_status: Annotated[WorkItemStatus | None, Query(alias="status")] = None,
    kind: WorkItemKind | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> WorkItemListResponse:
    items = _items(request).list(space_slug=space, status=item_status, kind=kind, limit=limit)
    return WorkItemListResponse(work_items=_responses(request, items))


@router.post("/work-items", response_model=WorkItemResponse, status_code=status.HTTP_201_CREATED)
def create_work_item(body: CreateWorkItemRequest, request: Request) -> WorkItemResponse:
    try:
        item = _items(request).create(
            title=body.title,
            kind=body.kind,
            space_slug=body.space,
            brief=body.brief,
            links=body.links,
            chat_session_id=body.chat_session_id,
        )
    except SpaceNotFoundError as error:
        raise HTTPException(status_code=422, detail=f"Unknown space: {error}") from error
    except LookupError as error:
        raise _not_found(error) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _response(request, item)


@router.get("/work-items/{reference}", response_model=WorkItemResponse)
def get_work_item(reference: str, request: Request) -> WorkItemResponse:
    try:
        return _response(request, _items(request).get(reference))
    except WorkItemNotFoundError as error:
        raise _not_found(error) from error


@router.patch("/work-items/{reference}", response_model=WorkItemResponse)
def update_work_item(
    reference: str, body: UpdateWorkItemRequest, request: Request
) -> WorkItemResponse:
    try:
        item = _items(request).update(
            reference,
            expected_version=body.version,
            title=body.title,
            slug=body.slug,
            status=body.status,
            brief=body.brief,
            links=body.links,
        )
    except WorkItemNotFoundError as error:
        raise _not_found(error) from error
    except WorkItemConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _response(request, item)


@router.post(
    "/work-items/{reference}/checklist",
    response_model=WorkItemResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_checklist_entry(
    reference: str, body: AddChecklistEntryRequest, request: Request
) -> WorkItemResponse:
    try:
        return _response(request, _items(request).add_checklist_entry(reference, body.text))
    except WorkItemNotFoundError as error:
        raise _not_found(error) from error


@router.patch("/work-items/{reference}/checklist/{entry_id}", response_model=WorkItemResponse)
def update_checklist_entry(
    reference: str, entry_id: str, body: UpdateChecklistEntryRequest, request: Request
) -> WorkItemResponse:
    try:
        item = _items(request).set_checklist_entry(reference, entry_id, done=body.done)
    except WorkItemNotFoundError as error:
        raise _not_found(error) from error
    return _response(request, item)


@router.delete("/work-items/{reference}/checklist/{entry_id}", response_model=WorkItemResponse)
def remove_checklist_entry(reference: str, entry_id: str, request: Request) -> WorkItemResponse:
    try:
        return _response(request, _items(request).remove_checklist_entry(reference, entry_id))
    except WorkItemNotFoundError as error:
        raise _not_found(error) from error


@router.get("/work-items/{reference}/runs", response_model=TaskListResponse)
def list_work_item_runs(
    reference: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> TaskListResponse:
    try:
        runs = _items(request).runs(reference, limit=limit)
    except WorkItemNotFoundError as error:
        raise _not_found(error) from error
    return TaskListResponse(tasks=[TaskResponse.from_model(task) for task in runs])


@router.get("/work-items/{reference}/timeline", response_model=TimelineResponse)
def work_item_timeline(
    reference: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before: datetime | None = None,
) -> TimelineResponse:
    try:
        entries = _items(request).timeline(reference, limit=limit, before=before)
    except WorkItemNotFoundError as error:
        raise _not_found(error) from error
    return TimelineResponse(entries=[TimelineEntryResponse(**entry) for entry in entries])


@router.get("/timeline", response_model=TimelineResponse)
def global_timeline(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before: datetime | None = None,
) -> TimelineResponse:
    entries = _items(request).timeline(limit=limit, before=before)
    return TimelineResponse(entries=[TimelineEntryResponse(**entry) for entry in entries])


@router.post(
    "/work-items/{reference}/discuss",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def discuss_work_item(
    reference: str, body: DiscussWorkItemRequest, request: Request
) -> ChatSessionResponse:
    try:
        chat = _items(request).discuss(reference, about=body.about)
    except WorkItemNotFoundError as error:
        raise _not_found(error) from error
    return ChatSessionResponse.model_validate(chat)


@router.get("/chat-sessions/{chat_session_id}/focus", response_model=WorkItemListResponse)
def list_chat_focus(chat_session_id: str, request: Request) -> WorkItemListResponse:
    try:
        items = _items(request).focused(chat_session_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error
    return WorkItemListResponse(work_items=_responses(request, items))


@router.put(
    "/chat-sessions/{chat_session_id}/focus/{reference}",
    response_model=WorkItemListResponse,
)
def focus_chat(chat_session_id: str, reference: str, request: Request) -> WorkItemListResponse:
    try:
        _items(request).focus(chat_session_id, reference)
        items = _items(request).focused(chat_session_id)
    except WorkItemNotFoundError as error:
        raise _not_found(error) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error
    return WorkItemListResponse(work_items=_responses(request, items))


@router.delete(
    "/chat-sessions/{chat_session_id}/focus/{reference}",
    response_model=WorkItemListResponse,
)
def unfocus_chat(chat_session_id: str, reference: str, request: Request) -> WorkItemListResponse:
    try:
        _items(request).unfocus(chat_session_id, reference)
        items = _items(request).focused(chat_session_id)
    except WorkItemNotFoundError as error:
        raise _not_found(error) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error
    return WorkItemListResponse(work_items=_responses(request, items))


@router.get("/schedules", response_model=ScheduleListResponse)
def list_schedules(
    request: Request, work_item_id: str | None = None, include_inactive: bool = False
) -> ScheduleListResponse:
    item_id = None
    if work_item_id:
        try:
            item_id = _items(request).get(work_item_id).id
        except WorkItemNotFoundError as error:
            raise _not_found(error) from error
    schedules = request.app.state.schedule_service.list(
        work_item_id=item_id, active_only=not include_inactive
    )
    return ScheduleListResponse(
        schedules=[ScheduleResponse.model_validate(schedule) for schedule in schedules]
    )


@router.post("/schedules", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
def create_schedule(body: CreateScheduleRequest, request: Request) -> ScheduleResponse:
    try:
        schedule = request.app.state.schedule_service.create(
            kind=ScheduleKind(body.kind),
            message=body.message,
            run_at=body.run_at,
            recurrence=Recurrence(body.recurrence),
            timezone=body.timezone,
            work_item_id=body.work_item_id,
            chat_session_id=body.chat_session_id,
        )
    except WorkItemNotFoundError as error:
        raise _not_found(error) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ScheduleResponse.model_validate(schedule)


@router.delete("/schedules/{schedule_id}", response_model=ScheduleResponse)
def cancel_schedule(schedule_id: str, request: Request) -> ScheduleResponse:
    try:
        return ScheduleResponse.model_validate(
            request.app.state.schedule_service.cancel(schedule_id)
        )
    except ScheduleNotFoundError as error:
        raise _not_found(error) from error
