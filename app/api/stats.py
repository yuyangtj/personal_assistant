"""How the assistant is doing: reply times, failures, sizes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from app.work.stats import compute_stats

router = APIRouter()


@router.get("/stats")
def stats(request: Request, days: Annotated[int, Query(ge=1, le=90)] = 7) -> dict[str, Any]:
    """Counts, reply times (from the message to the end of the work) and failures by kind of
    work over the last ``days`` days, plus the size of the database and the disk."""
    return compute_stats(request.app.state.database, days=days)
