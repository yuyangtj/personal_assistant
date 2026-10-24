"""A short spec for a coding task, written before the agent starts.

The spec turns a chat request into something the agent and the reviewer can check:
the goal, acceptance criteria, what is in and out of scope, and how to verify it.
Writing it is best effort: when the planner model fails, the task runs from the
request alone, as before.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.coding.git import git
from app.integrations.chat import ChatClient, ChatMessage
from app.integrations.model_json import extract_json_object

logger = logging.getLogger(__name__)

_MAX_PATHS = 400
_GUIDE_CHARS = 4000
_GUIDE_FILES = ("AGENTS.md", "CLAUDE.md", "README.md")

SPEC_PROMPT = """You plan a change to a code repository before a coding agent makes it.
Write a short spec the agent will follow and a reviewer will check the result against.

Reply with only a JSON object:
{
  "goal": "one sentence: what the change achieves",
  "acceptance": ["When <situation>, <what> should <result>", ...],
  "in_scope": ["files or areas the change may touch"],
  "out_of_scope": ["what this change must not do or touch"],
  "checks": ["how to verify it, such as a test to add or a command to run"]
}

Rules:
- 1 to 8 acceptance criteria, each one observable and testable. Cover only what the
  request asks for; don't invent extra features.
- Base in_scope on the file list below; name real paths where you can.
- Keep every item to one short line. Keep the user's language for the goal."""


class CodingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=500)
    acceptance: list[str] = Field(min_length=1, max_length=8)
    in_scope: list[str] = Field(default_factory=list, max_length=20)
    out_of_scope: list[str] = Field(default_factory=list, max_length=20)
    checks: list[str] = Field(default_factory=list, max_length=10)


def render_spec(spec: CodingSpec) -> str:
    """The spec as compact text, for the agent's prompt and the task view."""
    sections = [f"Goal: {spec.goal}", "Acceptance criteria:"]
    sections += [f"{index}. {item}" for index, item in enumerate(spec.acceptance, start=1)]
    for title, items in (
        ("In scope", spec.in_scope),
        ("Out of scope", spec.out_of_scope),
        ("Checks", spec.checks),
    ):
        if items:
            sections.append(f"{title}: " + "; ".join(items))
    return "\n".join(sections)


class SpecWriter:
    """Asks a model for a CodingSpec; None when it can't get a usable one."""

    def __init__(self, client: ChatClient, *, max_tokens: int = 2000):
        self.client = client
        self.max_tokens = max_tokens

    def write(self, request: str, worktree: Path) -> CodingSpec | None:
        try:
            completion = self.client.complete(
                [
                    ChatMessage("system", SPEC_PROMPT),
                    ChatMessage("user", _repository_context(request, worktree)),
                ],
                max_tokens=self.max_tokens,
            )
            return CodingSpec.model_validate(extract_json_object(completion.text))
        except (ValueError, ValidationError) as error:
            logger.warning("Coding spec reply was not usable: %s", error)
        except Exception:  # the planner is best effort; never fail the task over it
            logger.warning("Coding spec could not be written", exc_info=True)
        return None


def _repository_context(request: str, worktree: Path) -> str:
    try:
        paths = git(worktree, "ls-files", timeout=30).splitlines()
    except Exception:
        paths = []
    listed = "\n".join(paths[:_MAX_PATHS])
    if len(paths) > _MAX_PATHS:
        listed += f"\n… and {len(paths) - _MAX_PATHS} more files"
    parts = [f"REQUEST\n{request.strip()}\nEND REQUEST", f"FILES\n{listed or '(none)'}\nEND FILES"]
    for name in _GUIDE_FILES:
        guide = worktree / name
        if guide.is_file():
            text = guide.read_text(encoding="utf-8", errors="replace")[:_GUIDE_CHARS]
            parts.append(f"{name} (start)\n{text}\nEND {name}")
            break
    return "\n\n".join(parts)
