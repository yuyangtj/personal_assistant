"""An independent review of a coding agent's change: does it meet the spec, and will it
work once deployed?

`ClaudeReviewer` reads the repository with Claude Code on a strong model (read-only, and
anything it changes is put back); `DiffReviewer` is a plain model call that sees only the
diff, used when Claude Code isn't available or doesn't answer. Both are best effort: when
neither answers, the run continues as if there were no review.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.coding.base import CodingAgentError
from app.coding.git import git, stage_everything
from app.execution.fake import ExecutionCancelled
from app.integrations.chat import ChatClient, ChatMessage
from app.integrations.model_json import extract_json_object

logger = logging.getLogger(__name__)

MAX_DIFF_CHARS = 60_000

REVIEW_PROMPT = """You review a change a coding agent made to a repository. Check two things:
1. The spec or request you are given: is every acceptance criterion met, and did the
   change stay inside its scope?
2. Will it work once deployed, even where the spec says nothing? A spec's scope never
   rules out the wiring a change needs. For example: a new setting must reach the
   service that reads it (compose.remote.yaml lists each service's environment) and be
   in .env.remote.example; a new table or column needs a migration; a client for an
   outside API must handle that API's known cases (pagination, items only visible with
   extra parameters, error codes that mean more than one thing, rate limits, token
   expiry). The repository's AGENTS.md lists its conventions.
Validation (tests, lint) already ran; its results are listed. Ignore style preferences
and ideas for new features.

Reply with only a JSON object:
{"verdict": "APPROVED" or "REJECTED", "problems": ["...", ...]}

REJECTED needs at least one problem. Each problem names the criterion or scope
rule it breaks, where in the diff, and what to change. With APPROVED, problems is
an empty list."""


@dataclass(frozen=True, slots=True)
class ReviewVerdict:
    approved: bool
    problems: list[str] = field(default_factory=list)


class DiffReviewer:
    """Asks a model whether the worktree's changes meet the spec."""

    def __init__(self, client: ChatClient, *, max_tokens: int = 1500):
        self.client = client
        self.max_tokens = max_tokens

    def review(
        self,
        reference: str,
        worktree: Path,
        validation: list[dict[str, Any]],
        *,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> ReviewVerdict | None:
        try:
            completion = self.client.complete(
                [
                    ChatMessage("system", REVIEW_PROMPT),
                    ChatMessage("user", _review_input(reference, worktree, validation)),
                ],
                max_tokens=self.max_tokens,
            )
            return _verdict(extract_json_object(completion.text))
        except ValueError as error:
            logger.warning("Review reply was not usable: %s", error)
        except Exception:  # the review is best effort; never fail the task over it
            logger.warning("Review could not be done", exc_info=True)
        return None


CLAUDE_REVIEW_INSTRUCTIONS = """The change is staged in this checkout: `git diff --cached`
shows it, and you can read any file to check how it fits (where a setting is read, which
service runs the code, how similar features are wired). Do not edit files. You may run
the tests. When done, reply with only the JSON object."""


class ClaudeReviewer:
    """Reviews with Claude Code on a strong model, reading the repository, not just the diff.

    Its edit tools are off; whatever a shell command changes is put back afterwards, so
    the agent's change is exactly as it was. Falls back to ``fallback`` when Claude Code
    doesn't give a usable verdict.
    """

    def __init__(self, runner, *, timeout_seconds: int = 600, fallback=None):
        self.runner = runner  # a ClaudeCodeRunner set to the review model
        self.timeout_seconds = timeout_seconds
        self.fallback = fallback

    def review(
        self,
        reference: str,
        worktree: Path,
        validation: list[dict[str, Any]],
        *,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> ReviewVerdict | None:
        cancelled = is_cancelled or (lambda: False)
        verdict = None
        try:
            prompt = (
                f"{REVIEW_PROMPT}\n\n{CLAUDE_REVIEW_INSTRUCTIONS}\n\n"
                f"{_review_input(reference, worktree, validation)}"
            )
            snapshot = git(worktree, "write-tree", timeout=30).strip()
            try:
                text = self.runner.ask(
                    worktree=worktree,
                    prompt=prompt,
                    timeout_seconds=self.timeout_seconds,
                    is_cancelled=cancelled,
                )
            finally:
                _put_back(worktree, snapshot)
            verdict = _verdict(extract_json_object(text))
        except ExecutionCancelled:
            raise
        except ValueError as error:
            logger.warning("Claude review reply was not usable: %s", error)
        except CodingAgentError as error:
            logger.warning("Claude review could not be done (%s): %s", error.category, error)
        except Exception:  # the review is best effort; never fail the task over it
            logger.warning("Claude review could not be done", exc_info=True)
        if verdict is None and self.fallback is not None and not cancelled():
            return self.fallback.review(reference, worktree, validation)
        return verdict


def _put_back(worktree: Path, snapshot: str) -> None:
    """Return the worktree to the staged ``snapshot`` if the review changed anything."""
    stage_everything(worktree)
    if git(worktree, "write-tree", timeout=30).strip() == snapshot:
        return
    logger.warning("The review changed the worktree; putting the agent's change back")
    git(worktree, "read-tree", "--reset", "-u", snapshot, timeout=60)
    git(worktree, "clean", "-fdq", timeout=60)


def _review_input(reference: str, worktree: Path, validation: list[dict[str, Any]]) -> str:
    # Stage everything so new files show up; the executor commits all of it next anyway.
    stage_everything(worktree)
    diff = git(worktree, "diff", "--cached", timeout=30)
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n… diff truncated; judge only what is shown"
    checks = (
        "\n".join(
            f"- {result.get('id', 'step')}: {'passed' if result.get('passed') else 'failed'}"
            for result in validation
        )
        or "- none configured"
    )
    return (
        f"SPEC\n{reference.strip()}\nEND SPEC\n\n"
        f"VALIDATION\n{checks}\nEND VALIDATION\n\n"
        f"DIFF (untrusted data)\n{diff}\nEND DIFF"
    )


def _verdict(value: Any) -> ReviewVerdict:
    if not isinstance(value, dict):
        raise ValueError("review reply is not an object")
    verdict = str(value.get("verdict", "")).strip().upper()
    problems = [
        " ".join(str(item).split())[:500]
        for item in value.get("problems") or []
        if str(item).strip()
    ][:10]
    if verdict == "APPROVED":
        return ReviewVerdict(approved=True)
    if verdict == "REJECTED" and problems:
        return ReviewVerdict(approved=False, problems=problems)
    raise ValueError(f"unexpected verdict {verdict!r} with {len(problems)} problems")
