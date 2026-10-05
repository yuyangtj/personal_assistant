"""What every coding runner shares: its contract, report and errors."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.coding.agent_session import GuidanceSource, ProgressCallback

logger = logging.getLogger(__name__)


class CodingAgentError(RuntimeError):
    """A safe coding-workflow failure suitable for task audit events."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "execution_failed",
        details: dict[str, Any] | None = None,
        user_message: str | None = None,
    ):
        super().__init__(message)
        self.category = category
        self.details = details or {}
        # A short, safe sentence for the chat reply; the message stays internal.
        self.user_message = user_message


class CodeAgentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=4000)
    tests: list[str] = Field(default_factory=list, max_length=50)
    notes: list[str] = Field(default_factory=list, max_length=50)


@dataclass(frozen=True, slots=True)
class RunnerAttempt:
    provider: str
    outcome: str
    category: str | None = None


CODE_AGENT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "tests": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "tests", "notes"],
    "additionalProperties": False,
}


class CodeAgentRunner(Protocol):
    provider: str

    def run(
        self,
        *,
        worktree: Path,
        request: str,
        timeout_seconds: int,
        is_cancelled: Callable[[], bool],
        on_progress: ProgressCallback | None = None,
        next_guidance: GuidanceSource | None = None,
    ) -> CodeAgentReport: ...


def safe_agent_environment() -> dict[str, str]:
    allowed = {
        "CODEX_HOME",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "PATH",
        "SHELL",
        "SSL_CERT_FILE",
        "TERM",
        "TMPDIR",
        "USER",
    }
    return {name: value for name, value in os.environ.items() if name in allowed}
