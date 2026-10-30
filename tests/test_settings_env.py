from __future__ import annotations

from pathlib import Path

from app.config import Settings

#: Every variable the settings read. A setting renamed or dropped by accident would leave
#: the server's .env.remote silently ignored; change this list only on purpose.
ENV_NAMES = [
    "ASSISTANT_ABILITIES_DIRECTORY",
    "ASSISTANT_APPROVAL_TOKEN",
    "ASSISTANT_AUTO_CREATE_SCHEMA",
    "ASSISTANT_AVATAR_ASSETS_DIR",
    "ASSISTANT_BRIEF_WRITEBACK_ENABLED",
    "ASSISTANT_BRIEF_WRITEBACK_IDLE_SECONDS",
    "ASSISTANT_CLAUDE_CODE_EXECUTABLE",
    "ASSISTANT_CODEX_OWN_SANDBOX",
    "ASSISTANT_CODE_AGENT_ENABLED",
    "ASSISTANT_CODE_AGENT_EXECUTABLE",
    "ASSISTANT_CODE_AGENT_MODEL",
    "ASSISTANT_CODE_AGENT_PREFLIGHT_ENABLED",
    "ASSISTANT_CODE_AGENT_PROVIDERS",
    "ASSISTANT_CODE_AGENT_QUOTA_COOLDOWN_SECONDS",
    "ASSISTANT_CODE_AGENT_RATE_LIMIT_COOLDOWN_SECONDS",
    "ASSISTANT_CODE_AGENT_TIMEOUT_SECONDS",
    "ASSISTANT_CODE_REPOSITORY_PATH",
    "ASSISTANT_CODE_WORKTREE_ROOT",
    "ASSISTANT_CODING_CONCURRENCY",
    "ASSISTANT_CODING_PLANNER_TIMEOUT_SECONDS",
    "ASSISTANT_CODING_REVIEW_ENABLED",
    "ASSISTANT_CODING_SPEC_ENABLED",
    "ASSISTANT_CONVERSATION_MODEL",
    "ASSISTANT_CONVERSATION_MODEL_BASE_URL",
    "ASSISTANT_CONVERSATION_MODEL_FALLBACK_PROVIDER",
    "ASSISTANT_CONVERSATION_MODEL_PROVIDER",
    "ASSISTANT_CONVERSATION_MODEL_TIMEOUT_SECONDS",
    "ASSISTANT_DATABASE_URL",
    "ASSISTANT_DEEPSEEK_BASE_URL",
    "ASSISTANT_DEEPSEEK_MODEL",
    "ASSISTANT_DEEPSEEK_THINKING",
    "ASSISTANT_DEEPSEEK_TIMEOUT_SECONDS",
    "ASSISTANT_DEFAULT_REPOSITORY_ID",
    "ASSISTANT_DEFAULT_TIMEZONE",
    "ASSISTANT_DEPLOYMENT_TARGETS_DIRECTORY",
    "ASSISTANT_DEPLOY_SPOOL_DIRECTORY",
    "ASSISTANT_FAKE_EXECUTOR_DELAY_SECONDS",
    "ASSISTANT_GEMINI_BASE_URL",
    "ASSISTANT_GEMINI_MODEL",
    "ASSISTANT_GEMINI_REASONING_EFFORT",
    "ASSISTANT_GEMINI_TTS_API_KEY",
    "ASSISTANT_GEMINI_TTS_BASE_URL",
    "ASSISTANT_GEMINI_TTS_CACHE_ENTRIES",
    "ASSISTANT_GEMINI_TTS_MODEL",
    "ASSISTANT_GEMINI_TTS_TIMEOUT_SECONDS",
    "ASSISTANT_GEMINI_TTS_VOICE",
    "ASSISTANT_GITHUB_API_BASE_URL",
    "ASSISTANT_GITHUB_BASE_BRANCH",
    "ASSISTANT_GITHUB_DRAFT_PULL_REQUESTS",
    "ASSISTANT_GITHUB_REMOTE",
    "ASSISTANT_GITHUB_REPOSITORY",
    "ASSISTANT_GITHUB_TOKEN",
    "ASSISTANT_KIMI_API_KEY",
    "ASSISTANT_KIMI_BASE_URL",
    "ASSISTANT_KIMI_CODE_EXECUTABLE",
    "ASSISTANT_KIMI_CODE_MODEL",
    "ASSISTANT_KIMI_CODE_PROTOCOL",
    "ASSISTANT_KIMI_MODEL",
    "ASSISTANT_KIMI_TIMEOUT_SECONDS",
    "ASSISTANT_MCP_GATEWAY_TOKEN",
    "ASSISTANT_MCP_GATEWAY_URL",
    "ASSISTANT_MINIMAX_ANTHROPIC_BASE_URL",
    "ASSISTANT_MINIMAX_API_KEY",
    "ASSISTANT_MINIMAX_BASE_URL",
    "ASSISTANT_MINIMAX_CODE_MODEL",
    "ASSISTANT_MINIMAX_MODEL",
    "ASSISTANT_MINIMAX_TIMEOUT_SECONDS",
    "ASSISTANT_NTFY_TOKEN",
    "ASSISTANT_NTFY_TOPIC_URL",
    "ASSISTANT_PUBLIC_URL",
    "ASSISTANT_REPOSITORIES_DIRECTORY",
    "ASSISTANT_SCHEDULER_INTERVAL_SECONDS",
    "ASSISTANT_SLACK_BOT_TOKEN",
    "ASSISTANT_SLACK_SIGNING_SECRET",
    "ASSISTANT_SPACES_DIRECTORY",
    "ASSISTANT_TOOLSETS_DIRECTORY",
    "ASSISTANT_VALIDATION_PROFILES_DIRECTORY",
    "ASSISTANT_WORKER_CODING_ONLY",
    "ASSISTANT_WORKER_ID",
    "ASSISTANT_WORKER_LEASE_SECONDS",
    "ASSISTANT_WORKER_POLL_INTERVAL_SECONDS",
    "ASSISTANT_WORKER_REPLY_LANE",
    "ASSISTANT_WORKER_REPLY_POLL_INTERVAL_SECONDS",
    "ASSISTANT_WORKFLOWS_DIRECTORY",
    "BRAVE_SEARCH_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CODEX_HOME",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "GEMINI_TTS_API_KEY",
    "GITHUB_TOKEN",
    "KIMI_API_KEY",
    "MINIMAX_API_KEY",
    "TAVILY_API_KEY",
]


