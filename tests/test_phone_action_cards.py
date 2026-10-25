"""Phone actions the chat agent proposes become cards the phone app confirms and runs."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _reply_with(client: TestClient, action: dict) -> tuple[str, dict]:
    service = client.app.state.task_service
    chat = client.post("/chat-sessions", json={}).json()["id"]
    posted = service.append_chat_message(chat, content="set a timer for 10 minutes")
    service.create_task_from_message(chat, posted.message.id)
    task = service.claim_next_task(worker_id="w", lease_seconds=60)
    execution = service.start_execution(
        task.id, capability_id="conversation", executor_id="model-conversation", execution_input={}
    )
    service.start_validation(task.id, execution, output={"summary": "timer"})
    service.complete_task(task.id, execution, reply="A 10-minute timer: tap Do it.", action=action)
    reply = client.get(f"/chat-sessions/{chat}/messages").json()["messages"][-1]
    return chat, reply


def test_a_proposed_timer_is_a_card_the_phone_fetches_and_settles(client: TestClient) -> None:
    chat, reply = _reply_with(client, {"type": "set_timer", "seconds": 600, "label": "pasta"})

    (card,) = [b for b in reply["blocks"] if b["type"] == "phone_action"]
    assert card["state"] == "open"
    assert card["action"] == {"type": "set_timer", "seconds": 600, "label": "pasta"}
    url = f"/chat-sessions/{chat}/messages/{reply['id']}/phone-actions/{card['id']}"
    # The phone app reads the action from the server, not from the page.
    assert client.get(url).json() == {"id": card["id"], "action": card["action"]}

    settled = client.post(url, json={"state": "done", "result": "Timer set"})

    assert settled.status_code == 200
    (card,) = client.get(f"/chat-sessions/{chat}/messages").json()["messages"][-1]["blocks"]
    assert (card["state"], card["result"]) == ("done", "Timer set")
    assert client.get(url).status_code == 409  # never offered twice
    assert client.post(url, json={"state": "cancelled"}).status_code == 409


def test_an_invalid_action_is_never_offered(client: TestClient) -> None:
    _, reply = _reply_with(client, {"type": "set_timer", "seconds": 0})

    assert [b for b in reply["blocks"] if b["type"] == "phone_action"] == []
    assert reply["content"] == "A 10-minute timer: tap Do it."


def test_only_this_chats_assistant_cards_can_be_fetched(client: TestClient) -> None:
    chat, reply = _reply_with(client, {"type": "set_timer", "seconds": 60})
    (card,) = reply["blocks"]
    other = client.post("/chat-sessions", json={}).json()["id"]

    assert (
        client.get(
            f"/chat-sessions/{other}/messages/{reply['id']}/phone-actions/{card['id']}"
        ).status_code
        == 404
    )
    assert (
        client.get(f"/chat-sessions/{chat}/messages/{reply['id']}/phone-actions/nope").status_code
        == 404
    )
