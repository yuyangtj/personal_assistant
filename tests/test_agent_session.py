from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.execution.coding import CodingAgentError, KimiAcpRunner, worktree_summary
from app.execution.fake import ExecutionCancelled
from app.worker import ProgressReporter

FAKE_AGENT = Path(__file__).with_name("fake_acp_agent.py")


def _runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> KimiAcpRunner:
    executable = tmp_path / "kimi"
    executable.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_AGENT}"\n', encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("FAKE_ACP_MODE", mode)
    # The runner passes only allow-listed variables to the agent; let the mode through.
    monkeypatch.setattr(
        "app.execution.coding._safe_agent_environment",
        lambda: {"PATH": "/usr/bin:/bin", "FAKE_ACP_MODE": mode},
    )
    return KimiAcpRunner(
        api_key="kimi-secret", base_url="https://api.kimi.com/coding/v1", executable=str(executable)
    )


def _worktree(tmp_path: Path) -> Path:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "README.md").write_text("before\n", encoding="utf-8")
    return worktree


def test_acp_session_streams_plan_and_steps_and_returns_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = _worktree(tmp_path)
    progress: list[tuple[str, str]] = []

    report = _runner(tmp_path, monkeypatch, "work").run(
        worktree=worktree,
        request="Update the README",
        timeout_seconds=20,
        is_cancelled=lambda: False,
        on_progress=lambda text, kind: progress.append((kind, text)),
    )

    assert report.summary == "Updated the README"
    assert (worktree / "README.md").read_text() == "after\n"
    assert progress == [
        ("plan", "▸ Edit README\n· Check the result"),
        ("tool", "Writing README.md"),  # the worktree prefix is dropped
        ("tool", "Running: git diff --stat"),
        ("plan", "✓ Edit README\n✓ Check the result"),
    ]
    # Permission requests are answered with an allow option, as the one-shot mode does.
    assert json.loads((worktree / ".permission").read_text())["outcome"]["optionId"] == "ok"


def test_acp_session_cancels_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def cancelled_after_a_moment() -> bool:
        calls["n"] += 1
        return calls["n"] > 4

    with pytest.raises(ExecutionCancelled):
        _runner(tmp_path, monkeypatch, "hang").run(
            worktree=_worktree(tmp_path),
            request="Update the README",
            timeout_seconds=20,
            is_cancelled=cancelled_after_a_moment,
        )


def test_acp_session_failure_is_categorized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(CodingAgentError) as raised:
        _runner(tmp_path, monkeypatch, "crash").run(
            worktree=_worktree(tmp_path),
            request="Update the README",
            timeout_seconds=20,
            is_cancelled=lambda: False,
        )

    assert raised.value.category == "rate_limited"


def test_acp_session_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(CodingAgentError) as raised:
        _runner(tmp_path, monkeypatch, "hang").run(
            worktree=_worktree(tmp_path),
            request="Update the README",
            timeout_seconds=1,
            is_cancelled=lambda: False,
        )

    assert raised.value.category == "timeout"


def test_worktree_summary_names_files_and_line_counts(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / "a.py").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "a.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-qm",
            "init",
        ],
        check=True,
    )
    assert worktree_summary(repository) == ""

    (repository / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (repository / "b.py").write_text("new\n", encoding="utf-8")

    assert worktree_summary(repository) == "Changed a.py, b.py (+2 −0)"


class _Recorder:
    def __init__(self) -> None:
        self.recorded: list[tuple[str, str]] = []

    def record_progress(self, task_id: str, text: str, kind: str) -> None:
        self.recorded.append((kind, text))


def test_progress_reporter_keeps_plans_and_throttles_steps() -> None:
    service = _Recorder()
    now = {"t": 0.0}
    reporter = ProgressReporter(service, "task", interval=10, clock=lambda: now["t"])

    reporter("▸ Edit", "plan")
    reporter("Writing a.py", "tool")
    now["t"] = 3
    reporter("Writing b.py", "tool")  # too soon: dropped
    reporter("▸ Edit", "plan")  # unchanged: dropped
    reporter("✓ Edit", "plan")
    now["t"] = 12
    reporter("Running: pytest", "tool")

    assert service.recorded == [
        ("plan", "▸ Edit"),
        ("tool", "Writing a.py"),
        ("plan", "✓ Edit"),
        ("tool", "Running: pytest"),
    ]


def test_running_tasks_show_their_latest_progress(client: TestClient) -> None:
    service = client.app.state.task_service
    chat = client.post("/chat-sessions", json={}).json()["id"]
    message = client.post(f"/chat-sessions/{chat}/messages", json={"content": "hello"}).json()
    task = service.create_task_from_message(chat, message["message"]["id"])

    service.record_progress(task.id, "▸ Edit README", "plan")
    service.record_progress(task.id, "Writing README.md", "tool")
    service.record_progress(task.id, "Running: pytest", "tool")

    detail = client.get(f"/tasks/{task.id}").json()
    assert (detail["progress"], detail["plan"]) == ("Running: pytest", "▸ Edit README")
    (listed,) = client.get(f"/chat-sessions/{chat}/tasks").json()["tasks"]
    assert listed["progress"] == "Running: pytest"
    assert client.get(f"/tasks/{task.id}/context").status_code == 200
