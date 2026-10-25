"""A reply's emotion is kept on its chat message, for the avatar."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_a_replys_emotion_reaches_the_chat_message(client: TestClient) -> None:
    service = client.app.state.task_service
    chat = client.post("/chat-sessions", json={}).json()["id"]
    posted = service.append_chat_message(chat, content="my flight was cancelled")
    service.create_task_from_message(chat, posted.message.id)
    task = service.claim_next_task(worker_id="w", lease_seconds=60)
    execution = service.start_execution(
        task.id, capability_id="conversation", executor_id="model-conversation", execution_input={}
    )
    service.start_validation(task.id, execution, output={"summary": "sympathy"})
    service.complete_task(task.id, execution, reply="That's rotten, sorry.", emotion="Concerned")

    user, reply = client.get(f"/chat-sessions/{chat}/messages").json()["messages"][-2:]

    assert (reply["role"], reply["emotion"]) == ("assistant", "Concerned")
    assert user["emotion"] is None
