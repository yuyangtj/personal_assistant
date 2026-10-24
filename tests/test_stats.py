from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.persistence.database import Database
from app.persistence.models import ChatMessageModel, ChatSessionModel, ExecutionModel, TaskModel
from app.work.stats import compute_stats, kind_of, percentile

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def at(hour: int, minute: int, second: int = 0) -> datetime:
    return NOW.replace(hour=hour, minute=minute, second=second)


def add_task(
    database: Database,
    name: str,
    *,
    status: str,
    created: datetime,
    capabilities: list[str] | None = None,
    asked: datetime | None = None,
    started: datetime | None = None,
    finished: datetime | None = None,
) -> None:
    with database.session() as session, session.begin():
        origin = None
        if asked is not None:
            if session.get(ChatSessionModel, "chat") is None:
                session.add(ChatSessionModel(id="chat", title="t"))
                session.flush()
            origin = ChatMessageModel(
                id=f"message-{name}",
                chat_session_id="chat",
                role="user",
                content="hi",
                created_at=asked,
            )
            session.add(origin)
            session.flush()
        session.add(
            TaskModel(
                id=name,
                original_request="r",
                current_goal="g",
                status=status,
                required_capabilities=capabilities or [],
                source_context={},
                origin_message_id=origin.id if origin else None,
                created_at=created,
                updated_at=created,
            )
        )
        session.flush()
        if started is not None:
            session.add(
                ExecutionModel(
                    id=f"run-{name}",
                    task_id=name,
                    executor_id="x",
                    status="succeeded",
                    created_at=started,
                    started_at=started,
                    completed_at=finished,
                )
            )


def test_percentiles_interpolate() -> None:
    assert percentile([], 0.5) is None
    assert percentile([4.0], 0.9) == 4.0
    assert percentile([3.0, 12.0, 8.0], 0.5) == 8.0
    assert percentile([3.0, 8.0, 12.0], 0.9) == pytest.approx(11.2)


def test_the_kind_of_work_comes_from_the_capabilities() -> None:
    assert kind_of([]) == "chat reply"
    assert kind_of(["conversation"]) == "chat reply"
    assert kind_of(["coding", "pull_request_creation"]) == "coding"
    assert kind_of(["supervision"]) == "supervisor"
    assert kind_of(["groceries"]) == "groceries"
    assert kind_of(["web_research"]) == "research"
    assert kind_of(["something_new"]) == "other"


def test_reply_times_waits_and_failures_by_kind(database: Database) -> None:
    add_task(
        database,
        "a",
        status="completed",
        created=at(11, 0),
        asked=at(10, 59, 58),
        started=at(11, 0, 1),
        finished=at(11, 0, 6),
    )
    add_task(
        database,
        "b",
        status="completed",
        created=at(11, 10),
        started=at(11, 10, 2),
        finished=at(11, 10, 12),
    )
    add_task(
        database,
        "c",
        status="failed",
        created=at(11, 20),
        started=at(11, 20, 1),
        finished=at(11, 20, 3),
    )
    add_task(
        database,
        "d",
        status="completed",
        created=at(10, 0),
        capabilities=["coding"],
        started=at(10, 0, 5),
        finished=at(10, 20, 5),
    )
    add_task(database, "e", status="created", created=at(11, 59))  # still queued
    add_task(
        database,
        "f",
        status="completed",
        created=NOW - timedelta(days=20),
        started=NOW - timedelta(days=20),
        finished=NOW - timedelta(days=20),
    )  # too old

    stats = compute_stats(database, days=7, now=NOW)

    by_kind = {entry["kind"]: entry for entry in stats["kinds"]}
    assert list(by_kind) == ["chat reply", "coding"]  # kinds with no work are left out
    chat = by_kind["chat reply"]
    assert (chat["tasks"], chat["completed"], chat["failed"], chat["unfinished"]) == (4, 2, 1, 1)
    assert chat["queue_wait_median_s"] == 1.0  # waits 1, 2 and 1 seconds
    assert chat["reply_median_s"] == 8.0  # 8 s from the message, 12 s, 3 s
    assert chat["reply_p90_s"] == 11.2
    assert by_kind["coding"]["reply_median_s"] == 1205.0
    assert stats["counts"]["messages"] == 1
    assert stats["system"]["disk_total_gb"] > 0


def test_a_wider_window_includes_older_work(database: Database) -> None:
    add_task(
        database,
        "old",
        status="completed",
        created=NOW - timedelta(days=20),
        started=NOW - timedelta(days=20),
        finished=NOW - timedelta(days=20, seconds=-4),
    )

    assert compute_stats(database, days=7, now=NOW)["kinds"] == []
    wide = compute_stats(database, days=30, now=NOW)["kinds"]
    assert [(entry["kind"], entry["reply_median_s"]) for entry in wide] == [("chat reply", 4.0)]


def test_the_stats_endpoint(client: TestClient) -> None:
    client.post("/notes", json={"title": "A note"})

    response = client.get("/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["days"] == 7 and body["kinds"] == []
    assert body["counts"]["notes"] == 1
    assert set(body["system"]) == {
        "disk_total_gb",
        "disk_free_gb",
        "disk_used_percent",
        "database_mb",
    }
    assert client.get("/stats?days=30").json()["days"] == 30
    assert client.get("/stats?days=0").status_code == 422
    assert client.get("/stats?days=500").status_code == 422
