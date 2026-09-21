from __future__ import annotations

from pathlib import Path

from app.config import Settings


def test_manager_model_settings_are_loaded_from_environment(monkeypatch) -> None:
    monkeypatch.delenv("ASSISTANT_GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-minimax-test")
    monkeypatch.setenv("ASSISTANT_MINIMAX_BASE_URL", "https://minimax.example/v1")
    monkeypatch.setenv("ASSISTANT_MINIMAX_MODEL", "MiniMax-M3")
    monkeypatch.setenv("ASSISTANT_MINIMAX_TIMEOUT_SECONDS", "25")
    monkeypatch.setenv("GEMINI_TTS_API_KEY", "gemini-tts-test")
    monkeypatch.setenv("ASSISTANT_GEMINI_TTS_BASE_URL", "https://gemini.example/v1beta")
    monkeypatch.setenv("ASSISTANT_GEMINI_TTS_MODEL", "gemini-tts-test-model")
    monkeypatch.setenv("ASSISTANT_GEMINI_TTS_VOICE", "Charon")
    monkeypatch.setenv("ASSISTANT_GEMINI_TTS_TIMEOUT_SECONDS", "21")
    monkeypatch.setenv("ASSISTANT_GEMINI_TTS_CACHE_ENTRIES", "64")
    monkeypatch.setenv("ASSISTANT_CODE_AGENT_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_REPOSITORIES_DIRECTORY", "/srv/repository-registry")
    monkeypatch.setenv("ASSISTANT_WORKFLOWS_DIRECTORY", "/srv/workflow-registry")
    monkeypatch.setenv("ASSISTANT_DEFAULT_REPOSITORY_ID", "analytics-agent-playground")
    monkeypatch.setenv("ASSISTANT_CODE_AGENT_PROVIDERS", "kimi,minimax-claude,codex")
    monkeypatch.setenv("ASSISTANT_CODE_REPOSITORY_PATH", "/srv/repositories/widget")
    monkeypatch.setenv("ASSISTANT_CODE_WORKTREE_ROOT", "/srv/worktrees")
    monkeypatch.setenv("ASSISTANT_CODE_AGENT_EXECUTABLE", "/usr/local/bin/codex")
    monkeypatch.setenv("ASSISTANT_CODE_AGENT_MODEL", "coding-model")
    monkeypatch.setenv("ASSISTANT_KIMI_CODE_EXECUTABLE", "/usr/local/bin/kimi")
    monkeypatch.setenv("ASSISTANT_KIMI_CODE_MODEL", "kimi-code-model")
    monkeypatch.setenv("ASSISTANT_CLAUDE_CODE_EXECUTABLE", "/usr/local/bin/claude")
    monkeypatch.setenv(
        "ASSISTANT_MINIMAX_ANTHROPIC_BASE_URL", "https://minimax.example/anthropic"
    )
    monkeypatch.setenv("ASSISTANT_MINIMAX_CODE_MODEL", "MiniMax-M3")
    monkeypatch.setenv("ASSISTANT_CODE_AGENT_RATE_LIMIT_COOLDOWN_SECONDS", "120")
    monkeypatch.setenv("ASSISTANT_CODE_AGENT_QUOTA_COOLDOWN_SECONDS", "7200")
    monkeypatch.setenv("ASSISTANT_CODE_AGENT_TIMEOUT_SECONDS", "900")
    monkeypatch.setenv("GITHUB_TOKEN", "github-test")
    monkeypatch.setenv("ASSISTANT_GITHUB_API_BASE_URL", "https://github.example/api/v3")
    monkeypatch.setenv("ASSISTANT_GITHUB_REPOSITORY", "acme/widget")
    monkeypatch.setenv("ASSISTANT_GITHUB_BASE_BRANCH", "develop")
    monkeypatch.setenv("ASSISTANT_GITHUB_REMOTE", "upstream")
    monkeypatch.setenv("ASSISTANT_GITHUB_DRAFT_PULL_REQUESTS", "false")
    monkeypatch.setenv("ASSISTANT_APPROVAL_TOKEN", "approval-secret")
    monkeypatch.setenv("ASSISTANT_CONVERSATION_MODEL_PROVIDER", "minimax")
    monkeypatch.setenv("ASSISTANT_CONVERSATION_MODEL_FALLBACK_PROVIDER", "kimi")
    monkeypatch.setenv("ASSISTANT_CONVERSATION_MODEL_BASE_URL", "https://chat.example/v1")
    monkeypatch.setenv("ASSISTANT_CONVERSATION_MODEL", "MiniMax-M2.7")
    monkeypatch.setenv("ASSISTANT_CONVERSATION_MODEL_TIMEOUT_SECONDS", "18.5")
    monkeypatch.setenv("ASSISTANT_MANAGER_MODEL_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_MANAGER_MODEL_PROVIDER", "minimax")
    monkeypatch.setenv("ASSISTANT_MANAGER_MODEL_FALLBACK_PROVIDER", "kimi")
    monkeypatch.setenv("ASSISTANT_MANAGER_MODEL_BASE_URL", "https://manager.example/v1")
    monkeypatch.setenv("ASSISTANT_MANAGER_MODEL", "kimi-manager-test")
    monkeypatch.setenv("ASSISTANT_MANAGER_MODEL_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("ASSISTANT_MANAGER_MODEL_MINIMUM_CONFIDENCE", "0.72")

    settings = Settings.from_env()

    assert settings.minimax_api_key == "sk-minimax-test"
    assert settings.minimax_base_url == "https://minimax.example/v1"
    assert settings.minimax_model == "MiniMax-M3"
    assert settings.minimax_timeout_seconds == 25
    assert settings.gemini_tts_api_key == "gemini-tts-test"
    assert settings.gemini_tts_base_url == "https://gemini.example/v1beta"
    assert settings.gemini_tts_model == "gemini-tts-test-model"
    assert settings.gemini_tts_voice == "Charon"
    assert settings.gemini_tts_timeout_seconds == 21
    assert settings.gemini_tts_cache_entries == 64
    assert settings.code_agent_enabled is True
    assert settings.repositories_directory == Path("/srv/repository-registry")
    assert settings.workflows_directory == Path("/srv/workflow-registry")
    assert settings.default_repository_id == "analytics-agent-playground"
    assert settings.code_agent_providers == "kimi,minimax-claude,codex"
    assert settings.code_repository_path == Path("/srv/repositories/widget")
    assert settings.code_worktree_root == Path("/srv/worktrees")
    assert settings.code_agent_executable == "/usr/local/bin/codex"
    assert settings.code_agent_model == "coding-model"
    assert settings.kimi_code_executable == "/usr/local/bin/kimi"
    assert settings.kimi_code_model == "kimi-code-model"
    assert settings.claude_code_executable == "/usr/local/bin/claude"
    assert settings.minimax_anthropic_base_url == "https://minimax.example/anthropic"
    assert settings.minimax_code_model == "MiniMax-M3"
    assert settings.code_agent_rate_limit_cooldown_seconds == 120
    assert settings.code_agent_quota_cooldown_seconds == 7200
    assert settings.code_agent_timeout_seconds == 900
    assert settings.github_token == "github-test"
    assert settings.github_api_base_url == "https://github.example/api/v3"
    assert settings.github_repository == "acme/widget"
    assert settings.github_base_branch == "develop"
    assert settings.github_remote == "upstream"
    assert settings.github_draft_pull_requests is False
    assert settings.approval_token == "approval-secret"
    assert settings.conversation_model_provider == "minimax"
    assert settings.conversation_model_fallback_provider == "kimi"
    assert settings.conversation_model_base_url == "https://chat.example/v1"
    assert settings.conversation_model_name == "MiniMax-M2.7"
    assert settings.conversation_model_timeout_seconds == 18.5
    assert settings.manager_model_enabled is True
    assert settings.manager_model_provider == "minimax"
    assert settings.manager_model_fallback_provider == "kimi"
    assert settings.manager_model_base_url == "https://manager.example/v1"
    assert settings.manager_model_name == "kimi-manager-test"
    assert settings.manager_model_timeout_seconds == 12.5
    assert settings.manager_model_minimum_confidence == 0.72
