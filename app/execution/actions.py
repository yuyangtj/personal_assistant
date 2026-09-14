from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

LOCAL_DATETIME_FORMAT = "%Y-%m-%dT%H:%M"


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("label", "location", mode="before", check_fields=False)
    @classmethod
    def missing_text_is_empty(cls, value: Any) -> Any:
        return "" if value is None else value


def _local_minutes(value: Any) -> str:
    """Normalizes ISO-like local date-times ("2026-09-15 15:00:00", offsets) to YYYY-MM-DDTHH:MM."""
    if not isinstance(value, str):
        raise ValueError("time must be a string")
    parsed = datetime.fromisoformat(value.strip().replace(" ", "T").removesuffix("Z"))
    return parsed.replace(tzinfo=None).strftime(LOCAL_DATETIME_FORMAT)


class SetTimerAction(_Action):
    """Starts a countdown in the phone's clock app."""

    type: Literal["set_timer"]
    seconds: int = Field(ge=1, le=86_400)
    label: str = Field(default="", max_length=60)


class SetAlarmAction(_Action):
    """Sets an alarm in the phone's clock app; `days` repeat it (1=Sunday … 7=Saturday)."""

    type: Literal["set_alarm"]
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    label: str = Field(default="", max_length=60)
    days: tuple[int, ...] = Field(default=(), max_length=7)
    date: str | None = Field(
        default=None,
        description="Local YYYY-MM-DD for one-time alarms; null for repeating alarms",
    )

    @field_validator("date", mode="before")
    @classmethod
    def normalize_date(cls, value: Any) -> str | None:
        if value is None or (
            isinstance(value, str) and value.strip().lower() in {"", "null", "none"}
        ):
            return None
        if not isinstance(value, str):
            raise ValueError("date must be a string")
        return Date.fromisoformat(value.strip()).isoformat()

    @model_validator(mode="after")
    def days_are_weekdays(self) -> SetAlarmAction:
        if any(day < 1 or day > 7 for day in self.days) or len(set(self.days)) != len(self.days):
            raise ValueError("days must be unique values from 1 (Sunday) to 7 (Saturday)")
        if self.days and self.date is not None:
            raise ValueError("repeating alarms must not include a date")
        if not self.days and self.date is None:
            raise ValueError("one-time alarms require a date")
        return self


class CreateEventAction(_Action):
    """Opens the calendar app pre-filled; the user saves the event there."""

    type: Literal["create_event"]
    title: str = Field(min_length=1, max_length=120)
    start: str = Field(description="Local time, YYYY-MM-DDTHH:MM")
    end: str | None = Field(default=None, description="Local time, YYYY-MM-DDTHH:MM")
    location: str = Field(default="", max_length=120)

    @field_validator("start", mode="before")
    @classmethod
    def normalize_start(cls, value: Any) -> str:
        return _local_minutes(value)

    @field_validator("end", mode="before")
    @classmethod
    def normalize_end(cls, value: Any) -> str | None:
        if value is None or (
            isinstance(value, str) and value.strip().lower() in {"", "null", "none"}
        ):
            return None
        return _local_minutes(value)

    @model_validator(mode="after")
    def times_are_ordered(self) -> CreateEventAction:
        start = datetime.strptime(self.start, LOCAL_DATETIME_FORMAT)
        if self.end is not None and datetime.strptime(self.end, LOCAL_DATETIME_FORMAT) <= start:
            raise ValueError("end must be after start")
        return self


PhoneAction = Annotated[
    SetTimerAction | SetAlarmAction | CreateEventAction,
    Field(discriminator="type"),
]
_ADAPTER: TypeAdapter[PhoneAction] = TypeAdapter(PhoneAction)


def validate_action(raw: Any) -> dict[str, Any] | None:
    """Returns a normalized action, or None when absent or invalid (invalid ones are dropped)."""
    if not isinstance(raw, dict):
        return None
    try:
        return _ADAPTER.validate_python(raw).model_dump(mode="json")
    except (ValidationError, ValueError):
        return None


def alarm_matches_next_occurrence(action: dict[str, Any], local_time: Any) -> bool:
    """One-time Clock intents are safe only when their date is the next occurrence."""
    if action.get("type") != "set_alarm" or action.get("days"):
        return True
    if not isinstance(local_time, str) or not isinstance(action.get("date"), str):
        return False
    try:
        now = datetime.fromisoformat(local_time.strip())
        candidate = now.replace(
            hour=int(action["hour"]),
            minute=int(action["minute"]),
            second=0,
            microsecond=0,
        )
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.date().isoformat() == action["date"]
    except (KeyError, TypeError, ValueError):
        return False
