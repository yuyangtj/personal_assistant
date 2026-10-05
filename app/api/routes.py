from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
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
    CodingRunnerListResponse,
    CreateChatSessionRequest,
    CreateCodingWorkflowFromMessageRequest,
    CreateDeploymentWorkflowRequest,
    CreateMemoryRequest,
    CreateTaskFromMessageRequest,
    CreateTaskRequest,
    CreateWorkflowRunRequest,
    DeploymentTargetListResponse,
    EventListResponse,
    EventResponse,
    FollowUpTaskRequest,
    HealthResponse,
    MemoryListResponse,
    MemoryResponse,
    PendingApprovalResponse,
    ProviderRuntimeStateListResponse,
    ProviderRuntimeStateResponse,
    PullRequestApprovalRequest,
    PullRequestCheckResponse,
    PullRequestReadyRequest,
    PullRequestRevisionRequest,
    PullRequestStatusResponse,
    RepositoryListResponse,
    RepositoryResponse,
    SpeechRequest,
    TaskContextResponse,
    TaskListResponse,
    TaskProposalResponse,
    TaskResponse,
    TriageDecisionResponse,
    ValidationProfileListResponse,
    WorkflowDecisionRequest,
    WorkflowListResponse,
    WorkflowRunEventListResponse,
    WorkflowRunEventResponse,
    WorkflowRunListResponse,
    WorkflowRunResponse,
)
from app.capabilities.models import CapabilityKind
from app.coding_flow import start_coding_run, start_coding_task
from app.deployments import HostDeployerSpoolError
from app.direct_actions import run_direct_action
from app.domain.enums import CODING_CAPABILITIES, TaskStatus
from app.domain.transitions import TERMINAL_STATUSES
from app.integrations.chat import ProviderError
from app.integrations.github import GitHubError
from app.offers import match_choice, offer_for, pending_choices, settle
from app.schedules import zone
from app.service import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ChatMessageNotFoundError,
    ChatSessionNotFoundError,
    MessageHasWorkflowError,
    TaskNotFoundError,
    TaskService,
)
from app.triage import TriageIntent
from app.work_items import WorkItemNotFoundError
from app.workflows import (
    WorkflowIntegrationUnavailableError,
    WorkflowRunConflictError,
    WorkflowRunNotFoundError,
)

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


def _propose_coding_run(
    request: Request,
    chat_session_id: str,
    message_id: str,
    repository_id: str,
    *,
    goal: str | None = None,
):
    """A proposed coding workflow for a user message; nothing runs until it is approved."""
    message = _service(request).get_user_chat_message(chat_session_id, message_id)
    repository = request.app.state.repository_registry.get(repository_id)
    request_text = message.content
    if goal and goal.strip() and goal.strip() != message.content:
        request_text = f"{goal.strip()}\n\nOriginal request: {message.content}"
    return request.app.state.workflow_service.create_run(
        workflow_id="coding-change",
        workflow_input={"repository_id": repository.id, "request": request_text},
        chat_session_id=chat_session_id,
        origin_message_id=message.id,
    )


