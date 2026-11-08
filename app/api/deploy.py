"""Deploys requested by GitHub Actions after a merge, over HTTPS.

The deploy workflow sends a token GitHub signed for that run; it is the only credential,
so these paths are public in Caddy. A request becomes an ordinary deployment workflow
run, approved by the merge, and goes through the same checks and host deployer as a
deploy from the console.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.deployments import GitHubDeployClaims, GitHubTokenError, HostDeployerSpoolError
from app.integrations.github import GitHubError
from app.workflows import WorkflowRunConflictError, WorkflowRunNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter()

GITHUB_DEPLOY_TARGET = "personal-assistant-production"
#: How long the workflow should wait before asking again while a coding run executes.
RETRY_AFTER_SECONDS = 30


class GitHubDeployRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    #: Deploy even while a coding run is executing (it is interrupted).
    force: bool = False


def _github_claims(request: Request) -> GitHubDeployClaims:
    verifier = request.app.state.github_deploy_verifier
    if verifier is None:
        raise HTTPException(status_code=404, detail="Not found")
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="A GitHub Actions token is required")
    try:
        return verifier.verify(token.strip())
    except GitHubTokenError as error:
        logger.warning("Rejected a GitHub deploy token: %s", error)
        raise HTTPException(status_code=401, detail=f"Token rejected: {error}") from error


@router.post("/deploy/github", status_code=202)
def deploy_from_github(body: GitHubDeployRequest, request: Request, response: Response):
    """Start deploying a merged commit; 409 with a retry hint while a coding run executes."""
    claims = _github_claims(request)
    state = request.app.state
    service = state.workflow_service
    github = state.github_client
    if github is None:
        raise HTTPException(status_code=503, detail="GitHub integration is not configured")
    try:
        target = state.deployment_registry.get(GITHUB_DEPLOY_TARGET)
        repository = state.repository_registry.get(target.repository_id)
        head = github.get_branch_head(
            repository=repository.github_repository, branch=repository.base_branch
        )
        if head != body.commit_sha:
            response.status_code = 200
            return {
                "outcome": "skipped",
                "detail": f"{repository.base_branch} has moved on to {head[:12]}; "
                "that commit deploys in its own run",
            }
        if not body.force and service.coding_run_in_progress():
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "A coding run is executing; deploy after it finishes",
                    "retry_after_seconds": RETRY_AFTER_SECONDS,
                },
            )
        workflow_input = {
            "commit_sha": body.commit_sha,
            "deployment_target_id": GITHUB_DEPLOY_TARGET,
        }
        if body.force:
            workflow_input["force"] = True
        run = service.create_run(workflow_id="assistant-deployment", workflow_input=workflow_input)
        # Merging the reviewed pull request is the approval.
        service.decide(
            run.id,
            approve=True,
            by=f"github-actions run {claims.run_id} ({claims.event_name}, {claims.actor})",
        )
        run = service.start_deployment(run.id, github=github)
    except (GitHubError, HostDeployerSpoolError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except (KeyError, ValueError, WorkflowRunConflictError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    logger.info("GitHub Actions run %s started deployment %s", claims.run_id, run.id)
    return {"outcome": "started", "workflow_run_id": run.id}


@router.get("/deploy/github/{workflow_run_id}")
def github_deploy_status(workflow_run_id: str, request: Request):
    """How a deployment is going: running, completed or failed, with the deploy log's end."""
    _github_claims(request)
    service = request.app.state.workflow_service
    try:
        run = service.get_run(workflow_run_id)
        if run.workflow_id != "assistant-deployment":
            raise WorkflowRunNotFoundError(workflow_run_id)
        if run.status == "running":
            run = service.sync_deployment(workflow_run_id, github=None)
        host = service.host_deployer.status(run.id) if service.host_deployer else None
    except WorkflowRunNotFoundError as error:
        raise HTTPException(status_code=404, detail="Deployment not found") from error
    except (HostDeployerSpoolError, WorkflowRunConflictError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {
        "workflow_run_id": run.id,
        "status": run.status,
        "deployer_state": host.state.value if host else None,
        "log_tail": host.log_tail if host else "",
    }
