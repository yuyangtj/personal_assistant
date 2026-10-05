"""Routines: saved instructions the assistant runs on a schedule, or on demand.

A routine is the assistant's "skill": "every 1st of the month, analyze my Klarna spending",
"every Monday, tell me when Zoégas is under 50 kr at Willys". Its instruction is routed
once, when it is saved, like a chat message (Klarna and the stores go to the shopping
agent, web questions to research, coding work to the supervisor), and every run goes there
with the routine's earlier results to compare against. A watch ("tell me when ...") posts
each result to its chat but only pushes to the phone when the agent marks it notable.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.chat.triage import TriageDecision, Triager
from app.persistence.models import ScheduleModel
from app.schedules import Recurrence, zone
from app.services import TaskService

_WATCH = re.compile(
    r"\b(?:tell|notify|alert|ping|let)\s+me\s+(?:know\s+)?(?:when|if|once)\b|\bonly\s+if\b|"
    r"\bwatch\b|\bsäg\s+till\s+(?:när|om)\b|\bmeddela\s+mig\s+(?:när|om)\b|\bbevaka\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"\w{3,}")
_FILLER = {
    "the",
    "and",
    "routine",
    "routines",
    "watch",
    "now",
    "run",
    "stop",
    "cancel",
    "delete",
    "remove",
    "every",
    "each",
    "please",
    "that",
    "this",
    "with",
    "for",
    "rutin",
    "rutinen",
}


def is_watch(instruction: str) -> bool:
    """Whether a routine should only push when something is worth knowing."""
    return bool(_WATCH.search(instruction))


def route_capabilities(decision: TriageDecision, *, supervision: bool) -> list[str]:
    """Which agent a routine's instruction needs, from the same triage as chat."""
    if "groceries" in decision.capabilities:
        return ["groceries"]
    if "web_research" in decision.capabilities:
        return ["web_research"]
    if decision.about_work and supervision:
        return ["supervision"]
    return []


def routine_capabilities(triager: Triager, instruction: str, *, supervision: bool) -> list[str]:
    try:
        decision = triager.decide(instruction)
    except Exception:  # routing is a nicety here; plain chat still answers
        return []
    return route_capabilities(decision, supervision=supervision)


def start_routine(
    tasks: TaskService,
    schedule: ScheduleModel,
    *,
    fallback: Triager | None = None,
    chat_session_id: str | None = None,
) -> str:
    """Start one run of a routine, with its stored route (or keyword rules for old ones).

    ``chat_session_id`` runs it in another chat, e.g. the one where "run it now" was said.
    """
    capabilities = schedule.capabilities
    if capabilities is None and fallback is not None:
        capabilities = routine_capabilities(fallback, schedule.message, supervision=False)
    task = tasks.create_task(
        request=schedule.message,
        required_capabilities=capabilities or None,
        chat_session_id=chat_session_id or schedule.chat_session_id,
        work_item_id=schedule.work_item_id,
        source_context={"schedule_id": schedule.id, "timezone": schedule.timezone},
    )
    return task.id


def find_routines(routines: list[ScheduleModel], text: str) -> list[ScheduleModel]:
    """Routines matching a description ("the Klarna one", "coffee watch"), best first."""
    wanted = set(_WORD.findall(text.lower())) - _FILLER
    scored = [
        (len(wanted & set(_WORD.findall(routine.message.lower()))), routine) for routine in routines
    ]
    best = max((score for score, _ in scored), default=0)
    if not best:
        return []
    return [routine for score, routine in scored if score == best]


_REPEATS = {
    Recurrence.DAILY.value: "every day",
    Recurrence.WEEKDAYS.value: "every weekday",
    Recurrence.WEEKLY.value: "every {weekday}",
    Recurrence.MONTHLY.value: "every month on the {day}",
    Recurrence.NONE.value: "once, {date}",
}


def describe(routine: ScheduleModel) -> str:
    """ "every Monday at 08:00: analyze my Klarna spending (next Mon 12 Oct)"."""
    next_run = routine.next_run_at
    if next_run.tzinfo is None:  # SQLite drops the time zone
        next_run = next_run.replace(tzinfo=UTC)
    local: datetime = next_run.astimezone(zone(routine.timezone))
    repeat = _REPEATS.get(routine.recurrence, routine.recurrence).format(
        weekday=local.strftime("%A"), day=_ordinal(local.day), date=local.strftime("%a %d %b")
    )
    watch = " (watch: tells you only when it's worth knowing)" if routine.quiet else ""
    return f"{repeat} at {local:%H:%M}: {routine.message}{watch} — next {local:%a %d %b}"


def _ordinal(day: int) -> str:
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"
