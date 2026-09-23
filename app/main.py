from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import router
from app.capabilities import CapabilityRegistry
from app.config import Settings
from app.decision import CodingRunnerRegistry
from app.deployments import DeploymentRegistry
from app.integrations.gemini_tts import GeminiTtsClient
from app.integrations.github import GitHubClient
from app.integrations.speech import CachedSpeechSynthesizer
from app.memory import MemoryService
from app.persistence.database import Database
from app.providers import DatabaseProviderStateStore
from app.repositories import RepositoryRegistry
from app.service import TaskService
from app.validation import ValidationProfileRegistry
from app.workflows import WorkflowRegistry, WorkflowService


def create_app(
    settings: Settings | None = None,
    capability_registry: CapabilityRegistry | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    registry = capability_registry or CapabilityRegistry.from_directory(
        resolved_settings.capabilities_directory
    )
    repository_registry = RepositoryRegistry.from_directory(
        resolved_settings.repositories_directory
    )
    workflow_registry = WorkflowRegistry.from_directory(resolved_settings.workflows_directory)
    coding_runner_registry = CodingRunnerRegistry.from_directory(
        resolved_settings.coding_runners_directory
    )
    validation_profile_registry = ValidationProfileRegistry.from_directory(
        resolved_settings.validation_profiles_directory
    )
    deployment_registry = DeploymentRegistry.from_directory(
        resolved_settings.deployment_targets_directory
    )
    database = Database(resolved_settings.database_url)
    if resolved_settings.auto_create_schema:
        database.create_schema()

    application = FastAPI(
        title="Personal Assistant Core",
        version="0.1.0",
        description="Persistent task and event shell for personal AI orchestration.",
    )
    application.state.settings = resolved_settings
    application.state.database = database
    application.state.task_service = TaskService(database)
    application.state.memory_service = MemoryService(database)
    application.state.capability_registry = registry
    application.state.repository_registry = repository_registry
    application.state.workflow_registry = workflow_registry
    application.state.coding_runner_registry = coding_runner_registry
    application.state.validation_profile_registry = validation_profile_registry
    application.state.deployment_registry = deployment_registry
    application.state.workflow_service = WorkflowService(
        database,
        workflow_registry,
        repository_registry,
        deployment_registry,
    )
    application.state.provider_state_store = DatabaseProviderStateStore(database)
    application.state.speech_synthesizer = (
        CachedSpeechSynthesizer(
            GeminiTtsClient(
                api_key=resolved_settings.gemini_tts_api_key,
                base_url=resolved_settings.gemini_tts_base_url,
                model=resolved_settings.gemini_tts_model,
                voice=resolved_settings.gemini_tts_voice,
                timeout_seconds=resolved_settings.gemini_tts_timeout_seconds,
            ),
            max_entries=resolved_settings.gemini_tts_cache_entries,
        )
        if resolved_settings.gemini_tts_api_key
        else None
    )
    application.state.github_client = (
        GitHubClient(
            token=resolved_settings.github_token,
            base_url=resolved_settings.github_api_base_url,
        )
        if resolved_settings.github_token
        else None
    )
    application.state.approval_token = resolved_settings.approval_token
    application.include_router(router)
    return application


app = create_app()
