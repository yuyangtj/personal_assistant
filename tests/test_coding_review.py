"""The Opus reviewer: reads the repository, can't change the agent's work, falls back."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.coding.base import CodingAgentError
from app.coding.plan import PLAN_PROMPT
from app.coding.review import REVIEW_PROMPT, ClaudeReviewer, DiffReviewer, ReviewVerdict
from app.execution.fake import ExecutionCancelled
from tests.test_coding_spec import ScriptedChat, _change, _repo

APPROVED = json.dumps({"verdict": "APPROVED", "problems": []})


class FakeClaude:
    """Stands in for ClaudeCodeRunner.ask; can scribble on the worktree like a shell would."""

    def __init__(self, answer, *, scribble=None):
        self.answer = answer
        self.scribble = scribble
        self.prompts: list[str] = []
        self.cancel_checks: list = []

    def ask(self, *, worktree: Path, prompt: str, is_cancelled, **_kwargs) -> str:
        self.prompts.append(prompt)
        self.cancel_checks.append(is_cancelled)
        if self.scribble:
            self.scribble(worktree)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _worktree_state(repo: Path) -> str:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    return subprocess.run(
        ["git", "-C", str(repo), "write-tree"], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_it_reviews_the_staged_change_with_the_spec_and_deploy_questions(tmp_path) -> None:
    repo = _repo(tmp_path)
    _change(repo)
    claude = FakeClaude('{"verdict": "REJECTED", "problems": ["compose.remote.yaml: X"]}')

    verdict = ClaudeReviewer(claude).review(
        "Goal: Add subtraction", repo, [{"id": "tests", "passed": True}]
    )

    assert verdict == ReviewVerdict(False, ["compose.remote.yaml: X"])
    prompt = claude.prompts[0]
    assert "Goal: Add subtraction" in prompt
    assert "+def subtract(a, b):" in prompt
    assert "git diff --cached" in prompt
    assert "Will it work once deployed" in prompt


def test_whatever_the_review_changes_is_put_back(tmp_path) -> None:
    repo = _repo(tmp_path)
    _change(repo)
    before = _worktree_state(repo)

    def scribble(worktree: Path) -> None:
        (worktree / "app.py").write_text("broken\n", encoding="utf-8")
        (worktree / "test_app.py").unlink()
        (worktree / "stray.txt").write_text("x", encoding="utf-8")

    verdict = ClaudeReviewer(FakeClaude(APPROVED, scribble=scribble)).review("Goal", repo, [])

    assert verdict == ReviewVerdict(True)
    assert _worktree_state(repo) == before
    assert "def subtract" in (repo / "app.py").read_text(encoding="utf-8")
    assert not (repo / "stray.txt").exists()


def test_the_change_is_put_back_even_when_claude_fails(tmp_path) -> None:
    repo = _repo(tmp_path)
    _change(repo)
    before = _worktree_state(repo)

    def scribble(worktree: Path) -> None:
        (worktree / "app.py").write_text("broken\n", encoding="utf-8")

    claude = FakeClaude(CodingAgentError("down", category="agent_failed"), scribble=scribble)
    assert ClaudeReviewer(claude).review("Goal", repo, []) is None
    assert _worktree_state(repo) == before


@pytest.mark.parametrize(
    "answer", ["looks fine", CodingAgentError("quota", category="quota_exhausted")]
)
def test_without_a_usable_verdict_the_diff_reviewer_takes_over(tmp_path, answer) -> None:
    repo = _repo(tmp_path)
    _change(repo)
    chat = ScriptedChat(APPROVED)

    reviewer = ClaudeReviewer(FakeClaude(answer), fallback=DiffReviewer(chat))

    assert reviewer.review("Goal", repo, []) == ReviewVerdict(True)
    assert len(chat.requests) == 1


def test_a_cancelled_review_does_not_fall_back(tmp_path) -> None:
    repo = _repo(tmp_path)
    _change(repo)
    chat = ScriptedChat(APPROVED)
    reviewer = ClaudeReviewer(FakeClaude("nonsense"), fallback=DiffReviewer(chat))

    assert reviewer.review("Goal", repo, [], is_cancelled=lambda: True) is None
    assert chat.requests == []


def test_cancellation_reaches_claude_and_is_raised(tmp_path) -> None:
    repo = _repo(tmp_path)
    _change(repo)
    claude = FakeClaude(ExecutionCancelled("stop"))
    check = lambda: False  # noqa: E731

    with pytest.raises(ExecutionCancelled):
        ClaudeReviewer(claude).review("Goal", repo, [], is_cancelled=check)
    assert claude.cancel_checks == [check]


def test_the_planner_and_reviewers_ask_about_deploy_wiring_and_api_pitfalls() -> None:
    for prompt in (PLAN_PROMPT, REVIEW_PROMPT):
        assert "compose.remote.yaml" in prompt
        assert "rate limits" in prompt
