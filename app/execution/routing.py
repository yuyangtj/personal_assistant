"""Which executor runs a task: the first installed adapter providing everything it needs.

A task names what it needs in ``required_capabilities`` (none for a chat reply,
``web_research``, or ``coding`` and ``pull_request_creation``). No model takes part.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

#: A chat reply names nothing; it needs an executor that can carry out a task.
DEFAULT_REQUIREMENTS = ("task_execution",)

#: In order of preference.
ROUTES: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "coding-pull-request",
        frozenset(
            {
                "coding",
                "coding-pull-request",
                "pull_request_creation",
                "repository_analysis",
                "shell_execution",
                "testing",
            }
        ),
    ),
    ("tool-agent", frozenset({"web_research"})),
    ("model-conversation", frozenset({"conversation", "task_execution"})),
    ("fake", frozenset({"task_execution"})),
)


def adapter_for(required: Sequence[str], installed: Collection[str]) -> str | None:
    """The adapter to run a task needing ``required``, or None when nothing installed can."""
    needs = set(required) or set(DEFAULT_REQUIREMENTS)
    return next(
        (adapter for adapter, provides in ROUTES if adapter in installed and needs <= provides),
        None,
    )
