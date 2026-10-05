"""Context from other Android apps: shared files, and the screen the user was on."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.chat.triage import Triager
from app.execution.conversation import ConversationExecutor
from app.repositories import RepositoryRegistry
from tests.test_purchases import EXPORT
from tests.test_triage import ScriptedChat, suggestion

SCREEN = {"kind": "screen", "app": "Willys", "text": "Zoégas Skånerost 450 g\n54,90 kr"}


def _chat(client: TestClient) -> str:
    return client.post("/chat-sessions", json={}).json()["id"]


def test_a_shared_klarna_export_is_recognized_and_imported(client: TestClient) -> None:
    chat = _chat(client)

    shared = client.post(
        f"/chat-sessions/{chat}/files", json={"filename": "klarna.csv", "content": EXPORT}
    ).json()

    assert shared["target"] == "klarna-purchases"
    assert shared["reply"].startswith("Imported your Klarna export: 5 new purchases")
    contents = [
        m["content"] for m in client.get(f"/chat-sessions/{chat}/messages").json()["messages"]
    ]
    assert contents == ["📎 klarna.csv", shared["reply"]]


def test_an_unknown_shared_file_is_answered_not_guessed(client: TestClient) -> None:
    chat = _chat(client)

    shared = client.post(
        f"/chat-sessions/{chat}/files", json={"filename": "notes.txt", "content": "buy milk"}
    ).json()

    assert shared["target"] is None and shared["reply"].startswith("I can't use this kind")
    assert (
        client.post(
            "/chat-sessions/missing/files", json={"filename": "a", "content": "b"}
        ).status_code
        == 404
    )


def test_the_screen_goes_with_the_message_to_routing_and_the_answering_agent(
    client: TestClient,
) -> None:
    model = ScriptedChat(suggestion(intent="answer"))
    client.app.state.triager = Triager(RepositoryRegistry.from_directory("repositories"), model)
    chat = _chat(client)

    posted = client.post(
        f"/chat-sessions/{chat}/messages",
        json={"content": "is this a good price?", "attachments": [SCREEN]},
    ).json()

    routed = json.loads(model.requests[0][1].content)
    assert routed["screen_context"].startswith("Zoégas Skånerost")
    (block,) = posted["message"]["blocks"]
    assert block["type"] == "attachment" and block["app"] == "Willys"
    task = client.post(f"/chat-sessions/{chat}/messages/{posted['message']['id']}/task", json={})
    context = client.app.state.task_service.get_task(task.json()["id"]).source_context
    assert context["attachments"][0]["text"] == SCREEN["text"]
    prompt = ConversationExecutor(ScriptedChat())._messages("is this a good price?", [], context)
    shown = "\n".join(message.content for message in prompt)
    assert "untrusted data from another app" in shown and "54,90 kr" in shown


def test_the_supervisor_never_gets_screen_content(client: TestClient) -> None:
    chat = _chat(client)
    service = client.app.state.task_service
    posted = service.append_chat_message(
        chat, content="fix this", blocks=[{"type": "attachment", **SCREEN}]
    )

    task = service.create_task_from_message(
        chat, posted.message.id, required_capabilities=["supervision"]
    )

    assert "attachments" not in (task.source_context or {})


def test_attachments_are_limited(client: TestClient) -> None:
    chat = _chat(client)

    too_long = {**SCREEN, "text": "x" * 8_001}
    for attachments in ([too_long], [SCREEN, SCREEN]):
        response = client.post(
            f"/chat-sessions/{chat}/messages",
            json={"content": "look", "attachments": attachments},
        )
        assert response.status_code == 422
