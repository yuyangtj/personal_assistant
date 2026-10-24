from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.coding import review as review_module
from app.coding.review import DiffReviewer, ReviewVerdict
from app.coding.spec import CodingSpec, SpecWriter, render_spec
from app.integrations.chat import ChatCompletion, ChatMessage, ProviderError


class ScriptedChat:
    provider = "scripted"
    model = "scripted"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[list[ChatMessage]] = []

    def complete(self, messages, *, max_tokens: int = 700) -> ChatCompletion:
        self.requests.append(list(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ChatCompletion(text=reply, model=self.model)

    def close(self) -> None:
        pass


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "AGENTS.md").write_text("Use uv run pytest for tests.\n", encoding="utf-8")
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"],
        check=True,
    )
    return repo


SPEC = {
    "goal": "Add subtraction",
    "acceptance": ["When subtract(5, 3) is called, it should return 2"],
    "in_scope": ["app.py", "tests/test_app.py"],
    "out_of_scope": ["Changing add()"],
    "checks": ["uv run pytest"],
}


def test_a_spec_is_written_from_the_request_and_the_repository(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    chat = ScriptedChat("<think>planning</think>Here it is:\n" + json.dumps(SPEC))

    spec = SpecWriter(chat).write("Add a subtract function", repo)

    assert spec == CodingSpec(**SPEC)
    prompt = chat.requests[0][1].content
    assert "Add a subtract function" in prompt
    assert "app.py" in prompt and "AGENTS.md" in prompt
    assert "Use uv run pytest for tests." in prompt
    assert render_spec(spec) == (
        "Goal: Add subtraction\n"
        "Acceptance criteria:\n"
        "1. When subtract(5, 3) is called, it should return 2\n"
        "In scope: app.py; tests/test_app.py\n"
        "Out of scope: Changing add()\n"
        "Checks: uv run pytest"
    )


def test_an_unusable_spec_reply_gives_no_spec(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    too_many = {**SPEC, "acceptance": [f"criterion {n}" for n in range(9)]}
    chat = ScriptedChat(
        "no json here",
        json.dumps(too_many),
        json.dumps({**SPEC, "extra": 1}),
        ProviderError("MiniMax timed out"),
    )
    writer = SpecWriter(chat)

    assert [writer.write("Add subtract", repo) for _ in range(4)] == [None] * 4


def _change(repo: Path) -> None:
    (repo / "app.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    (repo / "test_app.py").write_text("from app import subtract\n", encoding="utf-8")


def test_the_reviewer_sees_the_whole_diff_and_returns_a_verdict(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _change(repo)
    chat = ScriptedChat(
        json.dumps({"verdict": "APPROVED", "problems": []}),
        'Verdict: {"verdict": "rejected", "problems": ["Criterion 1: no test calls subtract"]}',
    )
    reviewer = DiffReviewer(chat)
    validation = [{"id": "tests", "passed": True}]

    assert reviewer.review("Goal: Add subtraction", repo, validation) == ReviewVerdict(True)
    assert reviewer.review("Goal: Add subtraction", repo, validation) == ReviewVerdict(
        False, ["Criterion 1: no test calls subtract"]
    )
    sent = chat.requests[0][1].content
    assert "Goal: Add subtraction" in sent
    assert "+def subtract(a, b):" in sent
    assert "test_app.py" in sent  # a new, untracked file is part of the diff
    assert "- tests: passed" in sent


def test_a_large_diff_is_cut_with_a_note(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    _change(repo)
    monkeypatch.setattr(review_module, "MAX_DIFF_CHARS", 80)
    chat = ScriptedChat(json.dumps({"verdict": "APPROVED", "problems": []}))

    DiffReviewer(chat).review("Goal", repo, [])

    assert "diff truncated" in chat.requests[0][1].content


def test_an_unusable_review_gives_no_verdict(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _change(repo)
    chat = ScriptedChat(
        "looks fine to me",
        json.dumps({"verdict": "REJECTED", "problems": []}),  # a rejection needs a reason
        json.dumps({"verdict": "MAYBE", "problems": ["x"]}),
        ProviderError("down"),
    )
    reviewer = DiffReviewer(chat)

    assert [reviewer.review("Goal", repo, []) for _ in range(4)] == [None] * 4