def _resolve_choice(
    request: Request, chat_session_id: str, option: dict, body: AppendChatMessageRequest
) -> TriageDecisionResponse:
    """Carry out the option the user picked from an assistant's choices."""
    service = _service(request)
    payload = option.get("payload") or {}
    origin = payload.get("origin_message_id")
    # The same context the console sends with a chat turn, so times resolve locally.
    source_context = {
        key: value
        for key, value in {
            "client": "chat-choice",
            "local_time": body.local_time,
            "timezone": body.timezone,
        }.items()
        if value
    }
    action = option["action"]
    reply: str | None = None
    blocks: list[dict] = []
    try:
        if action == "start_coding":
            # Agreeing in the chat is enough to start: the agent only works on a branch and
            # opens a draft pull request; merging still needs the approval token.
            message = service.get_user_chat_message(chat_session_id, origin)
            goal = str(payload.get("goal") or "").strip()
            run = start_coding_run(
                service,
                request.app.state.workflow_service,
                request.app.state.repository_registry,
                repository_id=payload["repository_id"],
                request=(
                    f"{goal}\n\nOriginal request: {message.content}"
                    if goal and goal != message.content
                    else message.content
                ),
                chat_session_id=chat_session_id,
                origin_message_id=origin,
                approved_by="chat",
            )
            reply = "Started a coding agent on it. I'll report back here."
            blocks = [{"type": "workflow", "workflow_run_id": run.id}]
        elif action in ("start_research", "start_task"):
            task = service.create_task_from_message(
                chat_session_id,
                origin,
                goal=payload.get("goal"),
                required_capabilities=list(payload.get("capabilities") or []),
                source_context=source_context,
                create_work_item=True,
                space=payload.get("space"),
            )
            reply = "Researching it now." if action == "start_research" else "Started on it."
            if task.work_item_id:
                item = request.app.state.work_item_service.get(task.work_item_id)
                blocks = [{"type": "item", "slug": item.slug}]
        else:  # answer: the original message gets an ordinary chat reply
            service.create_task_from_message(
                chat_session_id, origin, source_context=source_context
            )
    except (LookupError, ValueError) as error:
        reply, blocks = f"I couldn't set that up: {error}", []
    if reply:
        service.append_chat_message(chat_session_id, content=reply, role="assistant", blocks=blocks)
    return TriageDecisionResponse(
        intent="direct_action" if action != "answer" else "answer",
        goal=str(payload.get("goal") or ""),
        reason=f"Picked “{option['label']}”",
        confidence=1.0,
        result=reply,
        handled=True,
        source="rules",
    )


#: A coding request this clear starts work right away; less clear ones are offered.
SUPERVISOR_CONFIDENCE = 0.85


def _triage(
    request: Request, chat_session_id: str, body: AppendChatMessageRequest, message_id: str
) -> TriageDecisionResponse:
    """Decide what a just-stored message needs; never raises (rules are the fallback)."""
    service = _service(request)
    messages = service.list_chat_messages(chat_session_id, limit=500)
    # An answer to the assistant's open choices (a click sends the label; "yes" or "no"
    # pick the primary or decline option). Anything else lets the choices lapse.
    pending = pending_choices(messages[-10:], current_message_id=message_id)
    if pending is not None:
        offer_message, block = pending
        option = match_choice(body.content, block)
        service.set_message_blocks(
            offer_message.id,
            settle(offer_message.blocks or [], block["id"], chosen=option and option["label"]),
        )
        if option is not None and option["action"] != "reply":
            return _resolve_choice(request, chat_session_id, option, body)
    work_items = request.app.state.work_item_service
    focused = work_items.focused(chat_session_id)
    spaces = work_items.space_slugs(focused)
    history = messages[-7:-1]
    open_items = [
        {"slug": item.slug, "title": item.title, "kind": item.kind}
        for item in work_items.list(limit=50)
        if item.status != "done"
    ]
    decision = request.app.state.triager.decide(
        body.content,
        selected_repository_id=body.repository_id,
        focused_items=[
            {"slug": item.slug, "title": item.title, "space": spaces.get(item.space_id)}
            for item in focused
        ],
        recent_turns=[
            (message.role, message.content)
            for message in history
            if message.role in ("user", "assistant")
        ],
        work_items=open_items,
        local_time=body.local_time,
        timezone=body.timezone,
    )
    response = TriageDecisionResponse.model_validate(decision.model_dump(mode="json"))
    if decision.action is not None:
        # T0: done now, without a chat model; the reply lands in the transcript.
        timezone = body.timezone or request.app.state.settings.default_timezone
        try:
            zone(timezone)
        except ValueError:
            timezone = request.app.state.settings.default_timezone
        result = run_direct_action(
            decision.action,
            chat_session_id=chat_session_id,
            items=work_items,
            memory=request.app.state.memory_service,
            schedules=request.app.state.schedule_service,
            timezone=timezone,
        )
        service.append_chat_message(chat_session_id, content=result.reply, role="assistant")
        response.result = result.reply
        response.handled = True
        return response
    # Work goes to the supervisor, which sees every run and can start, inspect or stop
    # them: a clear request for a code change, or any message about coding work.
    if request.app.state.settings.supervisor_enabled and (
        decision.about_work
        or (
            decision.intent == TriageIntent.PROPOSE_CODING
            and decision.repository_id
            and decision.confidence >= SUPERVISOR_CONFIDENCE
        )
    ):
        service.create_task_from_message(
            chat_session_id,
            message_id,
            required_capabilities=["supervision"],
            source_context={
                key: value
                for key, value in {
                    "client": "web-console",
                    "local_time": body.local_time,
                    "timezone": body.timezone,
                }.items()
                if value
            },
        )
        response.handled = True
        return response
    # Proposals are asked in the conversation, with the options as choices to pick.
    offer = offer_for(
        decision,
        origin_message_id=message_id,
        repository_names={
            manifest.id: manifest.name for manifest in request.app.state.repository_registry.list()
        },
    )
    if offer is not None:
        service.append_chat_message(
            chat_session_id, content=offer.reply, role="assistant", blocks=offer.blocks
        )
        response.result = offer.reply
        response.handled = True
    return response


