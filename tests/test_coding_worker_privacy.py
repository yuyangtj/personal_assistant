from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from app.execution.base import ConversationTurn, ExecutionResult
from app.services import TaskService
from app.worker import TaskWorker


class RecordingCodingExecutor:
    id = "coding-pull-request"

    def __init__(self):
        self.history: Sequence[ConversationTurn] | None = None
        self.context: Mapping = {}

    def execute(
        self,
        *,
        task_id: str,
        request: str,
        is_cancelled: Callable[[], bool],
        history: Sequence[ConversationTurn] = (),
        context: Mapping | None = None,
    ) -> ExecutionResult:
        self.history = history
        self.context = context or {}
        return ExecutionResult(output={"summary": "Implemented"})


def test_coding_execution_does_not_read_private_or_general_chat_context(
    service: TaskService, monkeypatch
) -> None:
    executor = RecordingCodingExecutor()
    worker = TaskWorker(
        service=service,
        executors={executor.id: executor},
        worker_id="coding",
        coding_only=True,
    )
    task = service.create_task(
        request="Implement a feature",
        required_capabilities=["coding", "pull_request_creation"],
        source_context={"repository_id": "personal-assistant"},
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("coding execution attempted a general-context read")

    monkeypatch.setattr(service, "conversation_history", forbidden)
    monkeypatch.setattr(service, "work_item_context", forbidden)
    monkeypatch.setattr(service, "is_home_chat", forbidden)

    assert worker.run_once()

    assert executor.history == ()
    assert "work_items" not in executor.context
    assert "memories" not in executor.context
    assert "recent" not in executor.context
    assert "home" not in executor.context
    assert service.get_task(task.id).status == "completed"
