"""Fakes shared by the tests."""

from __future__ import annotations

from app.integrations.chat import ChatCompletion, ChatMessage


class ScriptedChat:
    """A chat model that answers with the given replies in order (an exception is raised)."""

    provider = "scripted"
    model = "scripted"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[list[ChatMessage]] = []

    def complete(self, messages, *, max_tokens: int = 700) -> ChatCompletion:
        self.requests.append(list(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ChatCompletion(text=reply, model=self.model)

    def close(self) -> None:
        pass
