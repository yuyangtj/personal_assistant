"""Organizing the chat list: pin, archive, delete, and sections per repository."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _chat(client: TestClient, title: str) -> str:
    return client.post("/chat-sessions", json={"title": title}).json()["id"]


def _listed(client: TestClient, query: str = "") -> dict[str, dict]:
    return {c["title"]: c for c in client.get(f"/chat-sessions{query}").json()["sessions"]}


def test_pinning_and_archiving_keep_a_chat_in_its_place_in_time(client: TestClient) -> None:
    groceries = _chat(client, "Groceries")
    _chat(client, "Trip")
    before = _listed(client)["Groceries"]["updated_at"]

    client.patch(f"/chat-sessions/{groceries}", json={"pinned": True})
    assert _listed(client)["Groceries"]["pinned"] is True
    assert _listed(client)["Groceries"]["updated_at"] == before

    client.patch(f"/chat-sessions/{groceries}", json={"archived": True})
    assert set(_listed(client)) == {"Trip"}
    archived = _listed(client, "?archived=true")["Groceries"]
    assert archived["archived"] is True and archived["pinned"] is False  # archiving unpins


def test_an_archived_chat_keeps_working_and_comes_back_when_you_write(
    client: TestClient,
) -> None:
    chat = _chat(client, "Old")
    client.patch(f"/chat-sessions/{chat}", json={"archived": True})
    service = client.app.state.task_service

    # A routine result (assistant) lands without bringing it back...
    service.append_chat_message(chat, content="Weekly summary", role="assistant")
    assert "Old" not in _listed(client)
    # ...but writing in it does.
    service.append_chat_message(chat, content="thanks")
    assert "Old" in _listed(client)


def test_deleting_a_chat_removes_its_messages_but_keeps_its_work(client: TestClient) -> None:
    chat = _chat(client, "To delete")
    service = client.app.state.task_service
    posted = service.append_chat_message(chat, content="research vacuums")
    task = service.create_task_from_message(chat, posted.message.id)
    memory = client.app.state.memory_service.create(
        kind="fact", content="I like Roborock", chat_session_id=chat
    )

    assert client.delete(f"/chat-sessions/{chat}").status_code == 204

    assert client.get(f"/chat-sessions/{chat}").status_code == 404
    assert client.app.state.task_service.get_task(task.id).chat_session_id is None
    kept = client.get("/memories").json()["memories"]
    assert [(m["id"], m["source_chat_session_id"]) for m in kept] == [(memory.id, None)]
    assert client.delete(f"/chat-sessions/{chat}").status_code == 404


def test_chats_are_grouped_by_the_repository_of_their_coding_work(client: TestClient) -> None:
    coding = _chat(client, "Fix the toggle")
    _chat(client, "Dinner ideas")
    client.app.state.task_service.create_task(
        request="fix the toggle",
        chat_session_id=coding,
        source_context={"repository_id": "personal-assistant"},
    )

    listed = _listed(client)

    assert listed["Fix the toggle"]["repository_id"] == "personal-assistant"
    assert listed["Dinner ideas"]["repository_id"] is None
