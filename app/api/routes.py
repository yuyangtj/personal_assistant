"""All HTTP routes of the assistant, one module per resource."""

from fastapi import APIRouter

from app.api import (
    catalog,
    chat,
    memories,
    passkeys,
    speech,
    tasks,
    web,
    workflows,
)

router = APIRouter()
for module in (chat, tasks, workflows, catalog, memories, passkeys, speech, web):
    router.include_router(module.router)
