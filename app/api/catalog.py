"""What the assistant has configured: capabilities, repositories, runners, targets, health."""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from sqlalchemy import text

from app.api.schemas import (
    CapabilityListResponse,
    CodingRunnerListResponse,
    DeploymentTargetListResponse,
    HealthResponse,
    ProviderRuntimeStateListResponse,
    ProviderRuntimeStateResponse,
    RepositoryListResponse,
    RepositoryResponse,
    ValidationProfileListResponse,
    WorkflowListResponse,
)
from app.capabilities.models import CapabilityKind

router = APIRouter()


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
