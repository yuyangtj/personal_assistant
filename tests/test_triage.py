from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.execution.conversation import SYSTEM_PROMPT as CONVERSATION_PROMPT
from app.integrations.chat import ChatCompletion, ChatMessage
from app.repositories import RepositoryRegistry
from app.triage import TriageIntent, Triager


class ScriptedChat:
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


def suggestion(**fields) -> str:
    return json.dumps(
        {"intent": "answer", "repository_id": None, "goal": "", "confidence": 0.9, "reason": "r"}
        | fields
    )


@pytest.fixture
def repositories() -> RepositoryRegistry:
    return RepositoryRegistry.from_directory("repositories")


# --- keyword rules (model off or failing) ----------------------------------------


def test_rules_answer_small_talk(repositories: RepositoryRegistry) -> None:
    decision = Triager(repositories).decide("good morning!")

    assert (decision.intent, decision.source) == (TriageIntent.ANSWER, "rules")


def test_rules_route_a_coding_request_by_repository_alias(
    repositories: RepositoryRegistry,
) -> None:
    decision = Triager(repositories).decide("Fix the theme toggle bug in the personal assistant")

    assert decision.intent == TriageIntent.PROPOSE_CODING
    assert decision.repository_id == "personal-assistant"


def test_rules_ask_which_project_when_it_is_unclear(repositories: RepositoryRegistry) -> None:
    decision = Triager(repositories).decide("Please fix the login bug")

    assert decision.intent == TriageIntent.CLARIFY
    assert [option.repository_id for option in decision.options] == [
        "analytics-agent-playground",
        "personal-assistant",
        None,
    ]
    assert decision.options[-1].intent == TriageIntent.ANSWER


def test_rules_ask_when_a_repository_is_selected(repositories: RepositoryRegistry) -> None:
    decision = Triager(repositories).decide(
        "In the web console, the toggle looks off", selected_repository_id="personal-assistant"
    )

    assert decision.intent == TriageIntent.CLARIFY
    assert decision.options[0].repository_id == "personal-assistant"


# --- model suggestion checked by rules ----------------------------------------------


def test_model_routes_casual_phrasing_to_coding(repositories: RepositoryRegistry) -> None:
    chat = ScriptedChat(
        suggestion(
            intent="propose_coding",
            repository_id="personal-assistant",
            goal="Update the light/dark toggle icon",
            confidence=0.92,
        )
    )

    decision = Triager(repositories, chat).decide(
        "In the web console, change the icon on the toggle",
        focused_items=[{"slug": "toggle-icon", "title": "Toggle icon", "space": "coding"}],
        recent_turns=[("user", "hi"), ("assistant", "hello")],
    )

    assert decision.intent == TriageIntent.PROPOSE_CODING
    assert (decision.repository_id, decision.source) == ("personal-assistant", "model")
    assert decision.goal == "Update the light/dark toggle icon"
    request = json.loads(chat.requests[0][1].content)
    assert request["work_items_in_focus"][0]["tag"] == "toggle-icon"
    assert {repo["id"] for repo in request["repositories"]} == {
        "personal-assistant",
        "analytics-agent-playground",
    }
    assert "untrusted data" in chat.requests[0][0].content


def test_model_cannot_invent_a_repository(repositories: RepositoryRegistry) -> None:
    chat = ScriptedChat(
        suggestion(intent="propose_coding", repository_id="secret-repo", confidence=0.9)
    )

    decision = Triager(repositories, chat).decide("Add a feature")

    assert decision.intent == TriageIntent.CLARIFY
    assert decision.repository_id is None


def test_picker_overrides_the_models_repository(repositories: RepositoryRegistry) -> None:
    chat = ScriptedChat(
        suggestion(intent="propose_coding", repository_id="personal-assistant", confidence=0.9)
    )

    decision = Triager(repositories, chat).decide(
        "Add a metric", selected_repository_id="analytics"
    )

    assert decision.repository_id == "analytics-agent-playground"


def test_low_confidence_asks_instead_of_guessing(repositories: RepositoryRegistry) -> None:
    chat = ScriptedChat(suggestion(intent="propose_task", goal="Compare desks", confidence=0.3))

    decision = Triager(repositories, chat).decide("desks?")

    assert decision.intent == TriageIntent.CLARIFY
    assert [option.intent for option in decision.options] == [
        TriageIntent.PROPOSE_TASK,
        TriageIntent.ANSWER,
    ]


def test_invalid_model_output_is_repaired_once_then_falls_back(
    repositories: RepositoryRegistry,
) -> None:
    repaired = ScriptedChat("not json", suggestion(intent="propose_task", confidence=0.8))
    assert Triager(repositories, repaired).decide("Plan my trip").source == "model"

    broken = ScriptedChat('{"intent": "deploy"}', "nope")
    assert Triager(repositories, broken).decide("good morning").source == "rules"

    failing = ScriptedChat(TimeoutError("slow provider"))
    assert Triager(repositories, failing).decide("good morning").source == "rules"


# --- API and chat hand-off ----------------------------------------------------------


def test_posting_a_message_returns_a_decision(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()

    answered = client.post(
        f"/chat-sessions/{chat['id']}/messages", json={"content": "good morning"}
    ).json()
    client.app.state.triager = Triager(
        client.app.state.repository_registry,
        ScriptedChat(
            suggestion(intent="propose_coding", repository_id="personal-assistant", confidence=0.9)
        ),
    )
    coding = client.post(
        f"/chat-sessions/{chat['id']}/messages",
        json={"content": "the toggle looks wrong, can you sort it", "repository_id": None},
    ).json()

    assert answered["decision"]["intent"] == "answer"
    assert coding["decision"]["intent"] == "propose_coding"
    assert coding["decision"]["repository_id"] == "personal-assistant"
    assert coding["decision"]["source"] == "model"


def test_chat_model_hands_coding_requests_to_the_workflow() -> None:
    assert "coding workflow" in CONVERSATION_PROMPT
    assert "never say you cannot work on their" in CONVERSATION_PROMPT


def test_null_goal_and_reason_are_accepted(repositories: RepositoryRegistry) -> None:
    chat = ScriptedChat(
        json.dumps({"intent": "answer", "goal": None, "reason": None, "confidence": 1})
    )

    decision = Triager(repositories, chat).decide("how are you today?")

    assert (decision.intent, decision.source) == (TriageIntent.ANSWER, "model")
    assert decision.goal == "how are you today?"


def test_chat_model_offers_research_only_when_available() -> None:
    from app.execution.conversation import ConversationExecutor

    with_research = ConversationExecutor(ScriptedChat(), research_available=True)
    without = ConversationExecutor(ScriptedChat())

    assert "research agent can" in with_research._messages("find one", (), {})[0].content
    assert "research agent" not in without._messages("find one", (), {})[0].content
