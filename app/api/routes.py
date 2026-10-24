"""All HTTP routes of the assistant, one module per resource."""

from fastapi import APIRouter

from app.api import (
    abilities,
    catalog,
    chat,
    memories,
    notes,
    passkeys,
    speech,
    stats,
    tasks,
    web,
    workflows,
)

router = APIRouter()
for module in (
    abilities,
    chat,
    tasks,
    workflows,
    catalog,
    memories,
    notes,
    passkeys,
    speech,
    stats,
    web,
):
    router.include_router(module.router)
