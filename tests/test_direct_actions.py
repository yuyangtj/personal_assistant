"""The bookkeeping actions the chat agent's tools run, and the one instant rule ("note:")."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.chat.direct_actions import DirectAction, note_rule, run_direct_action


def _chat(client: TestClient) -> str:
    return client.post("/chat-sessions", json={}).json()["id"]


def _say(client: TestClient, chat_id: str, text: str) -> dict:
    return client.post(f"/chat-sessions/{chat_id}/messages", json={"content": text}).json()


def _do(client: TestClient, chat_id: str, timezone: str = "UTC", **action) -> str:
    """One action, as the agent's tool runs it; the sentence it replies with."""
    state = client.app.state
    result = run_direct_action(
        DirectAction(**action),
        chat_session_id=chat_id,
        items=state.work_item_service,
        memory=state.memory_service,
        schedules=state.schedule_service,
        notes=state.note_service,
        tasks=state.task_service,
        timezone=timezone,
    )
    return result.reply


def test_list_entries_are_added_and_ticked_off(client: TestClient) -> None:
    groceries = client.post("/work-items", json={"title": "Groceries", "kind": "list"}).json()
    chat = _chat(client)

    added = _do(client, chat, type="checklist_add", text="oat milk", work_item="groceries")
    ticked = _do(client, chat, type="checklist_check", text="oat milk", work_item="groceries")

    assert added == "Added “oat milk” to #groceries."
    assert ticked == "Checked off “oat milk” on #groceries."
    entries = client.get(f"/work-items/{groceries['id']}").json()["checklist"]
    assert [(entry["text"], entry["done"]) for entry in entries] == [("oat milk", True)]


def test_the_shopping_list_starts_with_its_first_entry(client: TestClient) -> None:
    chat = _chat(client)

    first = _do(client, chat, type="checklist_add", text="oat milk", work_item="groceries")
    second = _do(client, chat, type="checklist_add", text="bread", work_item="shopping list")

    assert first == "Started your #groceries list with “oat milk”."
    assert second == "Added “bread” to #groceries."
    (groceries,) = client.get("/work-items?kind=list").json()["work_items"]
    assert (groceries["slug"], groceries["title"]) == ("groceries", "Groceries")
    entries = client.get(f"/work-items/{groceries['id']}").json()["checklist"]
    assert [entry["text"] for entry in entries] == ["oat milk", "bread"]


def test_a_list_of_your_own_called_shopping_is_used_as_it_is(client: TestClient) -> None:
    shopping = client.post("/work-items", json={"title": "Shopping", "kind": "list"}).json()
    chat = _chat(client)

    added = _do(client, chat, type="checklist_add", text="soap", work_item="#shopping")

    assert added == "Added “soap” to #shopping."
    assert len(client.get("/work-items?kind=list").json()["work_items"]) == 1
    assert client.get(f"/work-items/{shopping['id']}").json()["checklist"][0]["text"] == "soap"


def test_only_the_shopping_list_is_started_by_itself() -> None:
    from app.domain.work_items import is_grocery_list

    assert all(
        is_grocery_list(name)
        for name in ["groceries", "#groceries", "Shopping list", "the grocery list", "inköpslista"]
    )
    assert not any(is_grocery_list(name) for name in [None, "", "camping", "app", "grocery-run"])


def test_remember_saves_an_inspectable_memory(client: TestClient) -> None:
    chat = _chat(client)

    reply = _do(client, chat, type="remember", text="I prefer window seats", kind="fact")

    assert reply == "I'll remember that: I prefer window seats"
    memories = client.get("/memories").json()["memories"]
    assert [(memory["kind"], memory["content"]) for memory in memories] == [
        ("fact", "I prefer window seats")
    ]


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
        action = note_rule(text)
        assert action is not None and action.type == "note_add", text
        assert action.text == expected

    for text in [
        "note that the build is slow",
        "please note the deadline moved",
        "notes on the meeting were shared",
        "I wrote a note about it",
    ]:
        assert note_rule(text) is None, text


def test_only_a_note_is_done_without_the_agent() -> None:
    for text in [
        "remember I like oat milk, add it to groceries, and remind me tomorrow at 9",
        "remember that Anna and I swim on Mondays",
        "add eggs and butter to groceries",
        "add oat milk to groceries",
        "tick off oat milk",
        "forget the old address",
        "what routines do I have?",
    ]:
        assert note_rule(text) is None, text
    assert note_rule("note: buy bread, and remind me later").type == "note_add"


def test_only_a_note_may_be_longer_than_300_characters() -> None:
    from pydantic import ValidationError

    assert DirectAction(type="note_add", text="x" * 4000).text
    for kind in ("remember", "checklist_add"):
        try:
            DirectAction(type=kind, text="x" * 301)
        except ValidationError:
            continue
        raise AssertionError(f"{kind} accepted 301 characters")
