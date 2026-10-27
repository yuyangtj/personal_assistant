from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.chat.blocks import model_blocks
from app.persistence.models import ChatMessageModel


def _chat(client: TestClient) -> str:
    return client.post("/chat-sessions", json={}).json()["id"]


def _seed(
    client: TestClient,
    chat_id: str,
    count: int,
    *,
    equal_timestamps: bool = False,
    id_prefix: str = "",
) -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    with client.app.state.database.session() as session, session.begin():
        session.add_all(
            ChatMessageModel(
                id=f"{id_prefix}message-{number:04d}",
                chat_session_id=chat_id,
                role="user",
                content=f"message-{number}",
                created_at=started
                if equal_timestamps
                else started + timedelta(microseconds=number),
            )
            for number in range(1, count + 1)
        )


def _contents(page: dict) -> list[str]:
    return [message["content"] for message in page["messages"]]


def test_default_page_is_the_latest_500_in_chronological_order(client: TestClient) -> None:
    chat_id = _chat(client)
    _seed(client, chat_id, 501)

    response = client.get(f"/chat-sessions/{chat_id}/messages")

    assert response.status_code == 200
    page = response.json()
    assert _contents(page) == [f"message-{number}" for number in range(2, 502)]
    assert page["next_before"]
    older = client.get(
        f"/chat-sessions/{chat_id}/messages", params={"before": page["next_before"]}
    ).json()
    assert _contents(older) == ["message-1"]
    assert older["next_before"] is None


def test_cursor_uses_message_id_to_break_created_at_ties(client: TestClient) -> None:
    chat_id = _chat(client)
    _seed(client, chat_id, 5, equal_timestamps=True)

    latest = client.get(f"/chat-sessions/{chat_id}/messages", params={"limit": 2}).json()

    assert [message["id"] for message in latest["messages"]] == ["message-0004", "message-0005"]
    older = client.get(
        f"/chat-sessions/{chat_id}/messages",
        params={"limit": 2, "before": latest["next_before"]},
    ).json()
    assert [message["id"] for message in older["messages"]] == ["message-0002", "message-0003"]
    oldest = client.get(
        f"/chat-sessions/{chat_id}/messages",
        params={"limit": 2, "before": older["next_before"]},
    ).json()
    assert [message["id"] for message in oldest["messages"]] == ["message-0001"]
    assert oldest["next_before"] is None


def test_cursor_traversal_covers_every_message_exactly_once(client: TestClient) -> None:
    chat_id = _chat(client)
    _seed(client, chat_id, 1_205)

    pages = []
    before = None
    while True:
        response = client.get(
            f"/chat-sessions/{chat_id}/messages",
            params={"limit": 500, **({"before": before} if before else {})},
        )
        assert response.status_code == 200
        page = response.json()
        pages.append(_contents(page))
        before = page["next_before"]
        if before is None:
            break

    assert [len(page) for page in pages] == [500, 500, 205]
    assert [message for page in reversed(pages) for message in page] == [
        f"message-{number}" for number in range(1, 1_206)
    ]


def test_invalid_and_cross_chat_cursors_are_not_found(client: TestClient) -> None:
    chat_id = _chat(client)
    other_chat_id = _chat(client)
    _seed(client, chat_id, 2)
    _seed(client, other_chat_id, 2, id_prefix="other-")
    other_cursor = client.get(
        f"/chat-sessions/{other_chat_id}/messages", params={"limit": 1}
    ).json()["next_before"]

    assert (
        client.get(
            f"/chat-sessions/{chat_id}/messages", params={"before": "not-a-cursor"}
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/chat-sessions/{chat_id}/messages", params={"before": other_cursor}
        ).status_code
        == 404
    )


def test_recent_pending_choices_still_work_with_more_than_500_messages(
    client: TestClient,
) -> None:
    chat_id = _chat(client)
    _seed(client, chat_id, 500)
    offer = client.app.state.task_service.append_chat_message(
        chat_id,
        content="By week or by merchant?",
        role="assistant",
        blocks=model_blocks({"choices": ["By week"]}),
    ).message

    response = client.post(f"/chat-sessions/{chat_id}/messages", json={"content": "By week"})

    assert response.status_code == 201
    messages = client.get(f"/chat-sessions/{chat_id}/messages").json()["messages"]
    saved_offer = next(message for message in messages if message["id"] == offer.id)
    assert saved_offer["blocks"][0]["state"] == "chosen"
    assert saved_offer["blocks"][0]["chosen"] == "By week"
