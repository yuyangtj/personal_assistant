"""Push notifications to the phone through ntfy (https://ntfy.sh or self-hosted).

Off unless ``ASSISTANT_NTFY_TOPIC_URL`` is set. A failed push is logged and never
fails the work that triggered it.
"""

from __future__ import annotations

import base64
import logging
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    def send(self, title: str, message: str, *, priority: int = 3, path: str = "") -> bool: ...


class NullNotifier:
    def send(self, title: str, message: str, *, priority: int = 3, path: str = "") -> bool:
        logger.debug("Push not configured; skipped: %s", title)
        return False


class NtfyNotifier:
    def __init__(
        self,
        topic_url: str,
        *,
        token: str | None = None,
        public_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.topic_url = topic_url
        self.public_url = (public_url or "").rstrip("/")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(timeout=10, headers=headers, transport=transport)

    def send(self, title: str, message: str, *, priority: int = 3, path: str = "") -> bool:
        headers = {
            # HTTP headers must be Latin-1; ntfy reads RFC 2047 encoded titles.
            "Title": f"=?UTF-8?B?{_b64(title[:120])}?=",
            "Priority": str(min(max(priority, 1), 5)),
        }
        if self.public_url:
            headers["Click"] = f"{self.public_url}{path}"
        try:
            response = self._client.post(
                self.topic_url, content=message[:2000].encode(), headers=headers
            )
        except httpx.HTTPError as error:
            logger.warning("Push failed: %s", type(error).__name__)
            return False
        if response.status_code >= 300:
            logger.warning("Push failed: HTTP %s", response.status_code)
            return False
        return True


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def build_notifier(topic_url: str | None, *, token: str | None, public_url: str | None):
    if not topic_url:
        return NullNotifier()
    return NtfyNotifier(topic_url, token=token, public_url=public_url)
