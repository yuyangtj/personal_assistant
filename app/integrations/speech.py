from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from threading import Lock
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SpeechAudio:
    wav_bytes: bytes
    provider: str
    model: str
    voice: str
    duration_ms: int
    cache_hit: bool = False


class SpeechSynthesizer(Protocol):
    def synthesize(self, text: str, *, emotion: str) -> SpeechAudio: ...

    def close(self) -> None: ...


class CachedSpeechSynthesizer:
    """A bounded in-memory cache that avoids repeat billable TTS calls."""

    def __init__(self, client: SpeechSynthesizer, *, max_entries: int = 128):
        if max_entries < 1:
            raise ValueError("TTS cache size must be positive")
        self.client = client
        self.max_entries = max_entries
        self._cache: dict[str, SpeechAudio] = {}
        self._order: list[str] = []
        self._lock = Lock()

    def synthesize(self, text: str, *, emotion: str) -> SpeechAudio:
        key = sha256(f"{emotion}\0{text}".encode()).hexdigest()
        # Keep the lock through synthesis so concurrent duplicate requests make one paid call.
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._order.remove(key)
                self._order.append(key)
                return replace(cached, cache_hit=True)
            result = self.client.synthesize(text, emotion=emotion)
            self._cache[key] = replace(result, cache_hit=False)
            self._order.append(key)
            while len(self._order) > self.max_entries:
                self._cache.pop(self._order.pop(0), None)
            return replace(result, cache_hit=False)

    def close(self) -> None:
        self.client.close()
