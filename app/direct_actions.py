"""T0: small, reversible actions done instantly, without a chat model.

Triage decides that a message is a bookkeeping request; this module carries it out
against the work item and memory services and returns the sentence to reply with.
Nothing here is irreversible: checklist entries can be unchecked or removed in the
item page, and memories archived.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.work_items import WorkItemKind
from app.memory import MemoryService
from app.persistence.models import utc_now
from app.schedules import Recurrence, ScheduleKind, ScheduleService, zone
from app.triage import DirectAction
from app.work_items import RESUMABLE_STATUSES, WorkItemNotFoundError, WorkItemService


@dataclass(frozen=True)
class DirectActionResult:
    done: bool
    reply: str
    work_item_slug: str | None = None


def _target_item(action: DirectAction, chat_session_id: str, items: WorkItemService):
    """The named item, else the chat's only open focus, else the only open list."""
    if action.work_item:
        return items.get(action.work_item.lstrip("#"))
    focused = [
        item for item in items.focused(chat_session_id) if item.status in RESUMABLE_STATUSES
    ]
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
    schedules.create(
        kind=ScheduleKind.REMINDER if action.type == "remind" else ScheduleKind.ROUTINE,
        message=action.text,
        run_at=local,
        recurrence=Recurrence(action.recurrence),
        timezone=timezone,
        work_item_id=item.id if item else None,
        chat_session_id=chat_session_id,
    )
    when = local.strftime("%a %d %b %H:%M")
    repeat = "" if action.recurrence == "none" else f", repeating {action.recurrence}"
    verb = "I'll remind you" if action.type == "remind" else "I'll do this"
    return DirectActionResult(
        True, f"{verb} {when}{repeat}: {action.text}", item.slug if item else None
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
) -> DirectActionResult:
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
        )
    if action.type == "remember":
        memory.create(
            kind=action.kind or "fact", content=action.text, chat_session_id=chat_session_id
        )
        return DirectActionResult(True, f"I'll remember that: {action.text}")
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
        return DirectActionResult(
            False, f"“{action.text}” {problem} on #{item.slug}.", item.slug
        )
    items.set_checklist_entry(item.id, matches[0]["id"], done=True)
    return DirectActionResult(
        True, f"Checked off “{matches[0]['text']}” on #{item.slug}.", item.slug
    )
