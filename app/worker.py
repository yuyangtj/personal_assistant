from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Mapping

from app.capabilities import CapabilityRegistry
from app.config import Settings
from app.execution import Executor, FakeExecutor
from app.execution.fake import ExecutionCancelled
from app.manager import DeterministicManager, TaskManager
from app.manager.decisions import DelegateDecision, FailDecision
from app.persistence.database import Database
from app.service import TaskService

logger = logging.getLogger(__name__)


class ExecutorAdapterNotFoundError(LookupError):
    pass


class TaskWorker:
    def __init__(
        self,
        *,
        service: TaskService,
        manager: TaskManager,
        executors: Mapping[str, Executor],
        worker_id: str,
        lease_seconds: int = 60,
        poll_interval_seconds: float = 1.0,
    ):
        self.service = service
        self.manager = manager
        self.executors = dict(executors)
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()

    def run_once(self) -> bool:
        task = self.service.claim_next_task(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if task is None:
            return False

        execution_id: str | None = None
        try:
            outcome = self.manager.decide(task)
            decision = outcome.decision
            self.service.record_plan(
                task.id,
                [
                    "Resolve the task's required capabilities",
                    "Select an enabled registered capability",
                    "Execute through its configured adapter",
                    "Validate that a structured result was produced",
                ],
            )
            if outcome.analysis is not None:
                self.service.record_task_analysis(
                    task.id,
                    outcome.analysis.event_payload(),
                )
            if outcome.analysis_failure is not None:
                self.service.record_task_analysis_failure(
                    task.id,
                    outcome.analysis_failure.event_payload(),
                )
            self.service.record_manager_decision(
                task.id,
                decision.model_dump(mode="json"),
            )
            if isinstance(decision, FailDecision):
                self.service.fail_task(
                    task.id,
                    error=decision.reason,
                    reply="Sorry, I can't do that yet. I don't have a tool for it.",
                )
                return True
            if not isinstance(decision, DelegateDecision):
                raise TypeError(f"Unsupported manager decision: {decision.action}")
            executor = self.executors.get(decision.adapter)
            if executor is None:
                raise ExecutorAdapterNotFoundError(
                    f"No executor is installed for adapter: {decision.adapter}"
                )
            execution_id = self.service.start_execution(
                task.id,
                capability_id=decision.capability_id,
                executor_id=executor.id,
                execution_input={"request": task.original_request},
            )
            if execution_id is None:
                return True

            result = executor.execute(
                task_id=task.id,
                request=task.original_request,
                is_cancelled=lambda: self.service.is_cancelled(task.id),
            )
            if not self.service.start_validation(
                task.id,
                execution_id,
                output=result.output,
            ):
                return True
            if not result.output or not result.output.get("summary"):
                raise ValueError("Executor produced no summary")
            reply = result.output.get("reply") or result.output["summary"]
            self.service.complete_task(task.id, execution_id, reply=str(reply))
        except ExecutionCancelled:
            if execution_id is not None:
                self.service.finish_cancelled_execution(execution_id)
        except Exception as error:
            logger.exception("Task %s failed", task.id)
            self.service.fail_task(
                task.id,
                error=str(error),
                execution_id=execution_id,
            )
        return True

    def run_forever(self) -> None:
        logger.info("Worker %s started", self.worker_id)
        while not self._stop_event.is_set():
            processed = self.run_once()
            if not processed:
                self._stop_event.wait(self.poll_interval_seconds)
        logger.info("Worker %s stopped", self.worker_id)

    def stop(self) -> None:
        self._stop_event.set()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
    database = Database(settings.database_url)
    if settings.auto_create_schema:
        database.create_schema()
    registry = CapabilityRegistry.from_directory(settings.capabilities_directory)
    worker = TaskWorker(
        service=TaskService(database),
        manager=DeterministicManager(registry),
        executors={
            "fake": FakeExecutor(delay_seconds=settings.fake_executor_delay_seconds),
        },
        worker_id=settings.worker_id,
        lease_seconds=settings.worker_lease_seconds,
        poll_interval_seconds=settings.worker_poll_interval_seconds,
    )

    def stop_worker(_signum, _frame) -> None:
        worker.stop()

    signal.signal(signal.SIGINT, stop_worker)
    signal.signal(signal.SIGTERM, stop_worker)
    try:
        worker.run_forever()
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
