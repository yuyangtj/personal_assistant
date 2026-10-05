"""Controlled workflows: coding runs and deployments, with their approvals."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.common import require_approval_token, task_service
from app.api.schemas import (
    CreateDeploymentWorkflowRequest,
    CreateWorkflowRunRequest,
    WorkflowDecisionRequest,
    WorkflowRunEventListResponse,
    WorkflowRunEventResponse,
    WorkflowRunListResponse,
    WorkflowRunResponse,
)
from app.coding_flow import start_coding_task
from app.deployments import HostDeployerSpoolError
from app.domain.enums import TaskStatus
from app.integrations.github import GitHubError
from app.service import TaskNotFoundError
from app.workflows import (
    WorkflowIntegrationUnavailableError,
    WorkflowRunConflictError,
    WorkflowRunNotFoundError,
)

router = APIRouter()


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
        task = task_service(request).get_task(task_id)
        if task.status != TaskStatus.COMPLETED.value:
            raise ValueError("Only a completed merged task can be deployed")
        target = request.app.state.deployment_registry.get(body.deployment_target_id)
        repository_id = str((task.source_context or {}).get("repository_id") or "")
        if repository_id != target.repository_id:
            raise ValueError("Deployment target does not match the task repository")
        context = task_service(request).task_context(task_id)
        merge_artifact = next(
            (
                artifact
                for artifact in reversed(context.get("artifacts") or [])
                if artifact.get("type") == "github_pull_request_merge" and artifact.get("merge_sha")
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
        task_service(request).get_task(task_id)
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
    require_approval_token(request)
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
            start_coding_task(task_service(request), workflow_service, run)
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
