"""What a posted chat message needs: an explicit note saved at once, or the chat agent.

The chat agent (with its tools) is the only thing that decides what a message means. The
server does without a model only "note: …" and "remember privately …" (see the API).
"""

from __future__ import annotations

import logging
import re

from fastapi import Request

from app.api.schemas import AppendChatMessageRequest, TriageDecisionResponse
from app.chat.blocks import pending_choices, settle
from app.chat.direct_actions import DirectAction, note_rule, run_direct_action
from app.schedules import zone


def propose_coding_run(
    request: Request,
    chat_session_id: str,
    message_id: str,
    repository_id: str,
    *,
    goal: str | None = None,
):
    """A proposed coding workflow for a user message; nothing runs until it is approved."""
    message = request.app.state.task_service.get_user_chat_message(chat_session_id, message_id)
    repository = request.app.state.repository_registry.get(repository_id)
    request_text = message.content
    if goal and goal.strip() and goal.strip() != message.content:
        request_text = f"{goal.strip()}\n\nOriginal request: {message.content}"
    return request.app.state.workflow_service.create_run(
        workflow_id="coding-change",
        workflow_input={"repository_id": repository.id, "request": request_text},
        chat_session_id=chat_session_id,
        origin_message_id=message.id,
    )


logger = logging.getLogger(__name__)

_PRIVATE = re.compile(
    r"^(?:please\s+)?(?:(?:remember|save|note)\s+(?:this\s+)?privately|privately\s+remember)"
    r"(?:\s+that)?[:,]?\s+(?P<text>.+)$",
    re.IGNORECASE | re.DOTALL,
)


def private_memory_text(content: str) -> str | None:
    """The text of "remember privately …", which is handled before any model sees it."""
    match = _PRIVATE.match(content.strip())
    return match["text"].strip() if match and match["text"].strip() else None


def triage_message(
    request: Request, chat_session_id: str, body: AppendChatMessageRequest, message_id: str
) -> TriageDecisionResponse:
    """Decide what a just-stored message needs; never raises (rules are the fallback).

    The decision and where it led are kept on the message, so "why did it do that" can
    always be answered later (see ``ChatMessageResponse.routing``).
    """
    response = _route_message(request, chat_session_id, body, message_id)
    try:
        request.app.state.task_service.set_message_routing(
            message_id,
            response.model_dump(
                mode="json",
                include={
                    "route",
                    "intent",
                    "capabilities",
                    "goal",
                    "reason",
                    "confidence",
                    "source",
                    "follow_up",
                    "about_work",
                    "repository_id",
                },
            ),
        )
    except Exception:  # a record that fails to save never fails the message
        logger.exception("Could not record the routing of message %s", message_id)
    return response


def _route_message(
    request: Request, chat_session_id: str, body: AppendChatMessageRequest, message_id: str
) -> TriageDecisionResponse:
    service = request.app.state.task_service
    # The assistant's suggestion buttons are only suggestions: a tap sends the label as an
    # ordinary message. Whatever is said next settles them, so they can't be tapped twice.
    pending = pending_choices(
        service.list_chat_messages(chat_session_id, limit=500)[-10:],
        current_message_id=message_id,
    )
    if pending is not None:
        offer_message, block = pending
        said = " ".join(body.content.split()).lower()
        chosen = next(
            (o["label"] for o in block.get("options", []) if o["label"].lower() == said), None
        )
        service.set_message_blocks(
            offer_message.id, settle(offer_message.blocks or [], block["id"], chosen=chosen)
        )
    action = note_rule(body.content)
    response = TriageDecisionResponse(
        intent="direct_action" if action else "answer",
        goal=body.content[:500],
        reason="An explicit note." if action else "The chat agent answers.",
        confidence=1.0,
        source="rules" if action else "main-agent",
    )
    if action is not None:
        return _run_instantly(request, chat_session_id, body, action, response)
    # The client queues the chat reply; the agent decides what it needs.
    response.route = "main agent"
    return response


def _run_instantly(
    request: Request,
    chat_session_id: str,
    body: AppendChatMessageRequest,
    action: DirectAction,
    response: TriageDecisionResponse,
) -> TriageDecisionResponse:
    """T0: done now, without a chat model; the reply lands in the transcript."""
    service = request.app.state.task_service
    timezone = body.timezone or request.app.state.settings.default_timezone
    try:
        zone(timezone)
    except ValueError:
        timezone = request.app.state.settings.default_timezone
    result = run_direct_action(
        action,
        chat_session_id=chat_session_id,
        items=request.app.state.work_item_service,
        memory=request.app.state.memory_service,
        notes=request.app.state.note_service,
        schedules=request.app.state.schedule_service,
        timezone=timezone,
        tasks=service,
    )
    service.append_chat_message(
        chat_session_id, content=result.reply, role="assistant", blocks=result.blocks or None
    )
    response.result = result.reply
    response.handled = True
    response.route = f"action: {action.type}"
    return response
