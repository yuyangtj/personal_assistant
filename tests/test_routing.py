from __future__ import annotations

from app.execution.routing import adapter_for

ALL = {"coding-pull-request", "tool-agent", "model-conversation", "fake"}


def test_a_chat_reply_goes_to_the_conversation_model_before_the_fake() -> None:
    assert adapter_for([], ALL) == "model-conversation"
    assert adapter_for([], {"fake"}) == "fake"


def test_specialist_work_goes_to_the_adapter_providing_all_of_it() -> None:
    assert adapter_for(["web_research"], ALL) == "tool-agent"
    assert adapter_for(["coding", "pull_request_creation"], ALL) == "coding-pull-request"
    assert adapter_for(["coding-pull-request"], ALL) == "coding-pull-request"


def test_nothing_is_chosen_when_no_installed_adapter_provides_everything() -> None:
    assert adapter_for(["coding"], {"model-conversation", "fake"}) is None
    assert adapter_for(["web_research", "coding"], ALL) is None
    assert adapter_for(["teleport"], ALL) is None
