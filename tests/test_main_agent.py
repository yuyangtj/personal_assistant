"""The chat agent is the only thing that decides what a message means."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _say(client: TestClient, chat_id: str, text: str) -> dict:
    return client.post(f"/chat-sessions/{chat_id}/messages", json={"content": text}).json()


def test_every_message_goes_to_the_chat_agent(client: TestClient) -> None:
    client.post("/work-items", json={"title": "Groceries", "kind": "list"})
    chat = client.post("/chat-sessions", json={}).json()["id"]

    texts = (
        "how's it going?",
        "fix the theme toggle in the personal assistant",
        "how much did I spend at Lidl last month?",
        # Bookkeeping too: the agent has a tool for each, and reads the whole message
        # ("remember … and add … and remind …" once went to the wrong rule).
        "add oat milk to my groceries list",
        "remember that I like oat milk",
        "Yes please do",
    )
    for text in texts:
        decision = _say(client, chat, text)["decision"]
        assert (decision["route"], decision["source"], decision["handled"]) == (
            "main agent",
            "main-agent",
            False,
        ), text
    # No offers, no choices: the client queues one chat reply per message.
    messages = client.get(f"/chat-sessions/{chat}/messages").json()["messages"]
    assert [m["role"] for m in messages] == ["user"] * len(texts)


def test_an_explicit_note_is_saved_at_once(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()["id"]

    decision = _say(client, chat, "note: the cabin wifi password is on the fridge")["decision"]

    assert decision["handled"] and decision["route"] == "action: note_add"
    (note,) = client.app.state.note_service.list()
    assert note.body == "the cabin wifi password is on the fridge"


def test_a_suggestion_button_is_settled_by_whatever_is_said_next(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()["id"]
    service = client.app.state.task_service
    options = [{"label": "Add sweet dishes too"}, {"label": "Find street food"}]
    service.append_chat_message(
        chat, content="Done.", role="assistant", blocks=[{"type": "choices", "options": options}]
    )

    decision = _say(client, chat, "add sweet dishes too")["decision"]

    assert decision["route"] == "main agent"  # a tapped suggestion is just a message
    (block,) = service.list_chat_messages(chat)[0].blocks
    assert (block["state"], block["chosen"]) == ("chosen", "Add sweet dishes too")


def test_private_memories_still_never_reach_a_model(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()["id"]

    _say(client, chat, "remember privately that my PIN hint is the cat")

    (memory,) = client.get("/memories").json()["memories"]
    assert memory["private"] is True
    assert client.get(f"/chat-sessions/{chat}/tasks").json()["tasks"] == []
