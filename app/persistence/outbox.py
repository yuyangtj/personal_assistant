from __future__ import annotations

from datetime import UTC
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.persistence.models import OutboxDeliveryModel, utc_now


def enqueue(session: Session, *, kind: str, payload: dict[str, Any]) -> str:
    delivery = OutboxDeliveryModel(id=str(uuid4()), kind=kind, payload=payload)
    session.add(delivery)
    session.flush()
    return delivery.id


def schedule_payload(schedule, fired_at) -> dict[str, Any]:
    phone_until = schedule.phone_until
    if phone_until is not None and phone_until.tzinfo is None:
        phone_until = phone_until.replace(tzinfo=UTC)
    return {
        "schedule_id": schedule.id,
        "kind": schedule.kind,
        "message": schedule.message,
        "work_item_id": schedule.work_item_id,
        "chat_session_id": schedule.chat_session_id,
        "capabilities": schedule.capabilities,
        "quiet": schedule.quiet,
        "timezone": schedule.timezone,
        "fired_at": fired_at.isoformat(),
        "phone_until": phone_until.isoformat() if phone_until else None,
        "enqueued_at": utc_now().isoformat(),
    }
