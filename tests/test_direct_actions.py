from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.chat.triage import TriageIntent, Triager
from app.repositories import RepositoryRegistry
from tests.test_triage import ScriptedChat, suggestion


def _chat(client: TestClient) -> str:
    return client.post("/chat-sessions", json={}).json()["id"]


def _say(client: TestClient, chat_id: str, text: str) -> dict:
    return client.post(f"/chat-sessions/{chat_id}/messages", json={"content": text}).json()


def _last_reply(client: TestClient, chat_id: str) -> str:
    messages = client.get(f"/chat-sessions/{chat_id}/messages").json()["messages"]
    return [message for message in messages if message["role"] == "assistant"][-1]["content"]


def test_list_entries_are_added_and_ticked_off_without_a_model(client: TestClient) -> None:
    groceries = client.post("/work-items", json={"title": "Groceries", "kind": "list"}).json()
    chat = _chat(client)

    added = _say(client, chat, "add oat milk to my groceries list")
    ticked = _say(client, chat, "tick off oat milk")

    assert added["decision"]["intent"] == "direct_action"
    assert added["decision"]["result"] == "Added “oat milk” to #groceries."
    assert ticked["decision"]["result"] == "Checked off “oat milk” on #groceries."
    entries = client.get(f"/work-items/{groceries['id']}").json()["checklist"]
    assert [(entry["text"], entry["done"]) for entry in entries] == [("oat milk", True)]
    assert _last_reply(client, chat) == "Checked off “oat milk” on #groceries."
    # Done on the server: no chat-model run was queued.
    assert client.get(f"/chat-sessions/{chat}/tasks").json()["tasks"] == []


def test_remember_saves_an_inspectable_memory(client: TestClient) -> None:
    chat = _chat(client)

    response = _say(client, chat, "Remember that I prefer window seats.")

    assert response["decision"]["result"] == "I'll remember that: I prefer window seats"
    memories = client.get("/memories").json()["memories"]
    assert [(memory["kind"], memory["content"]) for memory in memories] == [
        ("fact", "I prefer window seats")
    ]


def test_unknown_lists_and_coding_requests_are_not_acted_on(client: TestClient) -> None:
    chat = _chat(client)

    coding = _say(client, chat, "add a login page to the app")
    client.app.state.triager = Triager(
        client.app.state.repository_registry,
        ScriptedChat(
            suggestion(
                intent="direct_action",
                confidence=0.9,
                action={"type": "checklist_add", "text": "bread", "work_item": "nope"},
            )
        ),
    )
    unknown = _say(client, chat, "add bread to the nope list")

    assert coding["decision"]["intent"] != "direct_action"
    assert unknown["decision"]["result"].startswith("I couldn't tell #nope you meant")
    assert client.get("/work-items").json()["work_items"] == []


def test_model_actions_need_confidence() -> None:
    triager = Triager(
        RepositoryRegistry.from_directory("repositories"),
        ScriptedChat(
            suggestion(
                intent="direct_action",
                confidence=0.4,
                action={"type": "remember", "text": "something", "kind": "fact"},
            )
        ),
    )

    decision = triager.decide("maybe remember this?")

    assert (decision.intent, decision.action) == (TriageIntent.ANSWER, None)


def test_model_sees_existing_work_items_for_actions() -> None:
    chat = ScriptedChat(suggestion())
    Triager(RepositoryRegistry.from_directory("repositories"), chat).decide(
        "add eggs", work_items=[{"slug": "groceries", "title": "Groceries", "kind": "list"}]
    )

    request = json.loads(chat.requests[0][1].content)
    assert request["work_items"] == [{"tag": "groceries", "title": "Groceries", "kind": "list"}]
    assert "direct_action" in chat.requests[0][0].content


def test_a_note_said_in_chat_is_saved_to_notes(client: TestClient) -> None:
    chat = _chat(client)

    response = _say(client, chat, "note: call the dentist on Monday")

    assert response["decision"]["intent"] == "direct_action"
    assert response["decision"]["result"] == "Saved to Notes: “call the dentist on Monday”"
    notes = client.get("/notes").json()["notes"]
    assert [(note["title"], note["body"]) for note in notes] == [("", "call the dentist on Monday")]
    messages = client.get(f"/chat-sessions/{chat}/messages").json()["messages"]
    assert messages[-1]["blocks"] == [{"type": "link", "href": "#/notes", "label": "Open Notes"}]
    assert client.get(f"/chat-sessions/{chat}/tasks").json()["tasks"] == []  # no model involved


def test_note_phrasings_and_what_is_not_a_note() -> None:
    from app.chat.triage import rule_action

    for text, expected in [
        ("Note: buy oat milk", "buy oat milk"),
        (
            "make a note: the router password is on the fridge",
            "the router password is on the fridge",
        ),
        ("please add a note: renew passport", "renew passport"),
        ("note to self: ask about the invoice", "ask about the invoice"),
        ("anteckna: köp mjölk", "köp mjölk"),
        ("ny anteckning: ring tandläkaren", "ring tandläkaren"),
        ("note: first line\nsecond line", "first line\nsecond line"),
    ]:
        action = rule_action(text)
        assert action is not None and action.type == "note_add", text
        assert action.text == expected

    for text in [
        "note that the build is slow",
        "please note the deadline moved",
        "notes on the meeting were shared",
        "I wrote a note about it",
    ]:
        assert rule_action(text) is None, text


def test_only_a_note_may_be_longer_than_300_characters() -> None:
    from pydantic import ValidationError

    from app.chat.triage import DirectAction

    assert DirectAction(type="note_add", text="x" * 4000).text
    for kind in ("remember", "checklist_add"):
        try:
            DirectAction(type=kind, text="x" * 301)
        except ValidationError:
            continue
        raise AssertionError(f"{kind} accepted 301 characters")
