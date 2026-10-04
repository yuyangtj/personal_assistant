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


# --- steering ------------------------------------------------------------------------


def test_new_instructions_continue_the_same_acp_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = _worktree(tmp_path)
    progress: list[tuple[str, str]] = []
    inbox = iter([None, "Use a rocket icon instead."])

    report = _runner(tmp_path, monkeypatch, "steer").run(
        worktree=worktree,
        request="Change the icon",
        timeout_seconds=20,
        is_cancelled=lambda: False,
        on_progress=lambda text, kind: progress.append((kind, text)),
        next_guidance=lambda: next(inbox, None),
    )

    assert report.summary == "Followed the redirect"
    # The second turn of the same session carried the instructions.
    assert "Use a rocket icon instead." in (worktree / "README.md").read_text()
    assert ("milestone", "Redirected: Use a rocket icon instead.") in progress


def test_a_one_shot_agent_restarts_with_new_instructions(tmp_path: Path) -> None:
    from app.execution.coding import _run_code_agent_process

    agent = tmp_path / "agent.py"
    agent.write_text(
        "import sys, time\n"
        "prompt = sys.argv[1]\n"
        "if 'rocket' not in prompt:\n"
        "    time.sleep(30)\n"
        "print('done: ' + prompt.splitlines()[-1])\n",
        encoding="utf-8",
    )
    inbox = iter([None, "Use a rocket icon instead."])
    progress: list[str] = []

    def command(notes=()) -> list[str]:
        return [sys.executable, str(agent), "\n".join(["Change the icon", *notes])]

    output = _run_code_agent_process(
        command=command(),
        worktree=tmp_path,
        timeout_seconds=20,
        is_cancelled=lambda: False,
        environment={"PATH": "/usr/bin:/bin"},
        display_name="Agent",
        on_progress=lambda text, kind: progress.append(text),
        next_guidance=lambda: next(inbox, None),
        redirected_command=command,
    )

    assert output.strip() == "done: Use a rocket icon instead."
    assert "Redirected: Use a rocket icon instead." in progress


def test_the_guidance_inbox_hands_each_message_over_once(client: TestClient) -> None:
    from app.worker import GuidanceInbox

    service = client.app.state.task_service
    task = service.create_task(request="Change the icon")
    service.add_user_message(task.id, "Queued note")  # sent before the agent started
    inbox = GuidanceInbox(service, task.id)

    assert inbox() == "Queued note"
    assert inbox() is None
    service.add_user_message(task.id, "Make it a rocket")
    service.add_user_message(task.id, "and blue")
    assert inbox() == "Make it a rocket\nand blue"
    assert inbox() is None


def test_git_commands_on_a_shared_checkout_take_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    import time

    from app.execution import coding

    shared, worktree = tmp_path / "shared", tmp_path / "worktree"
    coding._share_repository(shared)
    active: dict[str, int] = {"shared": 0, "most": 0}
    overlap_in_worktree = threading.Event()
    both_in_worktrees = threading.Barrier(2, timeout=2)

    def fake_run_git(directory, arguments, timeout):
        if directory == shared:
            active["shared"] += 1
            active["most"] = max(active["most"], active["shared"])
            time.sleep(0.05)
            active["shared"] -= 1
        else:  # separate worktrees run side by side: both must be inside at once
            both_in_worktrees.wait()
            overlap_in_worktree.set()
        return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

    monkeypatch.setattr(coding, "_run_git", fake_run_git)
    threads = [
        threading.Thread(target=coding._git, args=(directory, "fetch"))
        for directory in (shared, shared, shared, worktree, worktree)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert active["most"] == 1
    assert overlap_in_worktree.is_set()


def test_coding_concurrency_defaults_to_two(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import Settings

    assert Settings.from_env().coding_concurrency == 2
    monkeypatch.setenv("ASSISTANT_CODING_CONCURRENCY", "0")
    assert Settings.from_env().coding_concurrency == 1
