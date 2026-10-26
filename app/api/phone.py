"""What the Android app reads for itself: the reminders and alarms it rings.

The app fetches the coming two weeks, schedules them with Android, and reports back up
to when it has each one, so the server sends no second notification through ntfy.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.schedules import mark_on_phone, phone_alarms

router = APIRouter()


class PhoneAlarmsAck(BaseModel):
    #: Schedule id → the last time (ms since epoch) the phone has scheduled for it.
    until: dict[str, int] = Field(default_factory=dict, max_length=200)


@router.get("/phone/alarms")
def list_phone_alarms(request: Request) -> dict:
    return {"alarms": phone_alarms(request.app.state.database)}


@router.post("/phone/alarms/ack")
def ack_phone_alarms(body: PhoneAlarmsAck, request: Request) -> dict:
    return {"marked": mark_on_phone(request.app.state.database, body.until)}
