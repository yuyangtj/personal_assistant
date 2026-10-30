from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.persistence.models import OutboxDeliveryModel


def enqueue(session: Session, *, kind: str, payload: dict[str, Any]) -> str:
    delivery = OutboxDeliveryModel(id=str(uuid4()), kind=kind, payload=payload)
    session.add(delivery)
    session.flush()
    return delivery.id


def schedule_payload(schedule, fired_at) -> dict[str, Any]:
    """Only which schedule fired and when: the rest is read from the schedule on delivery,
    so a schedule cancelled or deleted in the meantime is never delivered."""
    return {"schedule_id": schedule.id, "fired_at": fired_at.isoformat()}
