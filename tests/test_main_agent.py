from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient


class NoTriage:
    """Fails the test if the triage model is asked anything."""

    def decide(self, *args, **kwargs):
        raise AssertionError("triage ran")


@pytest.fixture
def main_agent(client: TestClient) -> TestClient:
    state = client.app.state
    state.settings = replace(state.settings, main_agent_enabled=True)
    state.triager = NoTriage()
    return client


def _say(client: TestClient, chat_id: str, text: str) -> dict:
    return client.post(f"/chat-sessions/{chat_id}/messages", json={"content": text}).json()


def test_every_message_goes_to_the_chat_agent_without_triage(main_agent: TestClient) -> None:
    chat = main_agent.post("/chat-sessions", json={}).json()["id"]

    for text in (
        "how's it going?",
        "fix the theme toggle in the personal assistant",
        "how much did I spend at Lidl last month?",
        "find the cheapest train to Copenhagen",
    ):
        decision = _say(main_agent, chat, text)["decision"]
        assert (decision["route"], decision["source"], decision["handled"]) == (
            "main agent",
            "main-agent",
            False,
        ), text
    # No offers, no choices: the client queues one chat reply per message.
    messages = main_agent.get(f"/chat-sessions/{chat}/messages").json()["messages"]
    assert [m["role"] for m in messages] == ["user"] * 4


def test_plain_bookkeeping_is_still_done_instantly(main_agent: TestClient) -> None:
    groceries = main_agent.post("/work-items", json={"title": "Groceries", "kind": "list"}).json()
    chat = main_agent.post("/chat-sessions", json={}).json()["id"]

    decision = _say(main_agent, chat, "add oat milk to my groceries list")["decision"]

    assert decision["handled"] and decision["route"] == "action: checklist_add"
    entries = main_agent.get(f"/work-items/{groceries['id']}").json()["checklist"]
    assert [e["text"] for e in entries] == ["oat milk"]


def test_private_memories_still_never_reach_a_model(main_agent: TestClient) -> None:
    chat = main_agent.post("/chat-sessions", json={}).json()["id"]

    _say(main_agent, chat, "remember privately that my PIN hint is the cat")

    (memory,) = main_agent.get("/memories").json()["memories"]
    assert memory["private"] is True
    assert main_agent.get(f"/chat-sessions/{chat}/tasks").json()["tasks"] == []


def test_the_flag_is_off_by_default(client: TestClient) -> None:
    assert client.app.state.settings.main_agent_enabled is False
