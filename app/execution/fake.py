from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from app.execution.base import ConversationTurn, ExecutionResult


class ExecutionCancelled(RuntimeError):
    pass


class FakeExecutor:
    """Deterministic executor used to exercise orchestration before real agents exist."""

    id = "fake-executor"

    def __init__(self, *, delay_seconds: float = 0.1):
        self.delay_seconds = max(0.0, delay_seconds)

    def execute(
        self,
        *,
        task_id: str,
        request: str,
        is_cancelled: Callable[[], bool],
        history: Sequence[ConversationTurn] = (),
    ) -> ExecutionResult:
        deadline = time.monotonic() + self.delay_seconds
        while time.monotonic() < deadline:
            if is_cancelled():
                raise ExecutionCancelled(f"Task {task_id} was cancelled")
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))

        if is_cancelled():
            raise ExecutionCancelled(f"Task {task_id} was cancelled")

        return ExecutionResult(
            output={
                "summary": f"Fake executor completed: {request}",
                "reply": f"All done. I handled your request: {request}",
                "executor": self.id,
            }
        )
