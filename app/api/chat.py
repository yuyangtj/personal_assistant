"""Chat sessions and their messages; what each posted message needs is decided in app.chat.turns."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.api.common import (
    reject_direct_coding,
    repository_context,
    task_list_with_progress,
    task_service,
)
from app.api.schemas import (
    AppendChatMessageRequest,
    AppendChatMessageResponse,
    ChatMessageListResponse,
    ChatMessageResponse,
    ChatSessionListResponse,
    ChatSessionResponse,
    CreateChatSessionRequest,
    CreateCodingWorkflowFromMessageRequest,
    CreateTaskFromMessageRequest,
    TaskListResponse,
    TaskProposalResponse,
    TaskResponse,
    TriageDecisionResponse,
    WorkflowRunResponse,
)
from app.chat.turns import private_memory_text, propose_coding_run, triage_message
from app.chat.uploads import MAX_UPLOAD_CHARACTERS, TARGETS, UploadError, detect_target
from app.services import (
    ChatMessageNotFoundError,
    ChatSessionNotFoundError,
    MessageHasWorkflowError,
    TaskNotFoundError,
)
from app.work.items import WorkItemNotFoundError

router = APIRouter()


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
            task_service(request).create_chat_session(title=body.title)
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/chat-sessions", response_model=ChatSessionListResponse)
def list_chat_sessions(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ChatSessionListResponse:
    sessions = task_service(request).list_chat_sessions(limit=limit)
    return ChatSessionListResponse(
        sessions=[ChatSessionResponse.model_validate(chat) for chat in sessions]
    )


@router.get("/chat-sessions/{chat_session_id}", response_model=ChatSessionResponse)
def get_chat_session(chat_session_id: str, request: Request) -> ChatSessionResponse:
    try:
        return ChatSessionResponse.model_validate(
            task_service(request).get_chat_session(chat_session_id)
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
        tasks = task_service(request).list_chat_session_tasks(chat_session_id, limit=limit)
    except ChatSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error
    return task_list_with_progress(request, tasks)


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
        messages = task_service(request).list_chat_messages(chat_session_id, limit=limit)
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
    private = private_memory_text(body.content)
    if private is not None:
        return _remember_privately(request, chat_session_id, private)
    try:
        posted = task_service(request).append_chat_message(
            chat_session_id,
            content=body.content,
            blocks=[
                {"type": "attachment", **attachment.model_dump()} for attachment in body.attachments
            ]
            or None,
        )
    except ChatSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return AppendChatMessageResponse(
        message=ChatMessageResponse.from_model(posted.message),
        proposal=(
            TaskProposalResponse.model_validate(posted.proposal.as_dict())
            if posted.proposal is not None
            else None
        ),
        focused_work_items=posted.focused_work_items,
        decision=triage_message(request, chat_session_id, body, posted.message.id),
    )


class ChatUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    #: The file's text (CSV and the like); a few years of purchases is well under a megabyte.
    content: str = Field(min_length=1, max_length=MAX_UPLOAD_CHARACTERS)


@router.post("/chat-sessions/{chat_session_id}/messages/{message_id}/uploads/{block_id}")
def upload_to_chat(
    chat_session_id: str,
    message_id: str,
    block_id: str,
    body: ChatUploadRequest,
    request: Request,
) -> dict:
    """A file handed over with an upload button: its target's handler replies in the chat."""
    service = task_service(request)
    try:
        block = service.open_upload(chat_session_id, message_id, block_id)
    except ChatMessageNotFoundError as error:
        raise HTTPException(status_code=404, detail="Upload not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    target = TARGETS.get(block["target"])
    if target is None:
        raise HTTPException(status_code=409, detail="This kind of upload isn't supported")
    try:
        reply = target.handle(request.app.state, body.content)
    except UploadError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    service.complete_upload(message_id, block_id, reply.split(".")[0])
    service.append_chat_message(chat_session_id, content=f"📎 {body.filename}")
    service.append_chat_message(chat_session_id, content=reply, role="assistant")
    return {"reply": reply}


class SharedFileRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=MAX_UPLOAD_CHARACTERS)


@router.post("/chat-sessions/{chat_session_id}/files")
def share_file_to_chat(chat_session_id: str, body: SharedFileRequest, request: Request) -> dict:
    """A file shared from another app: the upload target that recognizes it handles it."""
    service = task_service(request)
    target = detect_target(body.content)
    try:
        service.append_chat_message(chat_session_id, content=f"📎 {body.filename}")
    except ChatSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error
    if target is None:
        reply = (
            "I can't use this kind of file yet. Right now I can read Klarna purchase exports (CSV)."
        )
    else:
        try:
            reply = target.handle(request.app.state, body.content)
        except UploadError as error:
            reply = f"I couldn't read that file: {error}"
    service.append_chat_message(chat_session_id, content=reply, role="assistant")
    return {"reply": reply, "target": target.id if target else None}


PRIVATE_PLACEHOLDER = "🔒 A private memory (kept out of the chat)"
PRIVATE_REPLY = (
    "Saved privately. It stays on your server and is never sent to an AI model; "
    "you'll find it under Memories."
)


def _remember_privately(
    request: Request, chat_session_id: str, text: str
) -> AppendChatMessageResponse:
    """ "Remember privately …": saved before any model sees it, and kept out of the chat.

    The transcript (which later turns pass to models as history) only gets a placeholder.
    """
    service = task_service(request)
    try:
        posted = service.append_chat_message(chat_session_id, content=PRIVATE_PLACEHOLDER)
    except ChatSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error
    request.app.state.memory_service.create(
        kind="fact", content=text, private=True, chat_session_id=chat_session_id
    )
    service.append_chat_message(chat_session_id, content=PRIVATE_REPLY, role="assistant")
    return AppendChatMessageResponse(
        message=ChatMessageResponse.from_model(posted.message),
        proposal=None,
        focused_work_items=[],
        decision=TriageDecisionResponse(
            intent="direct_action",
            goal="",
            reason="Saved as a private memory",
            confidence=1.0,
            result=PRIVATE_REPLY,
            handled=True,
            source="rules",
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
    reject_direct_coding(body.required_capabilities, body.repository_id, body.source_context)
    try:
        task = task_service(request).create_task_from_message(
            chat_session_id,
            message_id,
            goal=body.goal,
            required_capabilities=body.required_capabilities,
            source_context=repository_context(request, body.source_context, body.repository_id),
            work_item_id=body.work_item_id,
            create_work_item=body.create_work_item,
            space=(
                body.space
                if body.space and request.app.state.space_registry.get(body.space)
                else None
            ),
        )
    except ChatSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error
    except ChatMessageNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat message not found") from error
    except WorkItemNotFoundError as error:
        raise HTTPException(status_code=404, detail="Work item not found") from error
    except MessageHasWorkflowError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
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
        chat_session = task_service(request).ensure_task_chat_session(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error
    return ChatSessionResponse.model_validate(chat_session)


@router.post(
    "/chat-sessions/{chat_session_id}/messages/{message_id}/workflow-run",
    response_model=WorkflowRunResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_coding_workflow_from_message(
    chat_session_id: str,
    message_id: str,
    body: CreateCodingWorkflowFromMessageRequest,
    request: Request,
) -> WorkflowRunResponse:
    try:
        run = propose_coding_run(request, chat_session_id, message_id, body.repository_id)
    except ChatSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error
    except ChatMessageNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat message not found") from error
    except (LookupError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return WorkflowRunResponse.from_model(run)
