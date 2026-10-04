from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.blocks import model_blocks, validate_blocks
from app.execution.conversation import reply_blocks
from app.offers import match_choice
from app.triage import Triager
from tests.test_triage import ScriptedChat, suggestion

CODING = suggestion(
    intent="propose_coding",
    repository_id="personal-assistant",
    goal="Update the browser tab icon.",
    confidence=0.9,
)
RESEARCH = suggestion(
    intent="propose_task",
    goal="Compare standing desks under 5000 kr",
    confidence=0.9,
    needs_web_search=True,
    space="shopping",
)


def _script(client: TestClient, *replies: str) -> None:
    client.app.state.triager = Triager(
        client.app.state.repository_registry,
        ScriptedChat(*replies),
        research_available=True,
        spaces=client.app.state.triager.spaces,
    )


def _say(client: TestClient, chat_id: str, text: str) -> dict:
    return client.post(f"/chat-sessions/{chat_id}/messages", json={"content": text}).json()


def _messages(client: TestClient, chat_id: str) -> list[dict]:
    return client.get(f"/chat-sessions/{chat_id}/messages").json()["messages"]


# --- matching -------------------------------------------------------------------------

BLOCK = {
    "options": [
        {"label": "Set up coding workflow", "primary": True},
        {"label": "Just chat about it", "decline": True},
    ]
}


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("yes", "Set up coding workflow"),
        ("Yes please!", "Set up coding workflow"),
        ("go ahead", "Set up coding workflow"),
        ("set up coding workflow", "Set up coding workflow"),
        ("not now", "Just chat about it"),
        ("No thanks.", "Just chat about it"),
        ("yes but in the other repo", None),
        ("what would that change?", None),
    ],
)
def test_replies_pick_options_by_label_or_yes_no(text: str, label: str | None) -> None:
    picked = match_choice(text, BLOCK)

    assert (picked or {}).get("label") == label


# --- the conversation flow -----------------------------------------------------------


def test_coding_is_offered_in_chat_and_yes_proposes_the_workflow(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()["id"]
    _script(client, CODING)

    asked = _say(client, chat, "can you update the icon to a different one")

    assert asked["decision"]["handled"] is True
    offer = _messages(client, chat)[-1]
    assert offer["role"] == "assistant"
    assert "in Personal Assistant: “Update the browser tab icon”. Want me" in offer["content"]
    (choices,) = offer["blocks"]
    assert [option["label"] for option in choices["options"]] == [
        "Set up coding workflow",
        "Just chat about it",
    ]
    assert client.get("/workflow-runs").json()["runs"] == []

    confirmed = _say(client, chat, "yes")  # no triage model call: the choice settles it

    assert confirmed["decision"]["handled"] is True
    (run,) = client.get("/workflow-runs").json()["runs"]
    assert run["status"] == "proposed"  # still needs the separate approval
    assert run["origin_message_id"] == asked["message"]["id"]
    assert run["input"]["request"].startswith("Update the browser tab icon")
    messages = _messages(client, chat)
    assert messages[-1]["blocks"] == [{"type": "workflow", "workflow_run_id": run["id"]}]
    settled = next(message for message in messages if message["id"] == offer["id"])
    assert settled["blocks"][0]["state"] == "chosen"
    assert settled["blocks"][0]["chosen"] == "Set up coding workflow"


def test_research_is_started_from_its_choice(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()["id"]
    _script(client, RESEARCH)
    _say(client, chat, "find me a standing desk")

    _say(client, chat, "Research it")

    (task,) = client.get(f"/chat-sessions/{chat}/tasks").json()["tasks"]
    assert task["required_capabilities"] == ["web_research"]
    assert task["work_item_id"] is not None
    assert _messages(client, chat)[-1]["blocks"][0]["type"] == "item"


def test_declining_answers_in_chat_and_creates_nothing(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()["id"]
    _script(client, CODING)
    asked = _say(client, chat, "can you update the icon")

    _say(client, chat, "no")

    assert client.get("/workflow-runs").json()["runs"] == []
    (task,) = client.get(f"/chat-sessions/{chat}/tasks").json()["tasks"]
    assert task["origin_message_id"] == asked["message"]["id"]  # an ordinary reply
    assert task["required_capabilities"] == []


def test_an_unrelated_message_lets_the_offer_lapse(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()["id"]
    _script(client, CODING, suggestion(intent="answer"), suggestion(intent="answer"))
    _say(client, chat, "can you update the icon")

    moved_on = _say(client, chat, "actually, what's the weather like?")
    late_yes = _say(client, chat, "yes")

    assert moved_on["decision"]["handled"] is False  # triaged as usual
    assert late_yes["decision"]["handled"] is False  # nothing is pending any more
    offer = next(m for m in _messages(client, chat) if m["blocks"])
    assert offer["blocks"][0]["state"] == "lapsed"
    assert client.get("/workflow-runs").json()["runs"] == []


# --- blocks ----------------------------------------------------------------------------


def test_blocks_are_data_from_a_fixed_set() -> None:
    with pytest.raises(ValueError):
        validate_blocks([{"type": "html", "body": "<script>"}])
    with pytest.raises(ValueError):
        validate_blocks([{"type": "link", "label": "x", "href": "javascript:alert(1)"}])
    with pytest.raises(ValueError):
        validate_blocks([{"type": "choices", "options": [{"label": str(n)} for n in range(5)]}])
    assert validate_blocks([{"type": "link", "label": "PR", "href": "https://github.com/x"}])


def test_model_choices_and_links_are_validated_or_dropped() -> None:
    blocks = model_blocks(
        {
            "choices": ["Standing desk", "Desk converter"],
            "links": [
                {"label": "Guide", "href": "https://example.com/desks"},
                {"label": "Bad", "href": "data:text/html,hi"},
            ],
        }
    )

    assert [block["type"] for block in blocks] == ["choices", "link"]
    assert all(option["action"] == "reply" for option in blocks[0]["options"])
    assert reply_blocks('{"reply": "Hi", "choices": "not a list"}') == []
    assert reply_blocks("plain text") == []


def test_chat_model_choices_reach_the_transcript(client: TestClient) -> None:
    service = client.app.state.task_service
    chat = client.post("/chat-sessions", json={}).json()["id"]
    message = _say(client, chat, "good morning")["message"]
    task = service.create_task_from_message(chat, message["id"])
    service.claim_next_task(worker_id="w", lease_seconds=30)
    execution_id = service.start_execution(
        task.id, capability_id="conversation", executor_id="fake", execution_input={}
    )
    service.start_validation(task.id, execution_id, output={"summary": "answered"})

    service.complete_task(
        task.id, execution_id, reply="Which one?", blocks=model_blocks({"choices": ["A", "B"]})
    )

    reply = _messages(client, chat)[-1]
    assert reply["content"] == "Which one?"
    assert [option["label"] for option in reply["blocks"][0]["options"]] == ["A", "B"]
