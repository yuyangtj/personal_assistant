from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import router
from app.capabilities import CapabilityRegistry
from app.config import Settings
from app.persistence.database import Database
from app.service import TaskService


def create_app(
    settings: Settings | None = None,
    capability_registry: CapabilityRegistry | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    registry = capability_registry or CapabilityRegistry.from_directory(
        resolved_settings.capabilities_directory
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
    application.state.capability_registry = registry
    application.include_router(router)
    return application


app = create_app()
