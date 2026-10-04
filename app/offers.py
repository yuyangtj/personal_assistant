"""Chat-first proposals: the assistant offers work in the conversation, you answer.

When triage thinks a message needs a coding workflow or a task, the assistant asks in
the chat with a ``choices`` block instead of a separate card. The next message settles
it: a click sends the option's label, and typing "yes" or "no" picks the primary or
decline option. Anything else lets the offer lapse, so a stray "yes" later does nothing.
Proposing a coding workflow runs nothing; starting it still needs an explicit approval.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.blocks import ChoiceOption, ChoicesBlock, validate_blocks
from app.persistence.models import ChatMessageModel
from app.triage import TriageDecision, TriageIntent

_YES = re.compile(
    r"^(yes|yeah|yep|yup|sure|ok|okay|go ahead|go for it|do it|please do|pls do|"
    r"sounds good|let'?s do it|start it|好|好的|ja)( please| pls| thanks)?[\s.!]*$",
    re.IGNORECASE,
)
_NO = re.compile(
    r"^(no|nope|nah|not now|cancel|never ?mind|skip|no thanks|don'?t)( thanks| thank you)?"
    r"[\s.!]*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Offer:
    reply: str
    blocks: list[dict[str, Any]] = field(default_factory=list)


def offer_for(
    decision: TriageDecision, *, origin_message_id: str, repository_names: dict[str, str]
) -> Offer | None:
    """The assistant's question for a proposal or clarification, or None to just answer."""
    goal = decision.goal
    quoted = goal.strip().rstrip(".")  # it sits inside a sentence that adds its own
    answer = ChoiceOption(
        label="Just chat about it",
        action="answer",
        decline=True,
        payload={"origin_message_id": origin_message_id},
    )
    if decision.intent == TriageIntent.PROPOSE_CODING and decision.repository_id:
        name = repository_names.get(decision.repository_id, decision.repository_id)
        options = [
            ChoiceOption(
                label="Set up coding workflow",
                action="start_coding",
                primary=True,
                payload={
                    "repository_id": decision.repository_id,
                    "goal": goal,
                    "origin_message_id": origin_message_id,
                },
            ),
            answer,
        ]
        reply = (
            f"That sounds like a code change in {name}: “{quoted}”. Want me to set up a "
            "coding workflow? Nothing runs until you approve it."
        )
    elif decision.intent == TriageIntent.PROPOSE_TASK:
        research = "web_research" in decision.capabilities
        options = [
            ChoiceOption(
                label="Research it" if research else "Track it as a task",
                action="start_research" if research else "start_task",
                primary=True,
                payload={
                    "goal": goal,
                    "capabilities": list(decision.capabilities),
                    "space": decision.space,
                    "origin_message_id": origin_message_id,
                },
            ),
            answer,
        ]
        reply = (
            f"I can research this on the web: “{quoted}”."
            if research
            else f"This looks like something to keep track of: “{quoted}”."
        )
    elif decision.intent == TriageIntent.CLARIFY and decision.options:
        options = []
        for index, option in enumerate(decision.options[:4]):
            if option.intent == TriageIntent.PROPOSE_CODING and option.repository_id:
                action, payload = (
                    "start_coding",
                    {
                        "repository_id": option.repository_id,
                        "goal": goal,
                        "origin_message_id": origin_message_id,
                    },
                )
            elif option.intent == TriageIntent.PROPOSE_TASK:
                action, payload = (
                    "start_task",
                    {
                        "goal": goal,
                        "capabilities": list(decision.capabilities),
                        "space": decision.space,
                        "origin_message_id": origin_message_id,
                    },
                )
            else:
                action, payload = "answer", {"origin_message_id": origin_message_id}
            options.append(
                ChoiceOption(
                    label=option.label[:60],
                    action=action,
                    primary=index == 0 and action != "answer",
                    decline=action == "answer",
                    payload=payload,
                )
            )
        reply = decision.question or "What should I do with this?"
    else:
        return None
    block = ChoicesBlock(options=options).model_dump(mode="json")
    return Offer(reply=reply, blocks=validate_blocks([block]))


def pending_choices(
    messages: Sequence[ChatMessageModel], *, current_message_id: str
) -> tuple[ChatMessageModel, dict[str, Any]] | None:
    """The open choices the current message answers: on the turn right before it."""
    earlier = [message for message in messages if message.id != current_message_id]
    for message in reversed(earlier):
        if message.role == "system":
            continue  # task references don't count as a turn
        if message.role != "assistant":
            return None
        for block in message.blocks or []:
            if block.get("type") == "choices" and block.get("state") == "open":
                return message, block
        return None
    return None


def match_choice(text: str, block: dict[str, Any]) -> dict[str, Any] | None:
    """The option a reply picks: its label, or yes/no for the primary/decline option."""
    normalized = " ".join(text.split()).strip().lower()
    options = block.get("options", [])
    for option in options:
        if normalized == option["label"].lower():
            return option
    if _YES.match(normalized):
        return next((option for option in options if option.get("primary")), None)
    if _NO.match(normalized):
        return next((option for option in options if option.get("decline")), None)
    return None


def settle(blocks: list[dict[str, Any]], block_id: str, *, chosen: str | None) -> list[dict]:
    """The message's blocks with one choices block marked chosen (or lapsed)."""
    settled = []
    for block in blocks:
        if block.get("id") == block_id:
            block = {**block, "state": "chosen" if chosen else "lapsed", "chosen": chosen}
        settled.append(block)
    return settled
