from __future__ import annotations

import threading
import time
from collections.abc import Mapping

from app.capabilities import CapabilityRegistry
from app.domain.enums import TaskStatus
from app.execution.fake import FakeExecutor
from app.manager import DeterministicManager, ModelAssistedManager, TaskManager
from app.manager.model import ScriptedManagerModelClient, ValidatedManagerModelAdapter
from app.persistence.database import Database
from app.service import TaskService
from app.worker import TaskWorker


def make_worker(
    service: TaskService,
    *,
    delay: float = 0.0,
    manager: TaskManager | None = None,
    executors: Mapping[str, FakeExecutor] | None = None,
) -> TaskWorker:
    installed = executors or {"fake": FakeExecutor(delay_seconds=delay)}
    registry = CapabilityRegistry.from_directory("capabilities").restricted_to_adapters(installed)
    return TaskWorker(
        service=service,
        manager=manager or DeterministicManager(registry),
        executors=installed,
        worker_id="test-worker",
        lease_seconds=30,
        poll_interval_seconds=0.01,
    )


def test_worker_completes_task_and_records_ordered_events(service: TaskService) -> None:
    task = service.create_task(request="Research PostgreSQL hosting")

    assert make_worker(service).run_once() is True

    completed = service.get_task(task.id)
    assert completed.status == TaskStatus.COMPLETED.value
    events = service.list_events(task.id)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert [event.event_type for event in events] == [
        "TASK_CREATED",
        "TASK_PLANNING_STARTED",
        "PLAN_CREATED",
        "MANAGER_DECISION_CREATED",
        "CAPABILITY_SELECTED",
        "EXECUTION_STARTED",
        "EXECUTION_OUTPUT_RECEIVED",
        "VALIDATION_STARTED",
        "VALIDATION_SUCCEEDED",
        "ASSISTANT_REPLY",
        "TASK_COMPLETED",
    ]
    reply = events[-2].payload
    assert reply == {
        "text": "All done. I handled your request: Research PostgreSQL hosting",
        "emotion": "Warm",
        "intensity": 0.7,
        "outcome": "completed",
    }


def test_queued_task_survives_database_reconstruction(database_url: str) -> None:
    first_database = Database(database_url)
    first_database.create_schema()
    first_service = TaskService(first_database)
    task = first_service.create_task(request="Persist me")
    first_database.dispose()

    second_database = Database(database_url)
    second_service = TaskService(second_database)
    try:
        assert make_worker(second_service).run_once() is True
        assert second_service.get_task(task.id).status == TaskStatus.COMPLETED.value
    finally:
        second_database.dispose()


def test_running_task_can_be_cancelled(service: TaskService) -> None:
    task = service.create_task(request="Long-running fake task")
    worker = make_worker(service, delay=0.5)
    thread = threading.Thread(target=worker.run_once)
    thread.start()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if service.get_task(task.id).status == TaskStatus.EXECUTING.value:
            break
        time.sleep(0.01)
    else:
        raise AssertionError("Task did not begin executing")

    service.cancel_task(task.id)
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert service.get_task(task.id).status == TaskStatus.CANCELLED.value
    events = service.list_events(task.id)
    assert events[-1].event_type == "TASK_CANCELLED"
    assert events[-2].event_type == "ASSISTANT_REPLY"
    assert events[-2].payload["outcome"] == "cancelled"


def test_worker_returns_false_when_queue_is_empty(service: TaskService) -> None:
    assert make_worker(service).run_once() is False


def test_task_fails_cleanly_when_no_enabled_capability_matches(
    service: TaskService,
) -> None:
    task = service.create_task(
        request="Inspect a repository",
        required_capabilities=["repository_analysis"],
    )

    assert make_worker(service).run_once() is True

    failed = service.get_task(task.id)
    assert failed.status == TaskStatus.FAILED.value
    assert service.list_events(task.id)[-1].event_type == "TASK_FAILED"
    assert "No enabled capability provides" in service.list_events(task.id)[-1].payload["error"]
    reply = service.list_events(task.id)[-2]
    assert reply.event_type == "ASSISTANT_REPLY"
    assert reply.payload["outcome"] == "failed"
    assert reply.payload["emotion"] == "Concerned"
    assert "capability" not in reply.payload["text"].lower()


