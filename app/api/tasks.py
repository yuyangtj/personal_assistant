"""Tasks: creating and following them, their context and events, and the pull-request gates."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.common import (
    reject_direct_coding,
    repository_context,
    require_approval_token,
    task_service,
)
from app.api.schemas import (
    AddMessageRequest,
    CreateTaskRequest,
    EventListResponse,
    EventResponse,
    FollowUpTaskRequest,
    PendingApprovalResponse,
    PullRequestApprovalRequest,
    PullRequestCheckResponse,
    PullRequestReadyRequest,
    PullRequestRevisionRequest,
    PullRequestStatusResponse,
    TaskContextResponse,
    TaskListResponse,
    TaskResponse,
)
from app.coding.reconcile import PullRequestReconciler
from app.domain.enums import TaskStatus
from app.integrations.github import GitHubError
from app.services import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ChatSessionNotFoundError,
    TaskNotFoundError,
)
from app.work.items import WorkItemNotFoundError

router = APIRouter()


def _live_pull_request_status(
    request: Request,
    task_id: str,
    approval: dict[str, object],
    *,
    include_review_threads: bool = True,
    settle: bool = False,
) -> PullRequestStatusResponse:
    github = request.app.state.github_client
    if github is None:
        raise HTTPException(status_code=503, detail="GitHub integration is not configured")
    task = task_service(request).get_task(task_id)
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
    if settle and (pull_request.merged or pull_request.state == "closed"):
        # Merged or closed on GitHub directly: close the gate now instead of leaving the
        # console offering buttons for a PR that is already done.
        PullRequestReconciler(
            task_service(request),
            github,
            on_change=request.app.state.workflow_service.sync_for_task,
        ).settle_task(task_id, pull_request)
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
        merged=pull_request.merged,
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
    "/tasks/{task_id}/pull-request-approval",
    response_model=TaskResponse,
)
def decide_pull_request_approval(
    task_id: str,
    body: PullRequestApprovalRequest,
    request: Request,
) -> TaskResponse:
    require_approval_token(request)
    service = task_service(request)
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
    require_approval_token(request)
    service = task_service(request)
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
        approval = task_service(request).get_pending_approval(task_id)
        return _live_pull_request_status(request, task_id, approval, settle=True)
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
    require_approval_token(request)
    service = task_service(request)
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
            required_capabilities=["coding", "pull_request_creation"],
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
    reject_direct_coding(body.required_capabilities, body.repository_id, body.source_context)
    try:
        task = task_service(request).create_task(
            request=body.request,
            goal=body.goal,
            required_capabilities=body.required_capabilities,
            source_context=repository_context(request, body.source_context, body.repository_id),
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
    tasks = task_service(request).list_tasks(status=task_status, limit=limit)
    return TaskListResponse(tasks=[TaskResponse.from_model(task) for task in tasks])


@router.get(
    "/tasks/{task_id}/pending-approval",
    response_model=PendingApprovalResponse,
    response_model_exclude_none=True,
)
def get_pending_approval(task_id: str, request: Request) -> PendingApprovalResponse:
    try:
        approval = task_service(request).get_pending_approval(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error
    except ApprovalNotFoundError as error:
        raise HTTPException(status_code=404, detail="Pending approval not found") from error
    return PendingApprovalResponse.model_validate(approval)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: str, request: Request) -> TaskResponse:
    try:
        task = task_service(request).get_task(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error
    return TaskResponse.from_model(
        task, task_service(request).task_progress([task.id]).get(task.id)
    )


@router.get("/tasks/{task_id}/context", response_model=TaskContextResponse)
def get_task_context(task_id: str, request: Request) -> TaskContextResponse:
    """The curated summary a follow-up conversation is given, without the event stream."""
    try:
        return TaskContextResponse.model_validate(task_service(request).task_context(task_id))
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
    reject_direct_coding(body.required_capabilities, body.repository_id, body.source_context)
    try:
        task = task_service(request).create_follow_up_task(
            task_id,
            request=body.request,
            goal=body.goal,
            required_capabilities=body.required_capabilities,
            source_context=repository_context(request, body.source_context, body.repository_id),
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
        events = task_service(request).list_events(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error
    return EventListResponse(events=[EventResponse.from_model(event) for event in events])


@router.post("/tasks/{task_id}/messages", status_code=status.HTTP_204_NO_CONTENT)
def add_task_message(task_id: str, body: AddMessageRequest, request: Request) -> None:
    try:
        task_service(request).add_user_message(task_id, body.message)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error


@router.post("/tasks/{task_id}/cancel", response_model=TaskResponse)
def cancel_task(task_id: str, request: Request) -> TaskResponse:
    try:
        task = task_service(request).cancel_task(task_id)
        request.app.state.workflow_service.sync_for_task(task_id)
        return TaskResponse.from_model(task)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="Task not found") from error
