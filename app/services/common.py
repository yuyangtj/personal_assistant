"""Errors, reply texts and small helpers shared across the task service."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.domain.proposals import TaskProposal
from app.domain.work_items import mentioned_slugs
from app.persistence.models import ChatMessageModel
from app.work.items import focus_chat, get_work_item

logger = logging.getLogger(__name__)


class TaskNotFoundError(LookupError):
    pass


class ChatSessionNotFoundError(LookupError):
    pass


class ChatMessageNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class PostedMessage:
    message: ChatMessageModel
    proposal: TaskProposal | None
    focused_work_items: list[str] = field(default_factory=list)


class MessageHasWorkflowError(ValueError):
    """The message already proposed a coding workflow; a chat-only task would bypass it."""


MAX_REPLY_CHARACTERS = 4000


DEFAULT_FAILURE_REPLY = "Sorry, I couldn't finish that request."


MALFORMED_REPOSITORY_REPLY = (
    "That needs code changes, which run through a coding workflow. "
    "Select the repository in the chat and propose it there."
)


CANCELLED_REPLY = "Okay, I've stopped working on that."


REPLY_EMOTIONS = {"Warm", "Curious", "Excited", "Concerned", "Neutral"}


MAX_HISTORY_TURNS = 6


DEFAULT_CHAT_TITLE = "New conversation"


def _reply_payload(text: str, *, emotion: str, intensity: float, outcome: str) -> dict[str, Any]:
    """User-facing speech for clients such as the Android avatar."""
    normalized = " ".join(text.split())[:MAX_REPLY_CHARACTERS]
    return {"text": normalized, "emotion": emotion, "intensity": intensity, "outcome": outcome}


def _focus_mentions(session, chat_session_id: str, text: str) -> list[str]:
    """Focus the chat on every existing work item the text mentions as #slug."""
    focused: list[str] = []
    for slug in mentioned_slugs(text):
        try:
            item = get_work_item(session, slug)
        except LookupError:
            continue
        focus_chat(session, chat_session_id=chat_session_id, item=item)
        focused.append(item.slug)
    return focused


def _chat_title(request: str) -> str:
    normalized = " ".join(request.split())
    return normalized if len(normalized) <= 80 else normalized[:77].rstrip() + "…"


class ExecutionNotFoundError(LookupError):
    pass


class ApprovalNotFoundError(LookupError):
    pass


class ApprovalConflictError(ValueError):
    pass
