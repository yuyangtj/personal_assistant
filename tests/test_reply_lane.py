"""Chat replies have their own lane: background work can't keep someone waiting."""

from __future__ import annotations

import threading
import time

import pytest

from app.services import TaskService
from tests.test_worker import make_worker


def _chat(service: TaskService) -> str:
    return service.create_chat_session().id


def test_the_reply_lane_takes_only_replies_and_the_background_lane_the_rest(
    service: TaskService,
) -> None:
    chat = _chat(service)
    research = service.create_task(
        request="cheapest trains", required_capabilities=["web_research"], chat_session_id=chat
    )
    routine = service.create_task(
        request="summarize my week", source_context={"schedule_id": "s1"}, chat_session_id=chat
    )
    standalone = service.create_task(request="not from a chat")
    reply = service.create_task(request="thanks!", chat_session_id=chat)

    assert service.claim_next_task(worker_id="r", lease_seconds=30, lane="replies").id == reply.id
    assert service.claim_next_task(worker_id="r", lease_seconds=30, lane="replies") is None
    background = [
        service.claim_next_task(worker_id="b", lease_seconds=30, lane="background").id
        for _ in range(3)
    ]
    assert background == [research.id, routine.id, standalone.id]


def test_a_reply_finishes_while_slow_background_work_still_runs(service: TaskService) -> None:
    chat = _chat(service)
    slow = service.create_task(
        request="a long routine", source_context={"schedule_id": "s1"}, chat_session_id=chat
    )
    reply = service.create_task(request="thanks!", chat_session_id=chat)
    background = make_worker(service, delay=2.0)
    background.lane = "background"
    replies = make_worker(service)
    replies.lane = "replies"

    thread = threading.Thread(target=background.run_once)
    thread.start()
    time.sleep(0.3)  # the slow one is running
    started = time.monotonic()
    assert replies.run_once() is True
    took = time.monotonic() - started
    thread.join(10)

    assert took < 1.5, took  # not held up by the 2 s routine
    assert service.get_task(reply.id).status == "completed"
    assert service.get_task(slow.id).status == "completed"


def test_the_chat_worker_starts_both_lanes(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app import worker

    monkeypatch.setenv("ASSISTANT_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/w.sqlite3")
    monkeypatch.setenv("ASSISTANT_AUTO_CREATE_SCHEMA", "true")
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    lanes: list[str] = []
    lock = threading.Lock()

    def record(self) -> None:
        with lock:
            lanes.append(self.lane)

    monkeypatch.setattr(worker.TaskWorker, "run_forever", record)

    worker.main()
    time.sleep(0.2)  # the reply lane runs in its own thread

    assert sorted(lanes) == ["background", "replies"]
