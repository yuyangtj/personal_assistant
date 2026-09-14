from __future__ import annotations

import base64
import io
import json
import wave

import httpx
import pytest

from app.integrations.gemini_tts import GeminiTtsClient, GeminiTtsError
from app.integrations.speech import CachedSpeechSynthesizer, SpeechAudio


def test_gemini_tts_posts_audio_only_request_and_returns_wav() -> None:
    seen: dict[str, object] = {}
    pcm = b"\x00\x00\x10\x00" * 2_400

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["key"] = request.headers["x-goog-api-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "gemini-3.1-flash-tts-preview",
                "output_audio": {"data": base64.b64encode(pcm).decode()},
            },
        )

    client = GeminiTtsClient(
        api_key="gemini-secret",
        voice="Achird",
        transport=httpx.MockTransport(handler),
    )

    result = client.synthesize("Hello there.", emotion="Warm")

    assert seen["path"] == "/v1beta/interactions"
    assert seen["key"] == "gemini-secret"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "gemini-3.1-flash-tts-preview"
    assert body["response_format"] == {"type": "audio"}
    assert body["generation_config"] == {"speech_config": [{"voice": "Achird"}]}
    assert "Hello there." in body["input"]
    assert result.provider == "gemini-tts"
    assert result.duration_ms == 200
    with wave.open(io.BytesIO(result.wav_bytes), "rb") as source:
        assert source.getnchannels() == 1
        assert source.getsampwidth() == 2
        assert source.getframerate() == 24_000
        assert source.readframes(source.getnframes()) == pcm
    client.close()


def test_gemini_tts_reads_current_interactions_steps_response() -> None:
    pcm = b"\x01\x00" * 240

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "gemini-3.1-flash-tts-preview",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [
                            {
                                "type": "audio",
                                "mime_type": "audio/pcm;rate=24000",
                                "data": base64.b64encode(pcm).decode(),
                            }
                        ],
                    }
                ],
            },
        )

    client = GeminiTtsClient(api_key="secret", transport=httpx.MockTransport(handler))
    try:
        output = client.synthesize("Hello", emotion="Warm")
    finally:
        client.close()

    assert output.wav_bytes[:4] == b"RIFF"
    assert output.duration_ms == 10


def test_gemini_tts_errors_are_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad gemini-secret"}})

    client = GeminiTtsClient(
        api_key="gemini-secret",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(GeminiTtsError) as captured:
        client.synthesize("Private transcript", emotion="Warm")

    assert str(captured.value) == "Gemini TTS returned HTTP 401"
    assert "gemini-secret" not in str(captured.value)
    assert "Private transcript" not in str(captured.value)
    client.close()


def test_tts_cache_avoids_duplicate_provider_calls_and_evicts_old_entries() -> None:
    class RecordingSynthesizer:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def synthesize(self, text: str, *, emotion: str) -> SpeechAudio:
            self.calls.append((text, emotion))
            return SpeechAudio(b"RIFF", "gemini-tts", "model", "voice", 10)

        def close(self) -> None:
            pass

    provider = RecordingSynthesizer()
    cache = CachedSpeechSynthesizer(provider, max_entries=1)

    assert cache.synthesize("Hello", emotion="Warm").cache_hit is False
    assert cache.synthesize("Hello", emotion="Warm").cache_hit is True
    assert cache.synthesize("Other", emotion="Warm").cache_hit is False
    assert cache.synthesize("Hello", emotion="Warm").cache_hit is False
    assert provider.calls == [
        ("Hello", "Warm"),
        ("Other", "Warm"),
        ("Hello", "Warm"),
    ]
