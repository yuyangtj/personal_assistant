from __future__ import annotations

import threading
import time

from app.execution.fake import FakeExecutor
from app.services import TaskService
from app.worker import PeriodicJob, TaskWorker


def test_a_slow_job_runs_on_its_own_thread_and_never_holds_up_the_claim_loop(
    service: TaskService,
) -> None:
    started = threading.Event()

    def slow() -> int:
        started.set()
        time.sleep(2)
        return 0

    worker = TaskWorker(
        service=service,
        executors={"fake": FakeExecutor(delay_seconds=0)},
        worker_id="test",
        jobs=[PeriodicJob("slow", 0.01, slow, run_at_start=True)],
    )
    stop = threading.Event()
    worker.jobs[0].start(stop)
    assert started.wait(timeout=1)

    began = time.monotonic()
    assert worker.run_once() is False
    assert time.monotonic() - began < 0.5
    stop.set()


def test_a_failing_job_is_logged_and_keeps_running(caplog) -> None:
    calls: list[int] = []

    def flaky() -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("model down")
        return len(calls)

    job = PeriodicJob("flaky", 0.01, flaky, "Did %s things")
    with caplog.at_level("INFO"):
        assert job.run_once() is None
        assert job.run_once() == 2

    assert "Background job flaky failed" in caplog.text
    assert "Did 2 things" in caplog.text


def test_a_job_stops_with_the_worker() -> None:
    ran = threading.Event()
    job = PeriodicJob("tick", 0.01, lambda: ran.set())
    stop = threading.Event()
    thread = job.start(stop)
    assert ran.wait(timeout=1)

    stop.set()
    thread.join(timeout=1)
    assert not thread.is_alive()
