"""What a posted chat message needs: a choice answered, an action done, an offer, or a reply."""

from __future__ import annotations

from fastapi import Request

from app.api.schemas import AppendChatMessageRequest, TriageDecisionResponse
from app.chat.direct_actions import run_direct_action
from app.chat.offers import match_choice, offer_for, pending_choices, settle
from app.chat.triage import TriageIntent
from app.coding.flow import start_coding_run
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


def resolve_choice(
    request: Request, chat_session_id: str, option: dict, body: AppendChatMessageRequest
) -> TriageDecisionResponse:
    """Carry out the option the user picked from an assistant's choices."""
    service = request.app.state.task_service
    payload = option.get("payload") or {}
    origin = payload.get("origin_message_id")
    # The same context the console sends with a chat turn, so times resolve locally.
    source_context = {
        key: value
        for key, value in {
            "client": "chat-choice",
            "local_time": body.local_time,
            "timezone": body.timezone,
        }.items()
        if value
    }
    action = option["action"]
    reply: str | None = None
    blocks: list[dict] = []
    try:
        if action == "start_coding":
            # Agreeing in the chat is enough to start: the agent only works on a branch and
            # opens a draft pull request; merging still needs the approval token.
            message = service.get_user_chat_message(chat_session_id, origin)
            goal = str(payload.get("goal") or "").strip()
            run = start_coding_run(
                service,
                request.app.state.workflow_service,
                request.app.state.repository_registry,
                repository_id=payload["repository_id"],
                request=(
                    f"{goal}\n\nOriginal request: {message.content}"
                    if goal and goal != message.content
                    else message.content
                ),
                chat_session_id=chat_session_id,
                origin_message_id=origin,
                approved_by="chat",
            )
            reply = "Started a coding agent on it. I'll report back here."
            blocks = [{"type": "workflow", "workflow_run_id": run.id}]
        elif action in ("start_research", "start_task"):
            task = service.create_task_from_message(
                chat_session_id,
                origin,
                goal=payload.get("goal"),
                required_capabilities=list(payload.get("capabilities") or []),
                source_context=source_context,
                create_work_item=True,
                space=payload.get("space"),
            )
            reply = "Researching it now." if action == "start_research" else "Started on it."
            if task.work_item_id:
                item = request.app.state.work_item_service.get(task.work_item_id)
                blocks = [{"type": "item", "slug": item.slug}]
        else:  # answer: the original message gets an ordinary chat reply
            service.create_task_from_message(chat_session_id, origin, source_context=source_context)
    except (LookupError, ValueError) as error:
        reply, blocks = f"I couldn't set that up: {error}", []
    if reply:
        service.append_chat_message(chat_session_id, content=reply, role="assistant", blocks=blocks)
    return TriageDecisionResponse(
        intent="direct_action" if action != "answer" else "answer",
        goal=str(payload.get("goal") or ""),
        reason=f"Picked “{option['label']}”",
        confidence=1.0,
        result=reply,
        handled=True,
        source="rules",
    )


#: A coding request this clear starts work right away; less clear ones are offered.
SUPERVISOR_CONFIDENCE = 0.85


def triage_message(
    request: Request, chat_session_id: str, body: AppendChatMessageRequest, message_id: str
) -> TriageDecisionResponse:
    """Decide what a just-stored message needs; never raises (rules are the fallback)."""
    service = request.app.state.task_service
    messages = service.list_chat_messages(chat_session_id, limit=500)
    # An answer to the assistant's open choices (a click sends the label; "yes" or "no"
    # pick the primary or decline option). Anything else lets the choices lapse.
    pending = pending_choices(messages[-10:], current_message_id=message_id)
    if pending is not None:
        offer_message, block = pending
        option = match_choice(body.content, block)
        service.set_message_blocks(
            offer_message.id,
            settle(offer_message.blocks or [], block["id"], chosen=option and option["label"]),
        )
        if option is not None and option["action"] != "reply":
            return resolve_choice(request, chat_session_id, option, body)
    work_items = request.app.state.work_item_service
    focused = work_items.focused(chat_session_id)
    spaces = work_items.space_slugs(focused)
    history = messages[-7:-1]
    open_items = [
        {"slug": item.slug, "title": item.title, "kind": item.kind}
        for item in work_items.list(limit=50)
        if item.status != "done"
    ]
    decision = request.app.state.triager.decide(
        body.content,
        selected_repository_id=body.repository_id,
        focused_items=[
            {"slug": item.slug, "title": item.title, "space": spaces.get(item.space_id)}
            for item in focused
        ],
        recent_turns=[
            (message.role, message.content)
            for message in history
            if message.role in ("user", "assistant")
        ],
        work_items=open_items,
        local_time=body.local_time,
        timezone=body.timezone,
    )
    response = TriageDecisionResponse.model_validate(decision.model_dump(mode="json"))
    if decision.action is not None:
        # T0: done now, without a chat model; the reply lands in the transcript.
        timezone = body.timezone or request.app.state.settings.default_timezone
        try:
            zone(timezone)
        except ValueError:
            timezone = request.app.state.settings.default_timezone
        result = run_direct_action(
            decision.action,
            chat_session_id=chat_session_id,
            items=work_items,
            memory=request.app.state.memory_service,
            schedules=request.app.state.schedule_service,
            timezone=timezone,
        )
        service.append_chat_message(chat_session_id, content=result.reply, role="assistant")
        response.result = result.reply
        response.handled = True
        return response
    # Work goes to the supervisor, which sees every run and can start, inspect or stop
    # them: a clear request for a code change, or any message about coding work.
    if request.app.state.settings.supervisor_enabled and (
        decision.about_work
        or (
            decision.intent == TriageIntent.PROPOSE_CODING
            and decision.repository_id
            and decision.confidence >= SUPERVISOR_CONFIDENCE
        )
    ):
        service.create_task_from_message(
            chat_session_id,
            message_id,
            required_capabilities=["supervision"],
            source_context={
                key: value
                for key, value in {
                    "client": "web-console",
                    "local_time": body.local_time,
                    "timezone": body.timezone,
                }.items()
                if value
            },
        )
        response.handled = True
        return response
    # Proposals are asked in the conversation, with the options as choices to pick.
    offer = offer_for(
        decision,
        origin_message_id=message_id,
        repository_names={
            manifest.id: manifest.name for manifest in request.app.state.repository_registry.list()
        },
    )
    if offer is not None:
        service.append_chat_message(
            chat_session_id, content=offer.reply, role="assistant", blocks=offer.blocks
        )
        response.result = offer.reply
        response.handled = True
    return response
