"""Claude Code and Codex on the user's subscriptions, on a server without a browser."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.coding.process import failure_category
from app.coding.runners import ClaudeCodeRunner, CodexCliRunner, FallbackCodeAgentRunner


def test_claude_code_on_the_subscription_gets_the_token_and_no_api_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = {}

    def fake_process(**kwargs):
        seen.update(kwargs)
        return json.dumps({"type": "result", "is_error": False, "result": "Changed README."})

    monkeypatch.setattr("app.coding.runners.run_code_agent_process", fake_process)
    monkeypatch.setattr(
        "app.coding.runners.safe_agent_environment",
        lambda: {"PATH": "/usr/bin", "HOME": "/tmp/h"},
    )

    runner = ClaudeCodeRunner.subscription(oauth_token="sk-ant-oat-x")
    report = runner.run(
        worktree=tmp_path, request="fix it", timeout_seconds=60, is_cancelled=lambda: False
    )

    assert runner.provider == "claude-code"
    assert seen["environment"] == {
        "PATH": "/usr/bin",
        "HOME": "/tmp/h",
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-x",
    }
    assert seen["display_name"] == "Claude Code"
    assert "Changed README." in report.summary
    minimax = ClaudeCodeRunner.minimax(api_key="mm", base_url="https://api.minimaxi.com/anthropic/")
    assert minimax.provider == "minimax-claude-code"
    with pytest.raises(ValueError):
        ClaudeCodeRunner.subscription(oauth_token="")


def _fake_codex(tmp_path: Path) -> Path:
    """A codex that records its arguments and environment and writes a valid report."""
    script = tmp_path / "fake_codex.py"
    script.write_text(
        "import json, os, sys\n"
        f"open({str(tmp_path / 'call.json')!r}, 'w').write(json.dumps("
        "{'argv': sys.argv[1:], 'codex_home': os.environ.get('CODEX_HOME')}))\n"
        "if sys.argv[1:3] == ['login', 'status']:\n"
        "    sys.exit(0 if os.environ.get('CODEX_HOME') else 1)\n"
        "sys.stdin.read()\n"
        "out = sys.argv[sys.argv.index('--output-last-message') + 1]\n"
        "open(out, 'w').write(json.dumps({'summary': 'done'}))\n"
    )
    executable = tmp_path / "codex"
    executable.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    executable.chmod(0o755)
    return executable


def test_codex_uses_its_login_home_and_the_container_as_its_sandbox(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.coding.runners.safe_agent_environment", lambda: {"PATH": "/usr/bin:/bin"}
    )
    home = tmp_path / "codex-home"
    runner = CodexCliRunner(
        executable=str(_fake_codex(tmp_path)), codex_home=home, own_sandbox=False
    )
    worktree = tmp_path / "w"
    worktree.mkdir()

    report = runner.run(
        worktree=worktree, request="fix it", timeout_seconds=30, is_cancelled=lambda: False
    )

    call = json.loads((tmp_path / "call.json").read_text())
    assert report.summary == "done"
    assert call["codex_home"] == str(home)
    assert "--dangerously-bypass-approvals-and-sandbox" in call["argv"]
    assert "--sandbox" not in call["argv"]
    assert runner.check_login() is None
    logged_out = CodexCliRunner(executable=str(_fake_codex(tmp_path)))
    assert logged_out.check_login() == "execution failed"


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ("Claude AI usage limit reached|1761400000", "quota_exhausted"),
        ("You've hit your 5-hour limit. Resets at 14:00", "quota_exhausted"),
        ("You've hit your usage limit. Upgrade to Pro or try again in 3 days", "quota_exhausted"),
        ("API Error: 401 Invalid bearer token", "authentication_failed"),
        (
            "Failed to authenticate. API Error: 401 OAuth access token is invalid.",
            "authentication_failed",
        ),
        ("Not logged in · Please run /login", "authentication_failed"),
        ("Rate limit reached for requests", "rate_limited"),
    ],
)
def test_subscription_limits_and_logins_are_recognised(message: str, category: str) -> None:
    assert failure_category(message) == category


def test_a_logged_out_agent_is_left_out_and_the_others_serve() -> None:
    from app.worker import drop_logged_out_runners

    class Runner:
        def __init__(self, provider: str, problem: str | None = None):
            self.provider = provider
            self.problem = problem

        def check_login(self):
            return self.problem

    class Kimi:
        provider = "kimi-code"

    agent = FallbackCodeAgentRunner(
        [Runner("claude-code", "authentication failed"), Runner("codex-cli"), Kimi()]
    )

    class Configured:
        def __init__(self):
            self.agent = agent

    class Executor:
        executors = {"a": Configured(), "b": Configured()}

    drop_logged_out_runners(Executor())  # type: ignore[arg-type]

    assert [r.provider for r in agent.runners] == ["codex-cli", "kimi-code"]
    assert agent.provider == "codex-cli->kimi-code"
    agent.runners = (Runner("claude-code", "authentication failed"),)
    with pytest.raises(ValueError, match="no coding agent can log in"):
        drop_logged_out_runners(Executor())  # type: ignore[arg-type]


def test_agents_without_a_login_are_skipped_at_startup(tmp_path: Path) -> None:
    from app.config import Settings
    from app.worker import _build_code_agent_runners

    settings = Settings(
        code_agent_providers="claude,codex,kimi",
        kimi_api_key="k",
        codex_home=tmp_path,  # no auth.json yet
    )
    assert [r.provider for r in _build_code_agent_runners(settings)] == ["kimi-code"]

    (tmp_path / "auth.json").write_text("{}")
    settings = Settings(
        code_agent_providers="claude,codex,kimi",
        kimi_api_key="k",
        codex_home=tmp_path,
        claude_code_oauth_token="t",
    )
    providers = [r.provider for r in _build_code_agent_runners(settings)]
    assert providers == ["claude-code", "codex-cli", "kimi-code"]
