from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    """A previous request, spoken reply, and optional trusted outcome, oldest first."""

    request: str
    reply: str
    outcome: str | None = None


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
        history: Sequence[ConversationTurn] = (),
        context: Mapping[str, Any] | None = None,
    ) -> ExecutionResult: ...
