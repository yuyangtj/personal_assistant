"""What the assistant can do (abilities/*.yaml), as the console shows it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/abilities")
def abilities(request: Request) -> dict[str, Any]:
    """Every ability with its band, and whether this server has what it needs to offer it."""
    settings = request.app.state.settings
    return {
        "abilities": [
            ability.model_dump(mode="json") | {"available": ability.available(settings)}
            for ability in request.app.state.ability_registry.list()
        ]
    }
