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
