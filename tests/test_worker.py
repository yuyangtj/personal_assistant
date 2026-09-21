from __future__ import annotations

import threading
import time
from collections.abc import Mapping

import pytest

from app.capabilities import CapabilityRegistry
from app.config import Settings
from app.domain.enums import TaskStatus
from app.execution.base import ExecutionResult
from app.execution.fake import FakeExecutor
from app.integrations.fallback import FallbackChatClient, FallbackManagerModelClient
from app.manager import DeterministicManager, ModelAssistedManager, TaskManager
from app.manager.model import ScriptedManagerModelClient, ValidatedManagerModelAdapter
from app.persistence.database import Database
from app.service import TaskService
from app.worker import (
    TaskWorker,
    build_coding_executor,
    build_conversation_executor,
    build_manager,
)


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


def test_runtime_conversation_uses_kimi_when_minimax_is_not_configured() -> None:
    executor = build_conversation_executor(Settings(kimi_api_key="sk-test"))

    assert executor is not None
    assert executor.id == "model-conversation"
    assert executor.client.provider == "kimi"
    assert executor.client.model == "kimi-for-coding-highspeed"
    executor.client.close()


def test_runtime_conversation_can_use_minimax() -> None:
    executor = build_conversation_executor(
        Settings(
            minimax_api_key="sk-test",
            conversation_model_provider="minimax",
            conversation_model_base_url="https://chat.example/v1",
            conversation_model_name="MiniMax-M3",
            conversation_model_timeout_seconds=19,
        )
    )

    assert executor is not None
    assert executor.id == "model-conversation"
    assert executor.client.provider == "minimax"
    assert executor.client.model == "MiniMax-M3"
    assert executor.client._client.timeout.connect == 19
    executor.client.close()


def test_runtime_conversation_builds_preferred_provider_chain() -> None:
    executor = build_conversation_executor(
        Settings(kimi_api_key="sk-kimi", minimax_api_key="sk-minimax")
    )

    assert executor is not None
    assert isinstance(executor.client, FallbackChatClient)
    assert [client.provider for client in executor.client.clients] == ["minimax", "kimi"]
    executor.client.close()


def test_runtime_conversation_uses_available_fallback_when_primary_has_no_key() -> None:
    executor = build_conversation_executor(
        Settings(kimi_api_key="sk-kimi", conversation_model_provider="minimax")
    )

    assert executor is not None
    assert executor.client.provider == "kimi"
    executor.client.close()


def test_runtime_conversation_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported model provider"):
        build_conversation_executor(Settings(conversation_model_provider="unknown"))


def test_runtime_manager_is_deterministic_by_default() -> None:
    registry = CapabilityRegistry.from_directory("capabilities").restricted_to_adapters({"fake"})

    manager = build_manager(Settings(), registry)

    assert isinstance(manager, DeterministicManager)


def test_runtime_manager_can_use_kimi_analysis() -> None:
    registry = CapabilityRegistry.from_directory("capabilities").restricted_to_adapters({"fake"})
    settings = Settings(
        kimi_api_key="sk-test",
        manager_model_enabled=True,
        manager_model_base_url="https://manager.example/v1",
        manager_model_name="kimi-manager-test",
        manager_model_timeout_seconds=12,
        manager_model_minimum_confidence=0.7,
    )

    manager = build_manager(settings, registry)

    assert isinstance(manager, ModelAssistedManager)
    assert manager.minimum_confidence == 0.7
    assert manager.analyzer.timeout_seconds == 12
    assert manager.analyzer.client.model == "kimi-manager-test"
    manager.analyzer.client.close()


def test_runtime_manager_can_use_minimax_analysis() -> None:
    registry = CapabilityRegistry.from_directory("capabilities").restricted_to_adapters({"fake"})
    settings = Settings(
        minimax_api_key="sk-test",
        manager_model_enabled=True,
        manager_model_provider="minimax",
        manager_model_base_url="https://minimax.example/v1",
        manager_model_name="MiniMax-M3",
    )

    manager = build_manager(settings, registry)

    assert isinstance(manager, ModelAssistedManager)
    assert manager.analyzer.client.provider == "minimax"
    assert manager.analyzer.client.model == "MiniMax-M3"
    manager.analyzer.client.close()


