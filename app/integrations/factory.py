"""Builds chat clients from settings, shared by the worker and the API."""

from __future__ import annotations

from app.config import Settings
from app.integrations.chat import ChatClient
from app.integrations.fallback import FallbackChatClient
from app.integrations.kimi import KimiChatClient
from app.integrations.minimax import MiniMaxChatClient


def provider_order(primary: str, fallback: str | None) -> tuple[str, ...]:
    normalized_primary = primary.strip().lower()
    supported = {"kimi", "minimax"}
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
    base_url_override = settings.conversation_model_base_url if primary else None
    model_override = settings.conversation_model_name if primary else None
    if provider == "kimi":
        if not settings.kimi_api_key:
            return None
        return KimiChatClient(
            api_key=settings.kimi_api_key,
            base_url=base_url_override or settings.kimi_base_url,
            model=model_override or settings.kimi_model,
            timeout_seconds=(
                timeout_seconds
                or settings.conversation_model_timeout_seconds
                or settings.kimi_timeout_seconds
            ),
        )
    if not settings.minimax_api_key:
        return None
    return MiniMaxChatClient(
        api_key=settings.minimax_api_key,
        base_url=base_url_override or settings.minimax_base_url,
        model=model_override or settings.minimax_model,
        timeout_seconds=(
            timeout_seconds
            or settings.conversation_model_timeout_seconds
            or settings.minimax_timeout_seconds
        ),
    )


def triage_client(settings: Settings) -> ChatClient | None:
    """A fast client for triage: its own provider first, the other one as fallback.

    Triage runs before every chat reply, so it prefers the quicker provider (Kimi
    answers in about 1.5 s; MiniMax M2 reasons first and takes over 10 s).
    """
    clients = [
        client
        for index, provider in enumerate(provider_order(settings.triage_provider, "auto"))
        if (
            client := build_chat_client(
                settings,
                provider,
                primary=index == 0,
                timeout_seconds=settings.triage_timeout_seconds,
            )
        )
        is not None
    ]
    if not clients:
        return None
    return clients[0] if len(clients) == 1 else FallbackChatClient(clients)


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
