from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.config import Settings
from app.persistence.database import Database
from app.persistence.models import WorkerHeartbeatModel, utc_now

logger = logging.getLogger(__name__)
INSTANCE_FILE = Path("/tmp/personal-assistant-worker-instance")
STALE_AFTER = timedelta(seconds=30)
_INSTANCE_LOCK = threading.Lock()
_INSTANCE_IDS: dict[Path, str] = {}


def container_instance_id(path: Path = INSTANCE_FILE) -> str:
    with _INSTANCE_LOCK:
        if path in _INSTANCE_IDS:
            return _INSTANCE_IDS[path]
        try:
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                _INSTANCE_IDS[path] = existing
                return existing
        except FileNotFoundError:
            pass
        value = str(uuid4())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{value}\n", encoding="utf-8")
        _INSTANCE_IDS[path] = value
        return value


def expected_worker_ids(settings: Settings) -> list[str]:
    workers = [settings.worker_id]
    if settings.worker_coding_only:
        workers.extend(
            f"{settings.worker_id}-{index}" for index in range(2, settings.coding_concurrency + 1)
        )
    elif settings.worker_reply_lane:
        workers.append(f"{settings.worker_id}-replies")
    return workers


class WorkerHeartbeat:
    def __init__(
        self,
        database: Database,
        *,
        worker_id: str,
        instance_id: str,
        lane: str,
        interval_seconds: float = 5.0,
        clock: Callable[[], datetime] = utc_now,
    ):
        self.database = database
        self.worker_id = worker_id
        self.instance_id = instance_id
        self.lane = lane
        self.interval_seconds = interval_seconds
        self.clock = clock
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"health-{worker_id}", daemon=True)

    def start(self) -> None:
        self.beat()
        self._thread.start()

    def beat(self) -> None:
        now = self.clock()
        with self.database.session() as session, session.begin():
            heartbeat = session.get(WorkerHeartbeatModel, self.worker_id, with_for_update=True)
            if heartbeat is None:
                heartbeat = WorkerHeartbeatModel(
                    worker_id=self.worker_id,
                    instance_id=self.instance_id,
                    lane=self.lane,
                    started_at=now,
                    heartbeat_at=now,
                )
                session.add(heartbeat)
            else:
                if heartbeat.instance_id != self.instance_id:
                    heartbeat.started_at = now
                heartbeat.instance_id = self.instance_id
                heartbeat.lane = self.lane
                heartbeat.heartbeat_at = now
                heartbeat.stopped_at = None

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        with self.database.session() as session, session.begin():
            heartbeat = session.get(WorkerHeartbeatModel, self.worker_id)
            if heartbeat is not None and heartbeat.instance_id == self.instance_id:
                heartbeat.stopped_at = self.clock()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.beat()
            except Exception:
                logger.exception("Could not refresh worker heartbeat %s", self.worker_id)


def unhealthy_workers(
    database: Database,
    settings: Settings,
    *,
    instance_id: str,
    now: datetime | None = None,
) -> list[str]:
    now = now or datetime.now(UTC)
    with database.session() as session:
        heartbeats = {
            row.worker_id: row
            for row in session.scalars(
                select(WorkerHeartbeatModel).where(
                    WorkerHeartbeatModel.worker_id.in_(expected_worker_ids(settings))
                )
            )
        }
    unhealthy = []
    for worker_id in expected_worker_ids(settings):
        heartbeat = heartbeats.get(worker_id)
        heartbeat_at = heartbeat.heartbeat_at if heartbeat is not None else None
        if heartbeat_at is not None and heartbeat_at.tzinfo is None:
            heartbeat_at = heartbeat_at.replace(tzinfo=UTC)
        if (
            heartbeat is None
            or heartbeat.instance_id != instance_id
            or heartbeat.stopped_at is not None
            or heartbeat_at is None
            or heartbeat_at < now - STALE_AFTER
        ):
            unhealthy.append(worker_id)
    return unhealthy


def main() -> None:
    settings = Settings.from_env()
    database = Database(settings.database_url)
    try:
        unhealthy = unhealthy_workers(
            database,
            settings,
            instance_id=container_instance_id(),
        )
    finally:
        database.dispose()
    if unhealthy:
        print("Unhealthy workers: " + ", ".join(unhealthy), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
