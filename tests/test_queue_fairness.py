from __future__ import annotations

from app.services import TaskService


def _coding_task(service: TaskService, index: int):
    return service.create_task(
        request=f"Coding task {index}",
        required_capabilities=["coding", "pull_request_creation"],
        source_context={"repository_id": "personal-assistant"},
    )


def test_reply_lane_scans_past_more_than_one_hundred_background_tasks(
    service: TaskService,
) -> None:
    for index in range(101):
        service.create_task(request=f"Background {index}")
    chat = service.create_chat_session()
    reply = service.create_task(request="Reply now", chat_session_id=chat.id)

    claimed = service.claim_next_task(worker_id="replies", lease_seconds=30, lane="replies")

    assert claimed is not None
    assert claimed.id == reply.id


def test_background_lane_scans_past_more_than_one_hundred_replies(
    service: TaskService,
) -> None:
    chat = service.create_chat_session()
    for index in range(101):
        service.create_task(request=f"Reply {index}", chat_session_id=chat.id)
    background = service.create_task(request="Background")

    claimed = service.claim_next_task(worker_id="background", lease_seconds=30, lane="background")

    assert claimed is not None
    assert claimed.id == background.id


def test_coding_worker_scans_past_more_than_one_hundred_regular_tasks(
    service: TaskService,
) -> None:
    for index in range(101):
        service.create_task(request=f"Regular {index}")
    coding = _coding_task(service, 1)

    claimed = service.claim_next_task(worker_id="coding", lease_seconds=30, coding_only=True)

    assert claimed is not None
    assert claimed.id == coding.id


def test_general_worker_scans_past_more_than_one_hundred_coding_tasks(
    service: TaskService,
) -> None:
    for index in range(101):
        _coding_task(service, index)
    regular = service.create_task(request="Regular")

    claimed = service.claim_next_task(worker_id="general", lease_seconds=30, supports_coding=False)

    assert claimed is not None
    assert claimed.id == regular.id
