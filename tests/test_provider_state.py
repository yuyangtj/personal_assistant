from datetime import UTC, datetime, timedelta

from app.persistence.database import Database
from app.providers import DatabaseProviderStateStore


def test_provider_cooldown_survives_store_recreation(database: Database) -> None:
    now = [datetime(2026, 9, 21, 12, 0, tzinfo=UTC)]
    first = DatabaseProviderStateStore(database, clock=lambda: now[0])

    failed = first.record_failure(
        "coding:kimi",
        category="quota_exhausted",
        cooldown_seconds=3600,
    )

    assert failed.consecutive_failures == 1
    assert failed.cooldown_until == now[0] + timedelta(hours=1)
    second = DatabaseProviderStateStore(database, clock=lambda: now[0])
    assert second.is_available("coding:kimi") is False
    assert second.get("coding:kimi").last_error_category == "quota_exhausted"

    now[0] += timedelta(hours=1, seconds=1)
    assert second.is_available("coding:kimi") is True
    recovered = second.record_success("coding:kimi")
    assert recovered.consecutive_failures == 0
    assert recovered.cooldown_until is None
    assert recovered.last_error_category is None
