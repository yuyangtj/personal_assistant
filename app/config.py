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
    kimi_api_key: str | None = None
    kimi_base_url: str = "https://api.kimi.com/coding/v1"
    kimi_model: str = "kimi-for-coding-highspeed"
    kimi_timeout_seconds: float = 30.0
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
            kimi_api_key=os.getenv("ASSISTANT_KIMI_API_KEY") or os.getenv("KIMI_API_KEY") or None,
            kimi_base_url=os.getenv("ASSISTANT_KIMI_BASE_URL", defaults.kimi_base_url),
            kimi_model=os.getenv("ASSISTANT_KIMI_MODEL", defaults.kimi_model),
            kimi_timeout_seconds=float(
                os.getenv("ASSISTANT_KIMI_TIMEOUT_SECONDS", str(defaults.kimi_timeout_seconds))
            ),
            slack_signing_secret=os.getenv("ASSISTANT_SLACK_SIGNING_SECRET") or None,
            slack_bot_token=os.getenv("ASSISTANT_SLACK_BOT_TOKEN") or None,
        )
