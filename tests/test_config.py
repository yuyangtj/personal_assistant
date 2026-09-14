from __future__ import annotations

from app.config import Settings


def test_manager_model_settings_are_loaded_from_environment(monkeypatch) -> None:
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
