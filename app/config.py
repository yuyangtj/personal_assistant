from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = "postgresql+psycopg://assistant:assistant@localhost:5432/assistant"
    auto_create_schema: bool = False
    worker_id: str = "worker-local"
    worker_poll_interval_seconds: float = 1.0
    worker_lease_seconds: int = 60
    fake_executor_delay_seconds: float = 0.1
    capabilities_directory: Path = Path("capabilities")
    repositories_directory: Path = Path("repositories")
    workflows_directory: Path = Path("workflows")
    coding_runners_directory: Path = Path("coding-runners")
    validation_profiles_directory: Path = Path("validation-profiles")
    deployment_targets_directory: Path = Path("deployment-targets")
    default_repository_id: str | None = None
    kimi_api_key: str | None = None
    kimi_base_url: str = "https://api.kimi.com/coding/v1"
    kimi_model: str = "kimi-for-coding-highspeed"
    kimi_timeout_seconds: float = 30.0
    minimax_api_key: str | None = None
    minimax_base_url: str = "https://api.minimax.chat/v1"
    minimax_model: str = "MiniMax-M2.7-highspeed"
    minimax_timeout_seconds: float = 30.0
    gemini_tts_api_key: str | None = None
    gemini_tts_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_tts_model: str = "gemini-3.1-flash-tts-preview"
    gemini_tts_voice: str = "Achird"
    gemini_tts_timeout_seconds: float = 30.0
    gemini_tts_cache_entries: int = 128
    code_agent_enabled: bool = False
    code_agent_providers: str = "kimi,minimax-claude,codex"
    code_repository_path: Path | None = None
    code_worktree_root: Path = Path("/tmp/personal-assistant-worktrees")
    code_agent_executable: str = "codex"
    code_agent_model: str | None = None
    kimi_code_executable: str = "kimi"
    kimi_code_model: str | None = None
    claude_code_executable: str = "claude"
    minimax_anthropic_base_url: str = "https://api.minimaxi.com/anthropic"
    minimax_code_model: str | None = None
    code_agent_rate_limit_cooldown_seconds: int = 300
    code_agent_quota_cooldown_seconds: int = 3600
    code_agent_timeout_seconds: int = 1800
    github_token: str | None = None
    github_api_base_url: str = "https://api.github.com"
    github_repository: str | None = None
    github_base_branch: str = "main"
    github_remote: str = "origin"
    github_draft_pull_requests: bool = True
    approval_token: str | None = None
    conversation_model_provider: str = "minimax"
    conversation_model_fallback_provider: str | None = "auto"
    conversation_model_base_url: str | None = None
    conversation_model_name: str | None = None
    conversation_model_timeout_seconds: float | None = None
    manager_model_enabled: bool = False
    manager_model_provider: str = "kimi"
    manager_model_fallback_provider: str | None = "auto"
    manager_model_base_url: str | None = None
    manager_model_name: str | None = None
    manager_model_timeout_seconds: float = 30.0
    manager_model_minimum_confidence: float = 0.5
    slack_signing_secret: str | None = None
    slack_bot_token: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        defaults = cls()
        return cls(
            database_url=os.getenv(
                "ASSISTANT_DATABASE_URL",
                defaults.database_url,
            ),
            auto_create_schema=_as_bool(
                os.getenv("ASSISTANT_AUTO_CREATE_SCHEMA", str(defaults.auto_create_schema))
            ),
            worker_id=os.getenv("ASSISTANT_WORKER_ID", defaults.worker_id),
            worker_poll_interval_seconds=float(
                os.getenv(
                    "ASSISTANT_WORKER_POLL_INTERVAL_SECONDS",
                    str(defaults.worker_poll_interval_seconds),
                )
            ),
            worker_lease_seconds=int(
                os.getenv("ASSISTANT_WORKER_LEASE_SECONDS", str(defaults.worker_lease_seconds))
            ),
            fake_executor_delay_seconds=float(
                os.getenv(
                    "ASSISTANT_FAKE_EXECUTOR_DELAY_SECONDS",
                    str(defaults.fake_executor_delay_seconds),
                )
            ),
            capabilities_directory=Path(
                os.getenv(
                    "ASSISTANT_CAPABILITIES_DIRECTORY",
                    str(defaults.capabilities_directory),
                )
            ),
            repositories_directory=Path(
                os.getenv(
                    "ASSISTANT_REPOSITORIES_DIRECTORY",
                    str(defaults.repositories_directory),
                )
            ),
            workflows_directory=Path(
                os.getenv(
                    "ASSISTANT_WORKFLOWS_DIRECTORY",
                    str(defaults.workflows_directory),
                )
            ),
            coding_runners_directory=Path(
                os.getenv(
                    "ASSISTANT_CODING_RUNNERS_DIRECTORY",
                    str(defaults.coding_runners_directory),
                )
            ),
            validation_profiles_directory=Path(
                os.getenv(
                    "ASSISTANT_VALIDATION_PROFILES_DIRECTORY",
                    str(defaults.validation_profiles_directory),
                )
            ),
            deployment_targets_directory=Path(
                os.getenv(
                    "ASSISTANT_DEPLOYMENT_TARGETS_DIRECTORY",
                    str(defaults.deployment_targets_directory),
                )
            ),
            default_repository_id=os.getenv("ASSISTANT_DEFAULT_REPOSITORY_ID") or None,
            kimi_api_key=os.getenv("ASSISTANT_KIMI_API_KEY") or os.getenv("KIMI_API_KEY") or None,
            kimi_base_url=os.getenv("ASSISTANT_KIMI_BASE_URL", defaults.kimi_base_url),
            kimi_model=os.getenv("ASSISTANT_KIMI_MODEL", defaults.kimi_model),
            kimi_timeout_seconds=float(
                os.getenv("ASSISTANT_KIMI_TIMEOUT_SECONDS", str(defaults.kimi_timeout_seconds))
            ),
            minimax_api_key=os.getenv("ASSISTANT_MINIMAX_API_KEY")
            or os.getenv("MINIMAX_API_KEY")
            or None,
            minimax_base_url=os.getenv(
                "ASSISTANT_MINIMAX_BASE_URL",
                defaults.minimax_base_url,
            ),
            minimax_model=os.getenv(
                "ASSISTANT_MINIMAX_MODEL",
                defaults.minimax_model,
            ),
            minimax_timeout_seconds=float(
                os.getenv(
                    "ASSISTANT_MINIMAX_TIMEOUT_SECONDS",
                    str(defaults.minimax_timeout_seconds),
                )
            ),
            gemini_tts_api_key=os.getenv("ASSISTANT_GEMINI_TTS_API_KEY")
            or os.getenv("GEMINI_TTS_API_KEY")
            or None,
            gemini_tts_base_url=os.getenv(
                "ASSISTANT_GEMINI_TTS_BASE_URL",
                defaults.gemini_tts_base_url,
            ),
            gemini_tts_model=os.getenv(
                "ASSISTANT_GEMINI_TTS_MODEL",
                defaults.gemini_tts_model,
            ),
            gemini_tts_voice=os.getenv(
                "ASSISTANT_GEMINI_TTS_VOICE",
                defaults.gemini_tts_voice,
            ),
            gemini_tts_timeout_seconds=float(
                os.getenv(
                    "ASSISTANT_GEMINI_TTS_TIMEOUT_SECONDS",
                    str(defaults.gemini_tts_timeout_seconds),
                )
            ),
            gemini_tts_cache_entries=int(
                os.getenv(
                    "ASSISTANT_GEMINI_TTS_CACHE_ENTRIES",
                    str(defaults.gemini_tts_cache_entries),
                )
            ),
            code_agent_enabled=_as_bool(
                os.getenv("ASSISTANT_CODE_AGENT_ENABLED", str(defaults.code_agent_enabled))
            ),
            code_agent_providers=os.getenv(
                "ASSISTANT_CODE_AGENT_PROVIDERS", defaults.code_agent_providers
            ),
            code_repository_path=(
                Path(value).expanduser()
                if (value := os.getenv("ASSISTANT_CODE_REPOSITORY_PATH"))
                else None
            ),
            code_worktree_root=Path(
                os.getenv("ASSISTANT_CODE_WORKTREE_ROOT", str(defaults.code_worktree_root))
            ).expanduser(),
            code_agent_executable=os.getenv(
                "ASSISTANT_CODE_AGENT_EXECUTABLE", defaults.code_agent_executable
            ),
            code_agent_model=os.getenv("ASSISTANT_CODE_AGENT_MODEL") or None,
            kimi_code_executable=os.getenv(
                "ASSISTANT_KIMI_CODE_EXECUTABLE", defaults.kimi_code_executable
            ),
            kimi_code_model=os.getenv("ASSISTANT_KIMI_CODE_MODEL") or None,
            claude_code_executable=os.getenv(
                "ASSISTANT_CLAUDE_CODE_EXECUTABLE", defaults.claude_code_executable
            ),
            minimax_anthropic_base_url=os.getenv(
                "ASSISTANT_MINIMAX_ANTHROPIC_BASE_URL",
                defaults.minimax_anthropic_base_url,
            ),
            minimax_code_model=os.getenv("ASSISTANT_MINIMAX_CODE_MODEL") or None,
            code_agent_rate_limit_cooldown_seconds=int(
                os.getenv(
                    "ASSISTANT_CODE_AGENT_RATE_LIMIT_COOLDOWN_SECONDS",
                    str(defaults.code_agent_rate_limit_cooldown_seconds),
                )
            ),
            code_agent_quota_cooldown_seconds=int(
                os.getenv(
                    "ASSISTANT_CODE_AGENT_QUOTA_COOLDOWN_SECONDS",
                    str(defaults.code_agent_quota_cooldown_seconds),
                )
            ),
            code_agent_timeout_seconds=int(
                os.getenv(
                    "ASSISTANT_CODE_AGENT_TIMEOUT_SECONDS",
                    str(defaults.code_agent_timeout_seconds),
                )
            ),
            github_token=os.getenv("ASSISTANT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN") or None,
            github_api_base_url=os.getenv(
                "ASSISTANT_GITHUB_API_BASE_URL", defaults.github_api_base_url
            ),
            github_repository=os.getenv("ASSISTANT_GITHUB_REPOSITORY") or None,
            github_base_branch=os.getenv(
                "ASSISTANT_GITHUB_BASE_BRANCH", defaults.github_base_branch
            ),
            github_remote=os.getenv("ASSISTANT_GITHUB_REMOTE", defaults.github_remote),
            github_draft_pull_requests=_as_bool(
                os.getenv(
                    "ASSISTANT_GITHUB_DRAFT_PULL_REQUESTS",
                    str(defaults.github_draft_pull_requests),
                )
            ),
            approval_token=os.getenv("ASSISTANT_APPROVAL_TOKEN") or None,
            conversation_model_provider=os.getenv(
                "ASSISTANT_CONVERSATION_MODEL_PROVIDER",
                defaults.conversation_model_provider,
            ),
            conversation_model_fallback_provider=os.getenv(
                "ASSISTANT_CONVERSATION_MODEL_FALLBACK_PROVIDER",
                defaults.conversation_model_fallback_provider,
            )
            or None,
            conversation_model_base_url=os.getenv("ASSISTANT_CONVERSATION_MODEL_BASE_URL") or None,
            conversation_model_name=os.getenv("ASSISTANT_CONVERSATION_MODEL") or None,
            conversation_model_timeout_seconds=(
                float(value)
                if (value := os.getenv("ASSISTANT_CONVERSATION_MODEL_TIMEOUT_SECONDS"))
                else None
            ),
            manager_model_enabled=_as_bool(
                os.getenv(
                    "ASSISTANT_MANAGER_MODEL_ENABLED",
                    str(defaults.manager_model_enabled),
                )
            ),
            manager_model_provider=os.getenv(
                "ASSISTANT_MANAGER_MODEL_PROVIDER",
                defaults.manager_model_provider,
            ),
            manager_model_fallback_provider=os.getenv(
                "ASSISTANT_MANAGER_MODEL_FALLBACK_PROVIDER",
                defaults.manager_model_fallback_provider,
            )
            or None,
            manager_model_base_url=os.getenv("ASSISTANT_MANAGER_MODEL_BASE_URL") or None,
            manager_model_name=os.getenv("ASSISTANT_MANAGER_MODEL") or None,
            manager_model_timeout_seconds=float(
                os.getenv(
                    "ASSISTANT_MANAGER_MODEL_TIMEOUT_SECONDS",
                    str(defaults.manager_model_timeout_seconds),
                )
            ),
            manager_model_minimum_confidence=float(
                os.getenv(
                    "ASSISTANT_MANAGER_MODEL_MINIMUM_CONFIDENCE",
                    str(defaults.manager_model_minimum_confidence),
                )
            ),
            slack_signing_secret=os.getenv("ASSISTANT_SLACK_SIGNING_SECRET") or None,
            slack_bot_token=os.getenv("ASSISTANT_SLACK_BOT_TOKEN") or None,
        )