def _task_list_with_progress(request: Request, tasks: list) -> TaskListResponse:
    """Tasks with what their agents last reported, for live cards."""
    progress = _service(request).task_progress([task.id for task in tasks if active_status(task)])
    return TaskListResponse(
        tasks=[TaskResponse.from_model(task, progress.get(task.id)) for task in tasks]
    )


def active_status(task) -> bool:
    return TaskStatus(task.status) not in TERMINAL_STATUSES


CODING_WORKFLOW_REQUIRED = (
    "Coding work starts from a coding workflow (propose → approve → start), "
    "not from a plain task"
)


def _reject_direct_coding(
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


def _live_pull_request_status(
    request: Request,
    task_id: str,
    approval: dict[str, object],
    *,
    include_review_threads: bool = True,
) -> PullRequestStatusResponse:
    github = request.app.state.github_client
    if github is None:
        raise HTTPException(status_code=503, detail="GitHub integration is not configured")
    task = _service(request).get_task(task_id)
    repository_id = str((task.source_context or {}).get("repository_id") or "")
    if not repository_id:
        raise ApprovalConflictError("Task has no trusted repository id")
    manifest = request.app.state.repository_registry.get(repository_id)
    repository = str(approval["repository"])
    if repository != manifest.github_repository:
        raise ApprovalConflictError("Approval repository does not match the trusted registry")
    number = int(approval["number"])
    expected_sha = str(approval["expected_head_sha"])
    pull_request = github.get_pull_request(repository=repository, number=number)
    if (
        pull_request.repository != repository
        or pull_request.head_branch != str(approval.get("head_branch") or "")
        or pull_request.base_branch != manifest.base_branch
    ):
        raise ApprovalConflictError("Pull request identity does not match the trusted approval")
    check_runs = github.list_check_runs(repository=repository, commit_sha=pull_request.head_sha)
    required = set(manifest.required_checks)
    by_name = {}
    for check in check_runs:
        by_name.setdefault(check.name, check)
    missing = required - set(by_name)
    if not required or missing:
        aggregate = "missing"
    elif any(by_name[name].status != "completed" for name in required):
        aggregate = "pending"
    elif any(
        by_name[name].conclusion not in {"success", "neutral", "skipped"} for name in required
    ):
        aggregate = "failed"
    else:
        aggregate = "passed"
    unresolved = (
        github.list_unresolved_review_comments(repository=repository, number=number)
        if include_review_threads
        else ()
    )
    return PullRequestStatusResponse(
        repository=repository,
        number=number,
        url=pull_request.url,
        expected_head_sha=expected_sha,
        current_head_sha=pull_request.head_sha,
        head_matches=pull_request.head_sha == expected_sha,
        state=pull_request.state,
        draft=pull_request.draft,
        mergeable=pull_request.mergeable,
        required_checks_state=aggregate,
        checks=[
            PullRequestCheckResponse(
                name=check.name,
                status=check.status,
                conclusion=check.conclusion,
                url=check.url,
                required=check.name in required,
            )
            for check in check_runs
        ],
        unresolved_thread_count=len(unresolved),
    )


def _require_mergeable_status(status: PullRequestStatusResponse) -> None:
    if not status.head_matches:
        raise ApprovalConflictError(
            "Pull request changed; review the new head SHA before continuing"
        )
    if status.state != "open":
        raise ApprovalConflictError("Pull request is not open")
    if status.mergeable is not True:
        raise ApprovalConflictError("Pull request is not confirmed mergeable")
    if status.required_checks_state != "passed":
        raise ApprovalConflictError(f"Required GitHub checks are {status.required_checks_state}")


def _bounded_review_feedback(comments) -> list[dict[str, object]]:
    remaining = 12_000
    feedback: list[dict[str, object]] = []
    for comment in comments:
        if remaining <= 0:
            break
        body = comment.body[: min(2000, remaining)]
        remaining -= len(body)
        feedback.append(
            {
                "author": comment.author,
                "body": body,
                "path": comment.path,
                "line": comment.line,
                "url": comment.url,
            }
        )
    return feedback


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
    return _task_list_with_progress(request, tasks)


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
        posted = _service(request).append_chat_message(
            chat_session_id,
            content=body.content,
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
        decision=_triage(request, chat_session_id, body, posted.message.id),
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
    _reject_direct_coding(body.required_capabilities, body.repository_id, body.source_context)
    try:
        task = _service(request).create_task_from_message(
            chat_session_id,
            message_id,
            goal=body.goal,
            required_capabilities=body.required_capabilities,
            source_context=_repository_context(request, body.source_context, body.repository_id),
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
            task = service.reject_approval(task_id)
            request.app.state.workflow_service.sync_for_task(task_id)
            return TaskResponse.from_model(task)
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
        pending = service.get_pending_approval(task_id)
        if pending.get("expected_head_sha") != body.expected_head_sha:
            raise ApprovalConflictError("Reviewed head SHA does not match the pending approval")
        live_status = _live_pull_request_status(
            request, task_id, pending, include_review_threads=False
        )
        service.record_pull_request_status(
            task_id,
            operation="merge_preflight",
            snapshot=live_status.model_dump(mode="json"),
        )
        if live_status.head_matches:
            _require_mergeable_status(live_status)
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
    except GitHubError as error:
        raise HTTPException(status_code=502, detail="GitHub operation failed") from error

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
            task = service.complete_pull_request_merge(
                task_id,
                execution_id=execution_id,
                repository=repository,
                number=number,
                url=pull_request.url,
                merge_sha=pull_request.merge_commit_sha,
            )
            request.app.state.workflow_service.sync_for_task(task_id)
            return TaskResponse.from_model(task)
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
        decisive_status = _live_pull_request_status(
            request, task_id, approval, include_review_threads=False
        )
        service.record_pull_request_status(
            task_id,
            operation="merge_decisive_preflight",
            snapshot=decisive_status.model_dump(mode="json"),
        )
        _require_mergeable_status(decisive_status)
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
        task = service.complete_pull_request_merge(
            task_id,
            execution_id=execution_id,
            repository=repository,
            number=number,
            url=pull_request.url,
            merge_sha=result.sha,
        )
        request.app.state.workflow_service.sync_for_task(task_id)
        return TaskResponse.from_model(task)
    except ApprovalConflictError as error:
        service.return_to_pull_request_approval(task_id, reason="merge_preflight_changed")
        raise HTTPException(status_code=409, detail=str(error)) from error
    except HTTPException:
        raise
    except (GitHubError, KeyError, TypeError, ValueError) as error:
        service.return_to_pull_request_approval(task_id, reason="github_request_failed")
        raise HTTPException(status_code=502, detail="GitHub operation failed") from error


@router.post(
    "/tasks/{task_id}/pull-request-ready",
    response_model=PendingApprovalResponse,
)
def mark_pull_request_ready(
    task_id: str,
    body: PullRequestReadyRequest,
    request: Request,
) -> PendingApprovalResponse:
    _require_approval_authorization(request)
    service = _service(request)
    github = request.app.state.github_client
    if github is None:
        raise HTTPException(status_code=503, detail="GitHub integration is not configured")
    try:
        approval = service.get_pending_approval(task_id)
        if approval.get("type") != "github_pull_request_merge":
            raise ApprovalNotFoundError(task_id)
        if approval.get("expected_head_sha") != body.expected_head_sha:
            raise ApprovalConflictError("Reviewed head SHA does not match the pending approval")
        live_status = _live_pull_request_status(
            request, task_id, approval, include_review_threads=False
        )
        service.record_pull_request_status(
            task_id,
            operation="ready_for_review_preflight",
            snapshot=live_status.model_dump(mode="json"),
        )
        _require_mergeable_status(live_status)
        repository = str(approval["repository"])
        number = int(approval["number"])
        pull_request = github.get_pull_request(repository=repository, number=number)
        if pull_request.head_sha != body.expected_head_sha:
            raise ApprovalConflictError(
                "Pull request changed; review the new head SHA before marking it ready"
            )
        if pull_request.state != "open" or pull_request.merged:
            raise ApprovalConflictError("Pull request is not open")
        service.record_pull_request_ready_call(
            task_id,
            repository=repository,
            number=number,
            expected_head_sha=body.expected_head_sha,
        )
        if pull_request.draft:
            if not pull_request.node_id:
                raise GitHubError("GitHub did not return the pull request node id")
            if not github.mark_pull_request_ready_for_review(node_id=pull_request.node_id):
                raise GitHubError("GitHub did not mark the pull request ready for review")
        refreshed = service.record_pull_request_ready_result(task_id, ok=True)
        if refreshed is None:
            raise ApprovalConflictError("Pull request approval is no longer active")
        return PendingApprovalResponse.model_validate(refreshed)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error
    except ApprovalNotFoundError as error:
        raise HTTPException(status_code=404, detail="Pull request approval not found") from error
    except ApprovalConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (GitHubError, KeyError, TypeError, ValueError) as error:
        service.record_pull_request_ready_result(
            task_id,
            ok=False,
            reason="github_request_failed",
        )
        raise HTTPException(status_code=502, detail="GitHub operation failed") from error


@router.get(
    "/tasks/{task_id}/pull-request-status",
    response_model=PullRequestStatusResponse,
)
def get_pull_request_status(task_id: str, request: Request) -> PullRequestStatusResponse:
    try:
        approval = _service(request).get_pending_approval(task_id)
        return _live_pull_request_status(request, task_id, approval)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error
    except ApprovalNotFoundError as error:
        raise HTTPException(status_code=404, detail="Pull request approval not found") from error
    except (ApprovalConflictError, LookupError, KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except GitHubError as error:
        raise HTTPException(status_code=502, detail="GitHub operation failed") from error


@router.post(
    "/tasks/{task_id}/pull-request-revision",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
def request_pull_request_revision(
    task_id: str, body: PullRequestRevisionRequest, request: Request
) -> TaskResponse:
    _require_approval_authorization(request)
    service = _service(request)
    try:
        parent = service.get_task(task_id)
        approval = service.get_pending_approval(task_id)
        if approval.get("expected_head_sha") != body.expected_head_sha:
            raise ApprovalConflictError("Reviewed head SHA does not match the pending approval")
        live_status = _live_pull_request_status(
            request, task_id, approval, include_review_threads=False
        )
        if not live_status.head_matches:
            raise ApprovalConflictError(
                "Pull request changed; review the new head SHA before requesting revision"
            )
        if live_status.state != "open":
            raise ApprovalConflictError("Pull request is not open")
        repository_id = str((parent.source_context or {}).get("repository_id") or "")
        if not repository_id:
            raise ApprovalConflictError("The original task has no trusted repository id")
        github = request.app.state.github_client
        if github is None:
            raise HTTPException(status_code=503, detail="GitHub integration is not configured")
        comments = github.list_unresolved_review_comments(
            repository=str(approval["repository"]), number=int(approval["number"])
        )
        revision_context = {
            "repository_id": repository_id,
            "revision_pull_request": {
                "number": int(approval["number"]),
                "head_branch": str(approval.get("head_branch") or ""),
                "expected_head_sha": body.expected_head_sha,
                "review_feedback": _bounded_review_feedback(comments),
            },
        }
        if not revision_context["revision_pull_request"]["head_branch"]:
            raise ApprovalConflictError("Pending approval has no pull request branch")
        task = service.create_follow_up_task(
            task_id,
            request=body.instructions,
            required_capabilities=["coding-pull-request"],
            source_context=revision_context,
        )
        # The child now owns the PR. Retire the old exact-SHA approval so two
        # independently actionable merge gates cannot exist for one branch.
        service.supersede_pull_request_approval(task_id, revision_task_id=task.id)
        return TaskResponse.from_model(task)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error
    except ApprovalNotFoundError as error:
        raise HTTPException(status_code=404, detail="Pull request approval not found") from error
    except (ApprovalConflictError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except GitHubError as error:
        raise HTTPException(status_code=502, detail="GitHub operation failed") from error


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(body: CreateTaskRequest, request: Request) -> TaskResponse:
    _reject_direct_coding(body.required_capabilities, body.repository_id, body.source_context)
    try:
        task = _service(request).create_task(
            request=body.request,
            goal=body.goal,
            required_capabilities=body.required_capabilities,
            source_context=_repository_context(request, body.source_context, body.repository_id),
            chat_session_id=body.chat_session_id,
            external_source=body.external_source,
            external_key=body.external_key,
            work_item_id=body.work_item_id,
        )
    except ChatSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error
    except WorkItemNotFoundError as error:
        raise HTTPException(status_code=404, detail="Work item not found") from error
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
    response_model_exclude_none=True,
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
        task = _service(request).get_task(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error
    return TaskResponse.from_model(task, _service(request).task_progress([task.id]).get(task.id))


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
    _reject_direct_coding(body.required_capabilities, body.repository_id, body.source_context)
    try:
        task = _service(request).create_follow_up_task(
            task_id,
            request=body.request,
            goal=body.goal,
            required_capabilities=body.required_capabilities,
            source_context=_repository_context(request, body.source_context, body.repository_id),
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
        task = _service(request).cancel_task(task_id)
        request.app.state.workflow_service.sync_for_task(task_id)
        return TaskResponse.from_model(task)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error


@router.get("/ui", include_in_schema=False)
def web_console() -> FileResponse:
    """A local console for exercising the chat and task flow from a browser."""
    if not WEB_INDEX.is_file():
        raise HTTPException(status_code=404, detail="Web console is not installed")
    return FileResponse(WEB_INDEX, media_type="text/html")


WEB_DIRECTORY = WEB_INDEX.parent
#: Served without the console login: the installed app needs them before signing in, and
#: Android builds the installed app by fetching the manifest and icons from Google's side.
WEB_ICONS = {
    "icon-192.png",
    "icon-512.png",
    "maskable-512.png",
    "apple-touch-icon.png",
    "favicon-48.png",
}
WEB_MANIFEST = {
    "id": "/ui",
    "name": "Personal Assistant",
    "short_name": "Assistant",
    "description": "Talk to your assistant, follow its work, and approve what it does.",
    "start_url": "/ui#/chats",
    "scope": "/",
    "display": "standalone",
    "background_color": "#071116",
    "theme_color": "#0d8a7e",
    "icons": [
        {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {
            "src": "/icons/maskable-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ],
    "shortcuts": [
        {"name": "Chats", "url": "/ui#/chats"},
        {"name": "Work items", "url": "/ui#/items"},
    ],
}


@router.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest() -> JSONResponse:
    return JSONResponse(WEB_MANIFEST, media_type="application/manifest+json")


@router.get("/sw.js", include_in_schema=False)
def web_service_worker() -> FileResponse:
    # Never cached by the browser, so a new service worker is picked up on the next visit.
    return FileResponse(
        WEB_DIRECTORY / "sw.js",
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/icons/{name}", include_in_schema=False)
def web_icon(name: str) -> FileResponse:
    if name not in WEB_ICONS:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        WEB_DIRECTORY / "icons" / name,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/", include_in_schema=False)
def web_console_root(request: Request) -> RedirectResponse:
    # Keep the query: push notifications link to /?task=<id>.
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(url=f"/ui{query}", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


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
                required_checks=list(manifest.required_checks),
            )
        )
    return RepositoryListResponse(repositories=repositories)


@router.get("/validation-profiles", response_model=ValidationProfileListResponse)
def list_validation_profiles(request: Request) -> ValidationProfileListResponse:
    return ValidationProfileListResponse(
        profiles=request.app.state.validation_profile_registry.list()
    )


@router.get("/deployment-targets", response_model=DeploymentTargetListResponse)
def list_deployment_targets(request: Request) -> DeploymentTargetListResponse:
    return DeploymentTargetListResponse(targets=request.app.state.deployment_registry.list())


@router.post("/memories", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
def create_memory(body: CreateMemoryRequest, request: Request) -> MemoryResponse:
    try:
        memory = request.app.state.memory_service.create(
            kind=body.kind,
            content=body.content,
            tags=body.tags,
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


@router.delete("/memories/{memory_id}", response_model=MemoryResponse)
def archive_memory(memory_id: str, request: Request) -> MemoryResponse:
    try:
        return MemoryResponse.model_validate(request.app.state.memory_service.archive(memory_id))
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Memory not found") from error


@router.get("/provider-states", response_model=ProviderRuntimeStateListResponse)
def list_provider_states(request: Request) -> ProviderRuntimeStateListResponse:
    store = request.app.state.provider_state_store
    providers = [
        ProviderRuntimeStateResponse(
            provider_key=state.provider_key,
            available=store.is_available(state.provider_key),
            cooldown_until=state.cooldown_until,
            last_error_category=state.last_error_category,
            consecutive_failures=state.consecutive_failures,
            total_successes=state.total_successes,
            total_failures=state.total_failures,
            last_success_at=state.last_success_at,
            last_failure_at=state.last_failure_at,
            updated_at=state.updated_at,
        )
        for state in store.list()
    ]
    return ProviderRuntimeStateListResponse(providers=providers)


@router.get("/coding-runners", response_model=CodingRunnerListResponse)
def list_coding_runners(request: Request) -> CodingRunnerListResponse:
    return CodingRunnerListResponse(runners=request.app.state.coding_runner_registry.list())


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
        run = _propose_coding_run(request, chat_session_id, message_id, body.repository_id)
    except ChatSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat session not found") from error
    except ChatMessageNotFoundError as error:
        raise HTTPException(status_code=404, detail="Chat message not found") from error
    except (LookupError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return WorkflowRunResponse.from_model(run)


@router.post(
    "/tasks/{task_id}/deployment-workflow",
    response_model=WorkflowRunResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_deployment_workflow_for_task(
    task_id: str,
    body: CreateDeploymentWorkflowRequest,
    request: Request,
) -> WorkflowRunResponse:
    try:
        task = _service(request).get_task(task_id)
        if task.status != TaskStatus.COMPLETED.value:
            raise ValueError("Only a completed merged task can be deployed")
        target = request.app.state.deployment_registry.get(body.deployment_target_id)
        repository_id = str((task.source_context or {}).get("repository_id") or "")
        if repository_id != target.repository_id:
            raise ValueError("Deployment target does not match the task repository")
        context = _service(request).task_context(task_id)
        merge_artifact = next(
            (
                artifact
                for artifact in reversed(context.get("artifacts") or [])
                if artifact.get("type") == "github_pull_request_merge"
                and artifact.get("merge_sha")
            ),
            None,
        )
        if merge_artifact is None:
            raise ValueError("Task has no trusted merged commit artifact")
        run = request.app.state.workflow_service.create_run(
            workflow_id="assistant-deployment",
            workflow_input={
                "deployment_target_id": target.id,
                "commit_sha": str(merge_artifact["merge_sha"]),
            },
            chat_session_id=task.chat_session_id,
            task_id=task.id,
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error
    except WorkflowRunConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (LookupError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return WorkflowRunResponse.from_model(run)


@router.get(
    "/tasks/{task_id}/deployment-workflow",
    response_model=WorkflowRunResponse,
)
def get_deployment_workflow_for_task(
    task_id: str,
    request: Request,
) -> WorkflowRunResponse:
    try:
        _service(request).get_task(task_id)
        run = request.app.state.workflow_service.get_deployment_for_task(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error
    if run is None:
        raise HTTPException(status_code=404, detail="Deployment workflow not found")
    return WorkflowRunResponse.from_model(run)


@router.get("/workflow-runs", response_model=WorkflowRunListResponse)
def list_workflow_runs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    chat_session_id: str | None = None,
) -> WorkflowRunListResponse:
    runs = request.app.state.workflow_service.list_runs(
        limit=limit,
        chat_session_id=chat_session_id,
    )
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


@router.post(
    "/workflow-runs/{workflow_run_id}/start",
    response_model=WorkflowRunResponse,
)
def start_workflow_run(workflow_run_id: str, request: Request) -> WorkflowRunResponse:
    workflow_service = request.app.state.workflow_service
    try:
        run = workflow_service.get_run(workflow_run_id)
        if run.workflow_id == "assistant-deployment":
            github = request.app.state.github_client
            if github is None:
                raise HTTPException(
                    status_code=503,
                    detail="GitHub integration is not configured",
                )
            return WorkflowRunResponse.from_model(
                workflow_service.start_deployment(run.id, github=github)
            )
        if run.workflow_id != "coding-change":
            raise WorkflowRunConflictError(
                f"Workflow {run.workflow_id} has no trusted execution adapter"
            )
        return WorkflowRunResponse.from_model(
            start_coding_task(_service(request), workflow_service, run)
        )
    except WorkflowRunNotFoundError as error:
        raise HTTPException(status_code=404, detail="Workflow run not found") from error
    except (GitHubError, HostDeployerSpoolError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except (KeyError, ValueError, WorkflowRunConflictError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/workflow-runs/{workflow_run_id}/sync",
    response_model=WorkflowRunResponse,
)
def sync_workflow_run(workflow_run_id: str, request: Request) -> WorkflowRunResponse:
    try:
        workflow_service = request.app.state.workflow_service
        run = workflow_service.get_run(workflow_run_id)
        if run.workflow_id == "assistant-deployment":
            run = workflow_service.sync_deployment(
                workflow_run_id,
                github=request.app.state.github_client,
            )
        else:
            run = workflow_service.sync_run(workflow_run_id)
    except WorkflowRunNotFoundError as error:
        raise HTTPException(status_code=404, detail="Workflow run not found") from error
    except WorkflowRunConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except WorkflowIntegrationUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (GitHubError, HostDeployerSpoolError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return WorkflowRunResponse.from_model(run)