def test_executor_exception_is_spoken_without_internal_details(service: TaskService) -> None:
    class ExplodingExecutor(FakeExecutor):
        def execute(self, **_kwargs):
            raise RuntimeError("database password leaked in stack trace")

    task = service.create_task(request="Break please")

    assert make_worker(service, executors={"fake": ExplodingExecutor()}).run_once() is True

    events = service.list_events(task.id)
    assert events[-1].event_type == "TASK_FAILED"
    assert events[-2].payload == {
        "text": "Sorry, I couldn't finish that request.",
        "emotion": "Concerned",
        "intensity": 0.6,
        "outcome": "failed",
    }


def test_worker_records_model_analysis_before_routing(service: TaskService) -> None:
    registry = CapabilityRegistry.from_directory("capabilities").restricted_to_adapters({"fake"})
    client = ScriptedManagerModelClient(
        [
            {
                "goal": "Exercise the task lifecycle",
                "required_capabilities": ["task_execution"],
                "constraints": [],
                "risk_signals": [],
                "confidence": 0.95,
            }
        ]
    )
    manager = ModelAssistedManager(
        registry,
        ValidatedManagerModelAdapter(client),
    )
    task = service.create_task(request="Please handle this task")

    assert make_worker(service, manager=manager).run_once() is True

    completed = service.get_task(task.id)
    assert completed.status == TaskStatus.COMPLETED.value
    assert completed.current_goal == "Exercise the task lifecycle"
    assert completed.required_capabilities == ["task_execution"]
    event_types = [event.event_type for event in service.list_events(task.id)]
    assert "TASK_ANALYZED" in event_types
    assert event_types.index("TASK_ANALYZED") < event_types.index("MANAGER_DECISION_CREATED")


def test_disabled_inferred_capability_is_recorded_as_gap(service: TaskService) -> None:
    registry = CapabilityRegistry.from_directory("capabilities").restricted_to_adapters({"fake"})
    client = ScriptedManagerModelClient(
        [
            {
                "goal": "Inspect the repository",
                "required_capabilities": ["repository_analysis"],
                "constraints": [],
                "risk_signals": [],
                "confidence": 0.9,
            }
        ]
    )
    manager = ModelAssistedManager(
        registry,
        ValidatedManagerModelAdapter(client),
    )
    task = service.create_task(request="Inspect my repository")

    assert make_worker(service, manager=manager).run_once() is True

    assert service.get_task(task.id).status == TaskStatus.FAILED.value
    events = service.list_events(task.id)
    decision_event = next(
        event for event in events if event.event_type == "MANAGER_DECISION_CREATED"
    )
    assert decision_event.payload["action"] == "fail"
    assert decision_event.payload["code"] == "capability_gap"
    assert "TASK_ANALYZED" in [event.event_type for event in events]


def test_worker_records_final_analysis_failure_without_raw_output(
    service: TaskService,
) -> None:
    registry = CapabilityRegistry.from_directory("capabilities").restricted_to_adapters({"fake"})
    client = ScriptedManagerModelClient(["secret malformed output", "still malformed"])
    manager = ModelAssistedManager(
        registry,
        ValidatedManagerModelAdapter(client, max_attempts=2),
    )
    task = service.create_task(request="Analyze this")

    assert make_worker(service, manager=manager).run_once() is True

    assert service.get_task(task.id).status == TaskStatus.FAILED.value
    events = service.list_events(task.id)
    failure = next(event for event in events if event.event_type == "TASK_ANALYSIS_FAILED")
    assert failure.payload["attempts"] == 2
    assert failure.payload["reason"] == "response was not valid JSON"
    assert "secret malformed output" not in str(failure.payload)
