"""The one "Assistant" conversation: made once, always first, never archived or deleted."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_assistant_is_made_once_and_comes_first(client: TestClient) -> None:
    first = client.get("/chat-sessions/home").json()
    again = client.get("/chat-sessions/home").json()
    other = client.post("/chat-sessions", json={}).json()

    assert first["id"] == again["id"] and first["title"] == "Assistant" and first["home"] is True
    listed = client.get("/chat-sessions").json()["sessions"]
    assert [chat["id"] for chat in listed][:2] == [first["id"], other["id"]]
    assert [chat["home"] for chat in listed] == [True, False]


def test_assistant_keeps_its_name_and_cannot_be_archived_or_deleted(client: TestClient) -> None:
    home = client.get("/chat-sessions/home").json()["id"]

    client.post(f"/chat-sessions/{home}/messages", json={"content": "how is it going?"})

    assert client.get(f"/chat-sessions/{home}").json()["title"] == "Assistant"
    assert client.patch(f"/chat-sessions/{home}", json={"archived": True}).status_code == 409
    assert client.delete(f"/chat-sessions/{home}").status_code == 409
    assert client.patch(f"/chat-sessions/{home}", json={"pinned": True}).status_code == 200
