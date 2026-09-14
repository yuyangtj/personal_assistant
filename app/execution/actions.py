from __future__ import annotations

from datetime import datetime
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

    @model_validator(mode="after")
    def days_are_weekdays(self) -> SetAlarmAction:
        if any(day < 1 or day > 7 for day in self.days) or len(set(self.days)) != len(self.days):
            raise ValueError("days must be unique values from 1 (Sunday) to 7 (Saturday)")
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
