from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.persistence.database import Database
from app.worker_liveness import (
    WorkerHeartbeat,
    container_instance_id,
    expected_worker_ids,
    unhealthy_workers,
)


def test_instance_id_is_stable_within_a_container(tmp_path) -> None:
    path = tmp_path / "instance"

    first = container_instance_id(path)

    assert container_instance_id(path) == first


def test_expected_workers_cover_reply_and_coding_lanes() -> None:
    assert expected_worker_ids(Settings(worker_id="general")) == ["general", "general-replies"]
    assert expected_worker_ids(
        Settings(worker_id="coding", worker_coding_only=True, coding_concurrency=3)
    ) == ["coding"]


def test_heartbeat_requires_fresh_current_instance_and_running_worker(
    database: Database,
) -> None:
    now = datetime.now(UTC)
    settings = Settings(worker_id="worker", worker_reply_lane=False)
    heartbeat = WorkerHeartbeat(
        database,
        worker_id="worker",
        instance_id="instance-a",
        lane="all",
        clock=lambda: now,
    )
    heartbeat.beat()

    assert unhealthy_workers(database, settings, instance_id="instance-a", now=now) == []
    assert unhealthy_workers(database, settings, instance_id="instance-b", now=now) == ["worker"]
    assert unhealthy_workers(
        database,
        settings,
        instance_id="instance-a",
        now=now + timedelta(seconds=31),
    ) == ["worker"]


def test_stopped_worker_is_not_healthy(database: Database) -> None:
    settings = Settings(worker_id="worker", worker_reply_lane=False)
    heartbeat = WorkerHeartbeat(
        database,
        worker_id="worker",
        instance_id="instance-a",
        lane="all",
    )
    heartbeat.start()
    heartbeat.stop()

    assert unhealthy_workers(database, settings, instance_id="instance-a") == ["worker"]


def test_extra_coding_loops_count_only_once_they_started(database: Database) -> None:
    now = datetime.now(UTC)
    settings = Settings(worker_id="coding", worker_coding_only=True, coding_concurrency=3)

    def beat(worker_id: str, at: datetime) -> None:
        WorkerHeartbeat(
            database, worker_id=worker_id, instance_id="instance-a", lane="coding", clock=lambda: at
        ).beat()

    # No coding agent could be built: only the main loop runs, and that is healthy.
    beat("coding", now)
    assert unhealthy_workers(database, settings, instance_id="instance-a", now=now) == []

    # An extra loop that started and then went quiet is not.
    beat("coding-2", now - timedelta(minutes=5))
    assert unhealthy_workers(database, settings, instance_id="instance-a", now=now) == ["coding-2"]
