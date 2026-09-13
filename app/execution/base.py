from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    output: dict[str, Any] = field(default_factory=dict)


class Executor(Protocol):
    id: str

    def execute(
        self,
        *,
        task_id: str,
        request: str,
        is_cancelled: Callable[[], bool],
    ) -> ExecutionResult: ...
