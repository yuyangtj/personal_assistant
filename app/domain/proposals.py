"""Deciding when a chat message is asking for work rather than just talking.

Chat is where the user thinks out loud; a task is work launched from it. The rule here is
deliberately deterministic and conservative: it only ever *proposes* a task, and the user
(or an explicit client request) has to confirm before anything is created. Consequential
work — anything that would touch a repository or deploy — is always flagged so the client
can confirm before a conversation launches it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MAX_GOAL_CHARACTERS = 200

#: Verbs that read as a request for work when they lead the sentence or follow a polite
#: opener ("can you add…", "please fix…").
WORK_VERBS = frozenset(
    {
        "add",
        "build",
        "check",
        "clean",
        "configure",
        "create",
        "debug",
        "deploy",
        "draft",
        "fix",
        "generate",
        "implement",
        "improve",
        "investigate",
        "migrate",
        "refactor",
        "release",
        "remove",
        "rename",
        "research",
        "review",
        "rewrite",
        "set",
        "ship",
        "test",
        "update",
        "upgrade",
        "wire",
        "write",
    }
)

#: Vocabulary that marks work as consequential: it changes a repository or a deployment,
#: costs real agent time, and must never start without an explicit confirmation.
CONSEQUENTIAL_TERMS = frozenset(
    {
        "branch",
        "ci",
        "codebase",
        "commit",
        "deploy",
        "endpoint",
        "merge",
        "migration",
        "module",
        "package",
        "pipeline",
        "pr",
        "production",
        "pull",
        "release",
        "repo",
        "repository",
        "revert",
        "rollback",
        "schema",
        "service",
        "test",
        "tests",
    }
)

CODING_TERMS = (CONSEQUENTIAL_TERMS - {"deploy", "production", "release", "rollback"}) | {
    "api",
    "app",
    "backend",
    "bug",
    "button",
    "class",
    "component",
    "css",
    "feature",
    "frontend",
    "function",
    "html",
    "javascript",
    "python",
    "theme",
    "ui",
}

_OPENERS = (
    "can you",
    "could you",
    "would you",
    "please",
    "i need you to",
    "i want you to",
    "we need to",
    "we should",
    "you should",
    "let us",
    "let's",
    "lets",
    "go ahead and",
)

_QUESTION_OPENERS = ("what", "why", "who", "when", "where", "which", "how", "is", "are", "does")

_WORD = re.compile(r"[a-z']+")


@dataclass(frozen=True, slots=True)
class TaskProposal:
    """A suggested — never launched — task derived from one chat message."""

    reason: str
    suggested_goal: str
    required_capabilities: list[str] = field(default_factory=list)
    consequential: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "suggested_goal": self.suggested_goal,
            "required_capabilities": list(self.required_capabilities),
            "consequential": self.consequential,
        }


def _strip_opener(words: list[str]) -> list[str]:
    joined = " ".join(words)
    for opener in _OPENERS:
        if joined.startswith(opener + " "):
            return joined[len(opener) + 1 :].split()
    return words


def propose_task(content: str) -> TaskProposal | None:
    """A task proposal when the message asks for work, otherwise ``None``.

    Questions are left alone: "why did that fail?" is a conversation, not a task.
    """
    normalized = " ".join(content.split())
    if not normalized:
        return None

    words = _WORD.findall(normalized.lower())
    if not words:
        return None

    leading = _strip_opener(words)
    if not leading:
        return None

    is_question = normalized.endswith("?")
    asks_for_work = leading[0] in WORK_VERBS and not (is_question and words[0] in _QUESTION_OPENERS)
    if not asks_for_work:
        return None

    vocabulary = set(words)
    consequential = bool(vocabulary & (CONSEQUENTIAL_TERMS | CODING_TERMS))
    capabilities = ["coding"] if vocabulary & CODING_TERMS else []
    goal = (
        normalized
        if len(normalized) <= MAX_GOAL_CHARACTERS
        else (normalized[: MAX_GOAL_CHARACTERS - 1].rstrip() + "…")
    )
    reason = (
        "This asks for work that changes code or infrastructure."
        if consequential
        else "This reads as something actionable."
    )
    return TaskProposal(
        reason=reason,
        suggested_goal=goal,
        required_capabilities=capabilities,
        consequential=consequential,
    )
