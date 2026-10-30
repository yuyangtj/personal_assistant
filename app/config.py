from __future__ import annotations

import os
import types
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env(*names: str) -> Any:
    """An optional setting read from these variables (first set wins) instead of
    ``ASSISTANT_<NAME>``: provider keys keep their usual names."""
    return field(default=None, metadata={"env": names})


def expanded(default: Path | None) -> Any:
    """A path setting where ``~`` means the home directory."""
    return field(default=default, metadata={"expanduser": True})


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = "postgresql+psycopg://assistant:assistant@localhost:5432/assistant"
    auto_create_schema: bool = False
    worker_id: str = "worker-local"
    worker_poll_interval_seconds: float = 1.0
    #: A separate claim loop for chat replies, checked more often than background work.
    worker_reply_lane: bool = True
    worker_reply_poll_interval_seconds: float = 0.25
    worker_lease_seconds: int = 60
    worker_coding_only: bool = False
    #: How many coding agents the coding worker runs at once.
    coding_concurrency: int = 2
    fake_executor_delay_seconds: float = 0.1
    abilities_directory: Path = Path("abilities")
    repositories_directory: Path = Path("repositories")
    workflows_directory: Path = Path("workflows")
    validation_profiles_directory: Path = Path("validation-profiles")
    deployment_targets_directory: Path = Path("deployment-targets")
    spaces_directory: Path = Path("spaces")
    #: Built Live2D avatar files (scripts/build_live2d.sh), served at /ui/avatar-assets/.
    #: Unset: the console shows its own drawn avatar instead.
    avatar_assets_dir: Path | None = None
    deploy_spool_directory: Path | None = None
    brief_writeback_enabled: bool = False
    ntfy_topic_url: str | None = None
    #: Web search for the research agent: DeepSeek, Tavily or Brave, the first key set wins.
    deepseek_api_key: str | None = env("DEEPSEEK_API_KEY")
    tavily_api_key: str | None = env("TAVILY_API_KEY")
    #: The mcp-tools gateway (Willys, Lidl); the grocery agent is installed when it is set.
    mcp_gateway_url: str | None = None
    mcp_gateway_token: str | None = None
    toolsets_directory: Path = Path("toolsets")
    brave_search_api_key: str | None = env("BRAVE_SEARCH_API_KEY")
    ntfy_token: str | None = None
    public_url: str | None = None
    default_timezone: str = "UTC"
    scheduler_interval_seconds: float = 20.0
    brief_writeback_idle_seconds: int = 600
    default_repository_id: str | None = None
    kimi_api_key: str | None = env("ASSISTANT_KIMI_API_KEY", "KIMI_API_KEY")
    kimi_base_url: str = "https://api.kimi.com/coding/v1"
    kimi_model: str = "kimi-for-coding-highspeed"
    kimi_timeout_seconds: float = 30.0
    minimax_api_key: str | None = env("ASSISTANT_MINIMAX_API_KEY", "MINIMAX_API_KEY")
    minimax_base_url: str = "https://api.minimax.chat/v1"
    minimax_model: str = "MiniMax-M3"
    minimax_timeout_seconds: float = 30.0
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    #: Off for chat: DeepSeek thinks by default, which costs seconds per reply.
    deepseek_thinking: bool = False
    deepseek_timeout_seconds: float = 30.0
    #: Gemini for chat (OpenAI-compatible API); the voice's key is used when unset.
    gemini_api_key: str | None = env(
        "GEMINI_API_KEY", "ASSISTANT_GEMINI_TTS_API_KEY", "GEMINI_TTS_API_KEY"
    )
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    gemini_model: str = "gemini-3.8-flash"
    gemini_reasoning_effort: str = "low"
    gemini_tts_api_key: str | None = env("ASSISTANT_GEMINI_TTS_API_KEY", "GEMINI_TTS_API_KEY")
    gemini_tts_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_tts_model: str = "gemini-3.1-flash-tts-preview"
    gemini_tts_voice: str = "Achird"
    gemini_tts_timeout_seconds: float = 30.0
    gemini_tts_cache_entries: int = 128
    code_agent_enabled: bool = False
    #: In order; a provider that is rate-limited or not logged in hands over to the next.
    code_agent_providers: str = "claude,codex,kimi,minimax-claude"
    code_repository_path: Path | None = expanded(None)
    code_worktree_root: Path = expanded(Path("/tmp/personal-assistant-worktrees"))
    code_agent_executable: str = "codex"
    code_agent_model: str | None = None
    kimi_code_executable: str = "kimi"
    kimi_code_model: str | None = None
    #: "acp" drives Kimi Code as a live session (progress, clean cancel); "prompt" is the
    #: one-shot fallback.
    kimi_code_protocol: str = "acp"
    claude_code_executable: str = "claude"
    #: From ``claude setup-token``: Claude Code on the user's Claude subscription.
    claude_code_oauth_token: str | None = env("CLAUDE_CODE_OAUTH_TOKEN")
    #: Codex's home, holding the ChatGPT login (auth.json); must be writable and kept.
    codex_home: Path | None = env("CODEX_HOME")
    #: False in the coding container, where Codex's own sandbox can't start.
    codex_own_sandbox: bool = True
    minimax_anthropic_base_url: str = "https://api.minimaxi.com/anthropic"
    minimax_code_model: str | None = None
    code_agent_rate_limit_cooldown_seconds: int = 300
    code_agent_quota_cooldown_seconds: int = 3600
    code_agent_timeout_seconds: int = 1800
    code_agent_preflight_enabled: bool = True
    coding_spec_enabled: bool = True
    coding_review_enabled: bool = True
    coding_planner_timeout_seconds: float = 90.0
    github_token: str | None = env("ASSISTANT_GITHUB_TOKEN", "GITHUB_TOKEN")
    github_api_base_url: str = "https://api.github.com"
    github_repository: str | None = None
    github_base_branch: str = "main"
    github_remote: str = "origin"
    github_draft_pull_requests: bool = True
    approval_token: str | None = None
    conversation_model_provider: str = "minimax"
    conversation_model_fallback_provider: str | None = "auto"
    conversation_model_base_url: str | None = None
    conversation_model_name: str | None = env("ASSISTANT_CONVERSATION_MODEL")
    conversation_model_timeout_seconds: float | None = None
    slack_signing_secret: str | None = None
    slack_bot_token: str | None = None

    @property
    def web_search_available(self) -> bool:
        return bool(self.deepseek_api_key or self.tavily_api_key or self.brave_search_api_key)

    @classmethod
    def env_names(cls) -> dict[str, tuple[str, ...]]:
        """Each setting's environment variables: ``ASSISTANT_<NAME>`` unless a field names
        its own (provider keys keep their usual names); with several, the first set wins."""
        return {
            item.name: item.metadata.get("env", (f"ASSISTANT_{item.name.upper()}",))
            for item in fields(cls)
        }

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Settings from the environment. Unset keeps the default; for an optional setting
        an empty value means None; anything else is read as the setting's type."""
        environ = os.environ if environ is None else environ
        hints = get_type_hints(cls)
        names = cls.env_names()
        values: dict[str, object] = {}
        for item in fields(cls):
            candidates = names[item.name]
            raw = (
                environ.get(candidates[0])
                if len(candidates) == 1
                else next((environ[name] for name in candidates if environ.get(name)), None)
            )
            if raw is None:
                continue
            kind, optional = _kind(hints[item.name])
            if optional and raw == "":
                values[item.name] = None
            else:
                values[item.name] = _parse(raw, kind, expand=item.metadata.get("expanduser"))
        settings = cls(**values)
        return replace(
            settings,
            coding_concurrency=max(1, settings.coding_concurrency),
            kimi_code_protocol=(settings.kimi_code_protocol or cls().kimi_code_protocol).lower(),
        )


def _kind(hint: object) -> tuple[type, bool]:
    """The setting's type, and whether it may be None."""
    if get_origin(hint) in (Union, types.UnionType):
        options = [option for option in get_args(hint) if option is not type(None)]
        return options[0], True
    return hint, False  # type: ignore[return-value]


def _parse(raw: str, kind: type, *, expand: bool | None) -> object:
    if kind is bool:
        return _as_bool(raw)
    if kind is Path:
        return Path(raw).expanduser() if expand else Path(raw)
    return kind(raw)
