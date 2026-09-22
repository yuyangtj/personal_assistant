from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select

from app.persistence.database import Database
from app.persistence.models import ProviderRuntimeStateModel


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ProviderRuntimeState:
    provider_key: str
    cooldown_until: datetime | None = None
    last_error_category: str | None = None
    consecutive_failures: int = 0
    total_successes: int = 0
    total_failures: int = 0
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    updated_at: datetime | None = None

    def is_available(self, now: datetime) -> bool:
        return self.cooldown_until is None or self.cooldown_until <= now


class ProviderStateStore(Protocol):
    def get(self, provider_key: str) -> ProviderRuntimeState: ...

    def is_available(self, provider_key: str) -> bool: ...

    def list(self) -> list[ProviderRuntimeState]: ...

    def record_failure(
        self,
        provider_key: str,
        *,
        category: str,
        cooldown_seconds: int,
    ) -> ProviderRuntimeState: ...

    def record_success(self, provider_key: str) -> ProviderRuntimeState: ...


class InMemoryProviderStateStore:
    def __init__(self, *, clock: Callable[[], datetime] = _utc_now):
        self._clock = clock
        self._states: dict[str, ProviderRuntimeState] = {}

    def get(self, provider_key: str) -> ProviderRuntimeState:
        return self._states.get(provider_key, ProviderRuntimeState(provider_key=provider_key))

    def list(self) -> list[ProviderRuntimeState]:
        return sorted(self._states.values(), key=lambda state: state.provider_key)

    def is_available(self, provider_key: str) -> bool:
        return self.get(provider_key).is_available(self._clock())

    def record_failure(
        self,
        provider_key: str,
        *,
        category: str,
        cooldown_seconds: int,
    ) -> ProviderRuntimeState:
        now = self._clock()
        previous = self.get(provider_key)
        state = ProviderRuntimeState(
            provider_key=provider_key,
            cooldown_until=(
                now + timedelta(seconds=cooldown_seconds) if cooldown_seconds else None
            ),
            last_error_category=category,
            consecutive_failures=previous.consecutive_failures + 1,
            total_successes=previous.total_successes,
            total_failures=previous.total_failures + 1,
            last_success_at=previous.last_success_at,
            last_failure_at=now,
            updated_at=now,
        )
        self._states[provider_key] = state
        return state

    def record_success(self, provider_key: str) -> ProviderRuntimeState:
        now = self._clock()
        state = ProviderRuntimeState(
            provider_key=provider_key,
            cooldown_until=None,
            last_error_category=None,
            consecutive_failures=0,
            total_successes=self.get(provider_key).total_successes + 1,
            total_failures=self.get(provider_key).total_failures,
            last_success_at=now,
            last_failure_at=self.get(provider_key).last_failure_at,
            updated_at=now,
        )
        self._states[provider_key] = state
        return state


class DatabaseProviderStateStore:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = _utc_now):
        self.database = database
        self._clock = clock

    def get(self, provider_key: str) -> ProviderRuntimeState:
        with self.database.session() as session:
            model = session.get(ProviderRuntimeStateModel, provider_key)
            return self._from_model(model) if model else ProviderRuntimeState(provider_key)

    def list(self) -> list[ProviderRuntimeState]:
        with self.database.session() as session:
            models = session.scalars(
                select(ProviderRuntimeStateModel).order_by(ProviderRuntimeStateModel.provider_key)
            )
            return [self._from_model(model) for model in models]

    def is_available(self, provider_key: str) -> bool:
        return self.get(provider_key).is_available(self._clock())

    def record_failure(
        self,
        provider_key: str,
        *,
        category: str,
        cooldown_seconds: int,
    ) -> ProviderRuntimeState:
        now = self._clock()
        with self.database.session() as session, session.begin():
            model = session.get(ProviderRuntimeStateModel, provider_key, with_for_update=True)
            if model is None:
                model = ProviderRuntimeStateModel(
                    provider_key=provider_key,
                    consecutive_failures=0,
                    total_successes=0,
                    total_failures=0,
                )
                session.add(model)
            model.cooldown_until = (
                now + timedelta(seconds=cooldown_seconds) if cooldown_seconds else None
            )
            model.last_error_category = category
            model.consecutive_failures += 1
            model.total_failures = (model.total_failures or 0) + 1
            model.last_failure_at = now
            model.updated_at = now
            session.flush()
            return self._from_model(model)

    def record_success(self, provider_key: str) -> ProviderRuntimeState:
        now = self._clock()
        with self.database.session() as session, session.begin():
            model = session.get(ProviderRuntimeStateModel, provider_key, with_for_update=True)
            if model is None:
                model = ProviderRuntimeStateModel(
                    provider_key=provider_key,
                    consecutive_failures=0,
                    total_successes=0,
                    total_failures=0,
                )
                session.add(model)
            model.cooldown_until = None
            model.last_error_category = None
            model.consecutive_failures = 0
            model.total_successes = (model.total_successes or 0) + 1
            model.last_success_at = now
            model.updated_at = now
            session.flush()
            return self._from_model(model)

    @staticmethod
    def _from_model(model: ProviderRuntimeStateModel) -> ProviderRuntimeState:
        def as_utc(value: datetime | None) -> datetime | None:
            if value is None or value.tzinfo is not None:
                return value
            return value.replace(tzinfo=UTC)

        return ProviderRuntimeState(
            provider_key=model.provider_key,
            cooldown_until=as_utc(model.cooldown_until),
            last_error_category=model.last_error_category,
            consecutive_failures=model.consecutive_failures,
            total_successes=model.total_successes,
            total_failures=model.total_failures,
            last_success_at=as_utc(model.last_success_at),
            last_failure_at=as_utc(model.last_failure_at),
            updated_at=as_utc(model.updated_at),
        )
