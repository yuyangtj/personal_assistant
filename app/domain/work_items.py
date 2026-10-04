"""Work items: the durable goals that chats and runs attach to.

A work item outlives any one conversation. Its brief is the source of truth that a new
chat reads instead of old transcripts; runs (tasks) and chats link to it, and every
change is recorded as a work item event so the timeline stays auditable.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_BRIEF_ENTRIES = 12
MAX_BRIEF_ENTRY_CHARACTERS = 300
MAX_SLUG_CHARACTERS = 40


class WorkItemKind(StrEnum):
    GOAL = "goal"
    LIST = "list"
    ROUTINE = "routine"
    WATCH = "watch"


class WorkItemStatus(StrEnum):
    OPEN = "open"
    ACTIVE = "active"
    BLOCKED = "blocked"
    DONE = "done"
    ARCHIVED = "archived"


class WorkItemEventType(StrEnum):
    WORK_ITEM_CREATED = "WORK_ITEM_CREATED"
    BRIEF_UPDATED = "BRIEF_UPDATED"
    BRIEF_UPDATE_FAILED = "BRIEF_UPDATE_FAILED"
    STATUS_CHANGED = "STATUS_CHANGED"
    TITLE_CHANGED = "TITLE_CHANGED"
    SLUG_CHANGED = "SLUG_CHANGED"
    LINKS_CHANGED = "LINKS_CHANGED"
    CHECKLIST_CHANGED = "CHECKLIST_CHANGED"
    RUN_LINKED = "RUN_LINKED"
    CHAT_FOCUSED = "CHAT_FOCUSED"
    CHAT_UNFOCUSED = "CHAT_UNFOCUSED"


DEFAULT_SPACES: tuple[tuple[str, str, str], ...] = (
    ("general", "General", "general"),
    ("coding", "Coding", "coding"),
)


def _bounded_entries(values: list[str]) -> list[str]:
    cleaned = [" ".join(value.split()) for value in values]
    cleaned = [value for value in cleaned if value]
    if len(cleaned) > MAX_BRIEF_ENTRIES:
        raise ValueError(f"at most {MAX_BRIEF_ENTRIES} entries are allowed")
    if any(len(value) > MAX_BRIEF_ENTRY_CHARACTERS for value in cleaned):
        raise ValueError(f"entries must be at most {MAX_BRIEF_ENTRY_CHARACTERS} characters")
    return cleaned


class Brief(BaseModel):
    """What a later chat needs to continue without redoing work."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(default="", max_length=600)
    status_summary: str = Field(default="", max_length=600)
    decisions: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    @field_validator("decisions", "next_steps", "open_questions")
    @classmethod
    def entries_are_bounded(cls, values: list[str]) -> list[str]:
        return _bounded_entries(values)


class ChecklistEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=36)
    text: str = Field(min_length=1, max_length=300)
    done: bool = False
    added_at: datetime


class WorkItemLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["pull_request", "repository", "url", "product"]
    label: str = Field(min_length=1, max_length=160)
    url: str | None = Field(default=None, max_length=500)
    ref: str | None = Field(default=None, max_length=200)


SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
#: A ``#slug`` mention in a chat message, not preceded by a word or path character.
MENTION_PATTERN = re.compile(r"(?<![\w/#])#([a-z0-9][a-z0-9-]{1,79})\b")
SLUG_WORDS = 5
_SLUG_FILLER = frozenset(
    {
        "a",
        "about",
        "also",
        "an",
        "and",
        "app",
        "can",
        "could",
        "for",
        "i",
        "in",
        "into",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "please",
        "that",
        "the",
        "this",
        "to",
        "we",
        "with",
        "would",
        "you",
    }
)


def slugify(title: str) -> str:
    """A short handle someone would type, such as ``add-voice-login``.

    Keeps the first few meaningful words, so a long request still yields a tag like
    ``update-icon-light-dark-mode`` rather than its whole first sentence.
    """
    words = re.findall(r"[a-z0-9]+", title.lower())
    meaningful = [word for word in words if word not in _SLUG_FILLER] or words
    slug = "-".join(meaningful[:SLUG_WORDS])[:MAX_SLUG_CHARACTERS].rstrip("-")
    return slug or "item"


def mentioned_slugs(text: str) -> list[str]:
    return list(dict.fromkeys(match.lower() for match in MENTION_PATTERN.findall(text.lower())))


MAX_CONTEXT_ITEMS = 3
MAX_CONTEXT_CHECKLIST = 10
MAX_CONTEXT_ACTIVITY = 5


def render_work_item_context(items: list[dict]) -> str:
    """Plain text for a model prompt: each focused item's brief, open checklist, and
    recent activity. Built from curated records only, never from raw transcripts."""
    blocks: list[str] = []
    for item in items[:MAX_CONTEXT_ITEMS]:
        brief = item.get("brief") or {}
        lines = [f"#{item['slug']} — {item['title']} ({item['kind']}, {item['status']})"]
        if goal := brief.get("goal"):
            lines.append(f"Goal: {goal}")
        if status_summary := brief.get("status_summary"):
            lines.append(f"Where it stands: {status_summary}")
        for label, key in (
            ("Decisions", "decisions"),
            ("Next steps", "next_steps"),
            ("Open questions", "open_questions"),
        ):
            if entries := brief.get(key):
                lines.append(f"{label}:")
                lines.extend(f"- {entry}" for entry in entries)
        if checklist := item.get("open_checklist"):
            lines.append("Open checklist:")
            lines.extend(f"- {entry}" for entry in checklist[:MAX_CONTEXT_CHECKLIST])
        if activity := item.get("recent_activity"):
            lines.append("Recent activity (newest first):")
            lines.extend(f"- {entry}" for entry in activity[:MAX_CONTEXT_ACTIVITY])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
