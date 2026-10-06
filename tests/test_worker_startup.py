from __future__ import annotations

import pytest


def test_the_worker_starts_with_a_gateway_and_no_search_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The live configuration that crash-looped after #57: grocery agent on, research off."""
    from app import worker

    monkeypatch.setenv("ASSISTANT_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/w.sqlite3")
    monkeypatch.setenv("ASSISTANT_AUTO_CREATE_SCHEMA", "true")
    monkeypatch.setenv("ASSISTANT_MCP_GATEWAY_URL", "http://mcp-tools:8790")
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    started: list[set[str]] = []
    monkeypatch.setattr(
        worker.TaskWorker,
        "run_forever",
        lambda self: started.append(set(self.executors)),
    )

    worker.main()

    assert "grocery-agent" in started[0]
    assert "tool-agent" not in started[0]


def test_the_coding_worker_starts_without_the_shopping_agents_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The coding worker crash-looped after #71: its image has no toolsets/ directory."""
    from app import worker

    monkeypatch.setenv("ASSISTANT_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/w.sqlite3")
    monkeypatch.setenv("ASSISTANT_AUTO_CREATE_SCHEMA", "true")
    monkeypatch.setenv("ASSISTANT_WORKER_CODING_ONLY", "true")
    monkeypatch.setenv("ASSISTANT_TOOLSETS_DIRECTORY", str(tmp_path / "missing"))
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    started: list[set[str]] = []
    monkeypatch.setattr(
        worker.TaskWorker,
        "run_forever",
        lambda self: started.append(set(self.executors)),
    )

    worker.main()

    assert started and "grocery-agent" not in started[0]
