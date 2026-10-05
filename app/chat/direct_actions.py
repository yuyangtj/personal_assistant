"""T0: small, reversible actions done instantly, without a chat model.

Triage decides that a message is a bookkeeping request; this module carries it out
against the work item and memory services and returns the sentence to reply with.
Nothing here is irreversible: checklist entries can be unchecked or removed in the
item page, and memories archived.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.chat.routines import describe, find_routines, is_watch, start_routine
from app.chat.triage import DirectAction
from app.domain.work_items import WorkItemKind
from app.persistence.models import utc_now
from app.schedules import Recurrence, ScheduleKind, ScheduleService, zone
from app.work.items import RESUMABLE_STATUSES, WorkItemNotFoundError, WorkItemService
from app.work.memory import MemoryService


@dataclass(frozen=True)
class DirectActionResult:
    done: bool
    reply: str
    work_item_slug: str | None = None
    blocks: list[dict[str, Any]] = field(default_factory=list)


def _target_item(action: DirectAction, chat_session_id: str, items: WorkItemService):
    """The named item, else the chat's only open focus, else the only open list."""
    if action.work_item:
        return items.get(action.work_item.lstrip("#"))
    focused = [item for item in items.focused(chat_session_id) if item.status in RESUMABLE_STATUSES]
    if len(focused) == 1:
        return focused[0]
    lists = [
        item for item in items.list(kind=WorkItemKind.LIST) if item.status in RESUMABLE_STATUSES
    ]
    if len(lists) == 1:
        return lists[0]
    raise WorkItemNotFoundError("ambiguous")


def _schedule(
    action: DirectAction,
    *,
    chat_session_id: str,
    items: WorkItemService,
    schedules: ScheduleService,
    timezone: str,
    now: datetime,
    route: Callable[[str], list[str]] | None = None,
) -> DirectActionResult:
    if not action.at:
        return DirectActionResult(False, "When should that be? Give me a day and time.")
    try:
        local = datetime.fromisoformat(action.at).replace(tzinfo=zone(timezone))
    except ValueError:
        return DirectActionResult(False, "I couldn't read that time; try a day and time.")
    if local <= now and action.recurrence == "none":
        return DirectActionResult(False, "That time has already passed.")
    item = None
    if action.work_item:
        try:
            item = items.get(action.work_item.lstrip("#"))
        except WorkItemNotFoundError:
            item = None
    routine = action.type == "routine"
    watch = routine and is_watch(action.text)
    schedules.create(
        kind=ScheduleKind.ROUTINE if routine else ScheduleKind.REMINDER,
        message=action.text,
        run_at=local,
        recurrence=Recurrence(action.recurrence),
        timezone=timezone,
        work_item_id=item.id if item else None,
        chat_session_id=chat_session_id,
        # Where every run goes is decided once, now, like routing a chat message.
        capabilities=route(action.text) if routine and route else None,
        quiet=watch,
    )
    when = local.strftime("%a %d %b %H:%M")
    repeat = "" if action.recurrence == "none" else f", repeating {action.recurrence}"
    verb = "I'll remind you" if action.type == "remind" else "I'll do this"
    note = " (I'll only notify you when it's worth knowing)" if watch else ""
    return DirectActionResult(
        True, f"{verb} {when}{repeat}: {action.text}{note}", item.slug if item else None
    )


