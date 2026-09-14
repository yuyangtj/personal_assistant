from __future__ import annotations

import base64
import binascii
import io
import wave

import httpx

from app.integrations.chat import ProviderError
from app.integrations.speech import SpeechAudio

DEFAULT_GEMINI_TTS_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_GEMINI_TTS_VOICE = "Achird"
SAMPLE_RATE = 24_000
MAX_PCM_BYTES = 12 * 1024 * 1024

_DELIVERY_STYLE = {
    "Warm": "warm, friendly, and natural",
    "Curious": "curious, attentive, and lightly animated",
    "Excited": "bright and enthusiastic without shouting",
    "Concerned": "calm, gentle, and reassuring",
    "Neutral": "clear, even, and conversational",
}


class GeminiTtsError(ProviderError):
    """Gemini TTS failed. Messages never include the key, transcript, or response body."""


def _encoded_audio(body: object) -> str:
    """Read current Interactions steps and the short-lived preview response shapes."""
    if not isinstance(body, dict):
        raise TypeError("response must be an object")
    direct = body.get("output_audio")
    if isinstance(direct, dict) and isinstance(direct.get("data"), str):
        return direct["data"]
    for collection_name in ("steps", "outputs"):
        collection = body.get(collection_name)
        if not isinstance(collection, list):
            continue
        for item in reversed(collection):
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            candidates = content if isinstance(content, list) else [item]
            for candidate in reversed(candidates):
                if (
                    isinstance(candidate, dict)
                    and candidate.get("type") == "audio"
                    and isinstance(candidate.get("data"), str)
                ):
                    return candidate["data"]
    raise KeyError("audio output missing")


def _wav_from_pcm(pcm: bytes) -> bytes:
    if not pcm or len(pcm) % 2 or len(pcm) > MAX_PCM_BYTES:
        raise GeminiTtsError("Gemini TTS returned invalid audio data")
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(SAMPLE_RATE)
        target.writeframes(pcm)
    return output.getvalue()


class GeminiTtsClient:
    """Dedicated audio-only Gemini client; it cannot perform chat or task analysis."""

    provider = "gemini-tts"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_GEMINI_TTS_BASE_URL,
        model: str = DEFAULT_GEMINI_TTS_MODEL,
        voice: str = DEFAULT_GEMINI_TTS_VOICE,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError("A Gemini TTS API key is required")
        self.model = model
        self.voice = voice
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"x-goog-api-key": api_key},
            timeout=timeout_seconds,
            transport=transport,
        )

    def synthesize(self, text: str, *, emotion: str) -> SpeechAudio:
        style = _DELIVERY_STYLE.get(emotion, _DELIVERY_STYLE["Warm"])
        payload = {
            "model": self.model,
            "input": (
                f"Read the transcript exactly once in a {style} voice. "
                "Do not add, remove, or answer anything.\nTranscript:\n"
                f"{text}"
            ),
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": self.voice}]},
        }
        try:
            response = self._client.post("/interactions", json=payload)
        except httpx.HTTPError as error:
            raise GeminiTtsError(
                f"Gemini TTS request failed: {type(error).__name__}"
            ) from error
        if response.status_code != 200:
            raise GeminiTtsError(f"Gemini TTS returned HTTP {response.status_code}")
        try:
            body = response.json()
            encoded = _encoded_audio(body)
            pcm = base64.b64decode(encoded, validate=True)
            wav_bytes = _wav_from_pcm(pcm)
        except GeminiTtsError:
            raise
        except (ValueError, KeyError, TypeError, binascii.Error) as error:
            raise GeminiTtsError("Gemini TTS returned an unexpected response shape") from error
        frames = len(pcm) // 2
        return SpeechAudio(
            wav_bytes=wav_bytes,
            provider=self.provider,
            model=str(body.get("model") or self.model),
            voice=self.voice,
            duration_ms=round(frames * 1000 / SAMPLE_RATE),
        )

    def close(self) -> None:
        self._client.close()
