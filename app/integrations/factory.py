"""Builds chat clients from settings, shared by the worker and the API."""

from __future__ import annotations

from app.config import Settings
from app.integrations.chat import ChatClient
from app.integrations.fallback import FallbackChatClient
from app.integrations.openai_compat import PRESETS, OpenAICompatibleChatClient

#: Chat providers. "auto" falls back from Kimi to MiniMax, and from the others to Kimi.
CHAT_PROVIDERS = frozenset(PRESETS)


def provider_order(
    primary: str, fallback: str | None, *, supported: frozenset[str] = CHAT_PROVIDERS
) -> tuple[str, ...]:
    normalized_primary = primary.strip().lower()
    if normalized_primary not in supported:
        raise ValueError(f"Unsupported model provider: {normalized_primary}")
    if fallback is None or fallback.strip().lower() in {"", "none", "off", "disabled"}:
        return (normalized_primary,)
    normalized_fallback = fallback.strip().lower()
    if normalized_fallback == "auto":
        normalized_fallback = "minimax" if normalized_primary == "kimi" else "kimi"
    if normalized_fallback not in supported:
        raise ValueError(f"Unsupported fallback model provider: {normalized_fallback}")
    return tuple(dict.fromkeys((normalized_primary, normalized_fallback)))


def build_chat_client(
    settings: Settings,
    provider: str,
    *,
    primary: bool,
    timeout_seconds: float | None = None,
) -> ChatClient | None:
    """One provider's client, or None without its API key."""
    keys = {
        "kimi": settings.kimi_api_key,
        "deepseek": settings.deepseek_api_key,
        "gemini": settings.gemini_api_key,
        "minimax": settings.minimax_api_key,
    }
    base_urls = {
        "kimi": settings.kimi_base_url,
        "deepseek": settings.deepseek_base_url,
        "gemini": settings.gemini_base_url,
        "minimax": settings.minimax_base_url,
    }
    models = {
        "kimi": settings.kimi_model,
        "deepseek": settings.deepseek_model,
        "gemini": settings.gemini_model,
        "minimax": settings.minimax_model,
    }
    timeouts = {
        "kimi": settings.kimi_timeout_seconds,
        "deepseek": settings.deepseek_timeout_seconds,
        "gemini": 30.0,
        "minimax": settings.minimax_timeout_seconds,
    }
    extra: dict[str, object] = {}
    if provider == "deepseek":
        extra["thinking"] = {"type": "enabled" if settings.deepseek_thinking else "disabled"}
    if provider == "gemini":
        extra["reasoning_effort"] = settings.gemini_reasoning_effort or None
    if not keys[provider]:
        return None
    return OpenAICompatibleChatClient(
        PRESETS[provider],
        api_key=keys[provider],
        base_url=(settings.conversation_model_base_url if primary else None) or base_urls[provider],
        model=(settings.conversation_model_name if primary else None) or models[provider],
        timeout_seconds=(
            timeout_seconds or settings.conversation_model_timeout_seconds or timeouts[provider]
        ),
        extra_payload=extra,
    )


def chat_client_chain(
    settings: Settings, *, timeout_seconds: float | None = None
) -> ChatClient | None:
    """The configured conversation provider with its fallback, or None without credentials."""
    order = provider_order(
        settings.conversation_model_provider,
        settings.conversation_model_fallback_provider,
    )
    clients = [
        client
        for index, provider in enumerate(order)
        if (
            client := build_chat_client(
                settings, provider, primary=index == 0, timeout_seconds=timeout_seconds
            )
        )
        is not None
    ]
    if not clients:
        return None
    return clients[0] if len(clients) == 1 else FallbackChatClient(clients)
