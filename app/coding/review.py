"""An independent review of a coding agent's diff against the task's spec.

The reviewer is a plain model call, so it can't change the worktree. It's best effort:
when it can't answer, the run continues as if there were no review.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.coding.git import git
from app.integrations.chat import ChatClient, ChatMessage
from app.integrations.model_json import extract_json_object

logger = logging.getLogger(__name__)

MAX_DIFF_CHARS = 60_000

REVIEW_PROMPT = """You review a change a coding agent made to a repository.
Judge it only against the spec or request you are given: is every acceptance
criterion met, and did the change stay inside its scope? Validation (tests, lint)
already ran; its results are listed. Ignore style preferences and ideas for
extra work.

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
        self, reference: str, worktree: Path, validation: list[dict[str, Any]]
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


def _review_input(reference: str, worktree: Path, validation: list[dict[str, Any]]) -> str:
    # Stage everything so new files show up; the executor commits all of it next anyway.
    git(worktree, "add", "-A")
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