def test_the_settings_read_exactly_these_environment_variables() -> None:
    read = sorted({name for names in Settings.env_names().values() for name in names})
    assert read == ENV_NAMES


def test_unset_keeps_the_default_and_values_are_read_as_their_type() -> None:
    settings = Settings.from_env(
        {
            "ASSISTANT_WORKER_CODING_ONLY": "yes",
            "ASSISTANT_WORKER_LEASE_SECONDS": "90",
            "ASSISTANT_SCHEDULER_INTERVAL_SECONDS": "2.5",
            "ASSISTANT_TOOLSETS_DIRECTORY": "/srv/toolsets",
            "ASSISTANT_CONVERSATION_MODEL_TIMEOUT_SECONDS": "12",
        }
    )
    assert settings.worker_coding_only is True
    assert settings.worker_lease_seconds == 90
    assert settings.scheduler_interval_seconds == 2.5
    assert settings.toolsets_directory == Path("/srv/toolsets")
    assert settings.conversation_model_timeout_seconds == 12.0
    assert settings.worker_id == Settings().worker_id


def test_an_empty_optional_setting_means_none_and_aliases_take_the_first_set() -> None:
    settings = Settings.from_env(
        {
            "ASSISTANT_CONVERSATION_MODEL_FALLBACK_PROVIDER": "",
            "ASSISTANT_KIMI_API_KEY": "",
            "KIMI_API_KEY": "from-the-usual-name",
            "GITHUB_TOKEN": "gh",
            "GEMINI_TTS_API_KEY": "voice",
            "ASSISTANT_CONVERSATION_MODEL": "k3",
        }
    )
    assert settings.conversation_model_fallback_provider is None
    assert Settings.from_env({}).conversation_model_fallback_provider == "auto"
    assert settings.kimi_api_key == "from-the-usual-name"
    assert settings.github_token == "gh"
    assert (settings.gemini_api_key, settings.gemini_tts_api_key) == ("voice", "voice")
    assert settings.conversation_model_name == "k3"


def test_the_few_settings_with_rules_of_their_own() -> None:
    settings = Settings.from_env(
        {
            "ASSISTANT_CODING_CONCURRENCY": "0",
            "ASSISTANT_KIMI_CODE_PROTOCOL": "Prompt",
            "ASSISTANT_CODE_WORKTREE_ROOT": "~/worktrees",
            "ASSISTANT_AVATAR_ASSETS_DIR": "~/avatar",
        }
    )
    assert settings.coding_concurrency == 1
    assert settings.kimi_code_protocol == "prompt"
    assert settings.code_worktree_root == Path.home() / "worktrees"
    assert settings.avatar_assets_dir == Path("~/avatar")  # only the code paths expand ~
    assert Settings.from_env({"ASSISTANT_KIMI_CODE_PROTOCOL": ""}).kimi_code_protocol == "acp"
