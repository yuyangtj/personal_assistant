from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from app.coding import process
from app.coding.base import CodingAgentError
from app.coding.claude_stream import ClaudeStreamProgress, final_result
from app.coding.runners import ClaudeCodeMiniMaxRunner
from app.execution.fake import ExecutionCancelled

FAKE_AGENT = Path(__file__).with_name("fake_claude_agent.py")
SAMPLE = Path(__file__).with_name("fixtures") / "claude_stream_sample.jsonl"
STEPS = [
    "List files and show calc.py",
    "Append subtract function to calc.py",
    "Write test_calc.py",
    "Run tests and grep for def",
]


def _sample_lines(cwd: Path) -> list[str]:
    return SAMPLE.read_text(encoding="utf-8").replace("{cwd}", str(cwd)).splitlines()


def _runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, pause: float = 1.5
) -> ClaudeCodeMiniMaxRunner:
    executable = tmp_path / "claude"
    executable.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_AGENT}" "$@"\n')
    executable.chmod(0o755)
    environment = {
        "PATH": "/usr/bin:/bin",
        "FAKE_CLAUDE_MODE": mode,
        "FAKE_CLAUDE_PAUSE": str(pause),
    }
    monkeypatch.setattr("app.coding.runners.safe_agent_environment", lambda: dict(environment))
    return ClaudeCodeMiniMaxRunner(
        api_key="minimax-secret",
        base_url="https://api.minimaxi.com/anthropic",
        executable=str(executable),
    )


def _worktree(tmp_path: Path) -> Path:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    return worktree


def test_the_recorded_stream_becomes_tool_steps(tmp_path: Path) -> None:
    progress: list[tuple[str, str]] = []
    stream = ClaudeStreamProgress(tmp_path, lambda text, kind: progress.append((kind, text)))

    for line in _sample_lines(tmp_path):
        stream.feed(line)

    # Bash steps use Claude Code's own description; file paths are relative.
    assert progress == [("tool", step) for step in STEPS]
    assert stream.last_step == "Run tests and grep for def"


def test_repeated_steps_and_unknown_lines_are_skipped(tmp_path: Path) -> None:
    progress: list[str] = []
    stream = ClaudeStreamProgress(tmp_path, lambda text, kind: progress.append(text))
    read = (
        '{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read",'
        f' "input": {{"file_path": "{tmp_path}/app/main.py"}}}}]}}}}'
    )

    for line in ["not json", "[1, 2]", '{"type": "mystery"}', "", read, read]:
        stream.feed(line)

    assert progress == ["Read app/main.py"]


def test_the_final_result_is_read_from_the_last_result_event(tmp_path: Path) -> None:
    output = "\n".join(_sample_lines(tmp_path))

    result, is_error = final_result(output)

    assert result is not None and result.startswith("Added `subtract(a, b)`")
    assert is_error is False
    assert final_result("plain text, no events") == (None, False)


def test_claude_code_reports_steps_while_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _runner(tmp_path, monkeypatch, "quiet", pause=1.5)
    seen: list[tuple[float, str, str]] = []

    report = runner.run(
        worktree=_worktree(tmp_path),
        request="Add subtract",
        timeout_seconds=20,
        is_cancelled=lambda: False,
        on_progress=lambda text, kind: seen.append((time.monotonic(), kind, text)),
    )
    finished = time.monotonic()

    assert [text for _, kind, text in seen if kind == "tool"] == STEPS
    # The first step arrived during the pause, well before the agent exited.
    assert finished - seen[0][0] > 1.0
    assert report.summary == "Done"


def test_an_error_result_fails_the_run_even_with_exit_status_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _runner(tmp_path, monkeypatch, "error")

    with pytest.raises(CodingAgentError) as error:
        runner.run(
            worktree=_worktree(tmp_path),
            request="Add subtract",
            timeout_seconds=20,
            is_cancelled=lambda: False,
        )

    assert error.value.category == "rate_limited"


def test_a_hanging_claude_code_is_cancelled_and_timed_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _runner(tmp_path, monkeypatch, "hang")
    worktree = _worktree(tmp_path)
    started = time.monotonic()

    with pytest.raises(ExecutionCancelled):
        runner.run(
            worktree=worktree,
            request="Add subtract",
            timeout_seconds=20,
            is_cancelled=lambda: time.monotonic() - started > 0.5,
        )
    with pytest.raises(CodingAgentError) as error:
        runner.run(
            worktree=worktree, request="Add subtract", timeout_seconds=1, is_cancelled=lambda: False
        )
    assert error.value.category == "timeout"


def test_a_redirected_run_reports_its_steps_afresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The first run pauses after its first step; the redirect lands during the pause.
    runner = _runner(tmp_path, monkeypatch, "quiet", pause=3)
    inbox = iter([None, "Name it minus"])
    progress: list[tuple[str, str]] = []

    report = runner.run(
        worktree=_worktree(tmp_path),
        request="Add subtract",
        timeout_seconds=30,
        is_cancelled=lambda: False,
        on_progress=lambda text, kind: progress.append((kind, text)),
        next_guidance=lambda: next(inbox, None),
    )

    assert ("milestone", "Redirected: Name it minus") in progress
    assert progress.count(("tool", STEPS[0])) == 2  # a new listener for the restart
    assert report.summary == "Done, redirected"


def test_a_quiet_agent_is_reported_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process, "QUIET_AFTER", 0.5)
    runner = _runner(tmp_path, monkeypatch, "quiet", pause=1.5)
    progress: list[tuple[str, str]] = []

    runner.run(
        worktree=_worktree(tmp_path),
        request="Add subtract",
        timeout_seconds=20,
        is_cancelled=lambda: False,
        on_progress=lambda text, kind: progress.append((kind, text)),
    )

    notices = [text for kind, text in progress if text.startswith("No activity")]
    assert notices == [f"No activity for 1 min (last step: {STEPS[0]})"]


def test_partial_lines_are_joined_across_reads(tmp_path: Path) -> None:
    path = tmp_path / "stdout.log"
    path.write_bytes(b'{"a": 1}\n{"b"')
    reader = process._LineReader(path)

    assert reader.read_lines() == ['{"a": 1}']
    with path.open("ab") as stream:
        stream.write(b': 2}\n{"c": 3}')
    assert reader.read_lines() == ['{"b": 2}']
    assert reader.read_lines(final=True) == ['{"c": 3}']


def test_a_failing_listener_does_not_stop_the_agent(tmp_path: Path) -> None:
    class Broken:
        last_step = ""

        def feed(self, line: str) -> None:
            raise RuntimeError("boom")

    output = process.run_code_agent_process(
        command=[sys.executable, "-c", "print('one'); print('two')"],
        worktree=tmp_path,
        timeout_seconds=10,
        is_cancelled=lambda: False,
        environment={"PATH": "/usr/bin:/bin"},
        display_name="Agent",
        output_listener=Broken,
    )

    assert output.split() == ["one", "two"]
