from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.repositories import RepositoryRegistry
from app.spaces import SpaceRegistry
from app.triage import Triager
from tests.test_triage import ScriptedChat, suggestion


def test_space_packs_load_and_require_the_core_spaces(tmp_path: Path) -> None:
    registry = SpaceRegistry.from_directory("spaces")
    assert [space.slug for space in registry.list()] == ["coding", "general", "shopping"]
    assert "Purchases always need your confirmation" in registry.get("shopping").description

    (tmp_path / "home.yaml").write_text("slug: home\nname: Home\nkind: home\n")
    with pytest.raises(ValueError, match="general space must be declared"):
        SpaceRegistry.from_directory(tmp_path)
    (tmp_path / "bad.yaml").write_text("slug: Bad Slug\nname: x\nkind: x\n")
    with pytest.raises(ValueError, match="Invalid space manifest"):
        SpaceRegistry.from_directory(tmp_path)


def test_triage_places_new_work_in_a_known_space() -> None:
    spaces = SpaceRegistry.from_directory("spaces")
    repositories = RepositoryRegistry.from_directory("repositories")
    chat = ScriptedChat(
        suggestion(intent="propose_task", space="shopping", confidence=0.9),
        suggestion(intent="propose_task", space="mars", confidence=0.9),
    )
    triager = Triager(repositories, chat, spaces=spaces)

    assert triager.decide("find me a rain jacket").space == "shopping"
    assert triager.decide("plan a trip to mars").space is None
    request = json.loads(chat.requests[0][1].content)
    assert {space["slug"] for space in request["spaces"]} == {"coding", "general", "shopping"}


def test_confirmed_task_creates_its_item_in_the_chosen_space(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()
    first = client.post(
        f"/chat-sessions/{chat['id']}/messages", json={"content": "find me a rain jacket"}
    ).json()["message"]

    task = client.post(
        f"/chat-sessions/{chat['id']}/messages/{first['id']}/task",
        json={"create_work_item": True, "space": "shopping", "goal": "Find a rain jacket"},
    ).json()
    unknown_chat = client.post("/chat-sessions", json={}).json()
    second = client.post(
        f"/chat-sessions/{unknown_chat['id']}/messages", json={"content": "plan things"}
    ).json()["message"]
    fallback = client.post(
        f"/chat-sessions/{unknown_chat['id']}/messages/{second['id']}/task",
        json={"create_work_item": True, "space": "mars"},
    ).json()

    assert client.get(f"/work-items/{task['work_item_id']}").json()["space"] == "shopping"
    assert client.get(f"/work-items/{fallback['work_item_id']}").json()["space"] == "general"