def _routines(
    action: DirectAction,
    *,
    chat_session_id: str,
    schedules: ScheduleService,
    tasks: Any,
) -> DirectActionResult:
    """List, run now, or stop saved routines."""
    routines = [s for s in schedules.list() if s.kind == ScheduleKind.ROUTINE.value]
    if action.type == "list_routines" or not routines:
        if not routines:
            return DirectActionResult(
                True,
                "You have no routines. Say, for example, “every month on the 1st at 9, analyze "
                "my Klarna spending”.",
            )
        lines = "\n".join(f"- {describe(routine)}" for routine in routines)
        options = []
        for routine in routines[:2]:
            short = routine.message if len(routine.message) <= 40 else routine.message[:39] + "…"
            options += [{"label": f"Run “{short}” now"}, {"label": f"Stop “{short}”"}]
        return DirectActionResult(
            True,
            f"Your routines:\n{lines}",
            blocks=[{"type": "choices", "options": options}],
        )
    matches = find_routines(routines, action.text)
    if not matches:
        return DirectActionResult(
            False, "I couldn't tell which routine you meant. Ask “what routines do I have?”."
        )
    if len(matches) > 1:
        listed = "\n".join(f"- {describe(routine)}" for routine in matches[:5])
        return DirectActionResult(False, f"More than one routine matches; which one?\n{listed}")
    routine = matches[0]
    if action.type == "cancel_routine":
        schedules.cancel(routine.id)
        return DirectActionResult(True, f"Stopped: {routine.message}")
    if tasks is None:
        return DirectActionResult(False, "Routines can't be run from here.")
    start_routine(tasks, routine, chat_session_id=chat_session_id)
    return DirectActionResult(True, f"Running now: {routine.message}")


def _forget(text: str, memory: MemoryService) -> DirectActionResult:
    matches = memory.find(text)
    if len(matches) == 1:
        memory.archive(matches[0].id)
        return DirectActionResult(True, f"Forgotten: {matches[0].content}")
    if not matches:
        return DirectActionResult(
            False, "I don't have a memory about that. Everything I remember is under Memories."
        )
    listed = "\n".join(f"- {match.content}" for match in matches[:5])
    return DirectActionResult(
        False,
        f"More than one memory matches; say which one, or delete it under Memories:\n{listed}",
    )


def run_direct_action(
    action: DirectAction,
    *,
    chat_session_id: str,
    items: WorkItemService,
    memory: MemoryService,
    schedules: ScheduleService | None = None,
    timezone: str = "UTC",
    now: datetime | None = None,
    tasks: Any = None,
    route: Callable[[str], list[str]] | None = None,
) -> DirectActionResult:
    if action.type in ("list_routines", "run_routine", "cancel_routine"):
        if schedules is None:
            return DirectActionResult(False, "Routines aren't available here.")
        return _routines(action, chat_session_id=chat_session_id, schedules=schedules, tasks=tasks)
    if action.type in ("remind", "routine"):
        if schedules is None:
            return DirectActionResult(False, "Reminders aren't available here.")
        return _schedule(
            action,
            chat_session_id=chat_session_id,
            items=items,
            schedules=schedules,
            timezone=timezone,
            now=now or utc_now(),
            route=route,
        )
    if action.type == "remember":
        memory.create(
            kind=action.kind or "fact", content=action.text, chat_session_id=chat_session_id
        )
        return DirectActionResult(True, f"I'll remember that: {action.text}")
    if action.type == "forget":
        return _forget(action.text, memory)
    try:
        item = _target_item(action, chat_session_id, items)
    except WorkItemNotFoundError:
        named = f"#{action.work_item.lstrip('#')}" if action.work_item else "which list"
        return DirectActionResult(
            False,
            f"I couldn't tell {named} you meant. Mention the list with its #tag, "
            "or create it in Work items.",
        )
    if action.type == "checklist_add":
        items.add_checklist_entry(item.id, action.text)
        return DirectActionResult(True, f"Added “{action.text}” to #{item.slug}.", item.slug)
    wanted = action.text.lower()
    matches = [
        entry
        for entry in item.checklist or []
        if not entry.get("done")
        and (wanted in entry["text"].lower() or entry["text"].lower() in wanted)
    ]
    if len(matches) != 1:
        problem = "isn't an open entry" if not matches else "matches more than one entry"
        return DirectActionResult(False, f"“{action.text}” {problem} on #{item.slug}.", item.slug)
    items.set_checklist_entry(item.id, matches[0]["id"], done=True)
    return DirectActionResult(
        True, f"Checked off “{matches[0]['text']}” on #{item.slug}.", item.slug
    )