def test_runtime_manager_builds_preferred_provider_chain() -> None:
    registry = CapabilityRegistry.from_directory("capabilities").restricted_to_adapters({"fake"})
    manager = build_manager(
        Settings(
            kimi_api_key="sk-kimi",
            minimax_api_key="sk-minimax",
            manager_model_enabled=True,
        ),
        registry,
    )

    assert isinstance(manager, ModelAssistedManager)
    assert isinstance(manager.analyzer.client, FallbackManagerModelClient)
    assert [client.provider for client in manager.analyzer.client.clients] == [
        "kimi",
        "minimax",
    ]
    manager.analyzer.client.close()


def test_runtime_manager_requires_kimi_credentials_when_enabled() -> None:
    registry = CapabilityRegistry.from_directory("capabilities").restricted_to_adapters({"fake"})

    with pytest.raises(ValueError, match="requires credentials"):
        build_manager(Settings(manager_model_enabled=True), registry)


def test_runtime_manager_requires_minimax_credentials_when_selected() -> None:
    registry = CapabilityRegistry.from_directory("capabilities").restricted_to_adapters({"fake"})

    with pytest.raises(ValueError, match="requires credentials"):
        build_manager(
            Settings(manager_model_enabled=True, manager_model_provider="minimax"),
            registry,
        )


def test_runtime_manager_rejects_unknown_provider() -> None:
    registry = CapabilityRegistry.from_directory("capabilities").restricted_to_adapters({"fake"})

    with pytest.raises(ValueError, match="Unsupported model provider"):
        build_manager(
            Settings(manager_model_enabled=True, manager_model_provider="unknown"),
            registry,
        )


def test_runtime_coding_agent_is_opt_in_and_requires_scoped_configuration() -> None:
    assert build_coding_executor(Settings()) is None

    with pytest.raises(ValueError, match="ASSISTANT_CODE_REPOSITORY_PATH"):
        build_coding_executor(Settings(code_agent_enabled=True))


def test_runtime_builds_configured_coding_pull_request_executor(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    executor = build_coding_executor(
        Settings(
            code_agent_enabled=True,
            code_agent_providers="codex",
            code_repository_path=repository,
            code_worktree_root=tmp_path / "worktrees",
            code_agent_executable="/opt/codex",
            code_agent_model="coding-model",
            github_token="github-test",
            github_repository="acme/widget",
            github_base_branch="develop",
        )
    )

    assert executor is not None
    assert executor.repository_path == repository
    assert executor.base_branch == "develop"
    assert executor.agent.runners[0].executable == "/opt/codex"
    assert executor.agent.runners[0].model == "coding-model"
    executor.github.close()


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
        "action": None,
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


def test_worker_pauses_coding_task_for_pull_request_merge_approval(
    service: TaskService,
) -> None:
    class ApprovalExecutor:
        id = "coding-pull-request"

        def execute(self, **_kwargs) -> ExecutionResult:
            return ExecutionResult(
                output={
                    "summary": "Implemented the change",
                    "artifacts": [{"type": "github_pull_request", "number": 17}],
                    "approval_request": {
                        "type": "github_pull_request_merge",
                        "repository": "acme/widget",
                        "number": 17,
                        "url": "https://github.com/acme/widget/pull/17",
                        "expected_head_sha": "a" * 40,
                        "draft": True,
                    },
                }
            )

    task = service.create_task(
        request="Implement a feature",
        required_capabilities=["pull_request_creation"],
    )
    worker = make_worker(
        service,
        executors={"coding-pull-request": ApprovalExecutor()},  # type: ignore[dict-item]
    )

    assert worker.run_once() is True
    assert service.get_task(task.id).status == TaskStatus.WAITING_FOR_APPROVAL.value
    event_types = [event.event_type for event in service.list_events(task.id)]
    assert event_types[-2:] == ["ARTIFACT_CREATED", "APPROVAL_REQUESTED"]
    assert "TASK_COMPLETED" not in event_types
