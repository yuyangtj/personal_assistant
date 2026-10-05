"""Calling MCP servers through the gateway in the mcp-tools container.

The MCP servers (Willys, Lidl) run in their own container with a browser and the store
logins; the worker only speaks HTTP to the gateway. Store pages return a lot (Lidl's
offers are hundreds of kilobytes), so results are condensed before a model sees them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

MAX_RESULT_CHARACTERS = 6_000
MAX_LIST_ITEMS = 25
MAX_STRING = 300
#: Keys that are noise for answering questions: pictures, links, tracking.
_DROPPED_KEYS = (
    "image",
    "img",
    "thumbnail",
    "picture",
    "icon",
    "logo",
    "media",
    "url",
    "href",
    "tracking",
    "analytics",
    "seo",
    "html",
)


class McpError(RuntimeError):
    pass


class McpGateway:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ):
        headers = {"authorization": f"Bearer {token}"} if token else {}
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.client.post(path, json=body)
        except httpx.HTTPError as error:
            raise McpError(f"The tools gateway is unreachable: {error}") from error
        if response.status_code != 200:
            try:
                detail = response.json().get("error", response.text)
            except ValueError:
                detail = response.text
            raise McpError(f"{path} failed ({response.status_code}): {str(detail)[:300]}")
        return response.json()

    def list_tools(self, server: str) -> list[dict[str, Any]]:
        return list(self._post(f"/{server}/tools/list", {}).get("tools", []))

    def call(self, server: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._post(f"/{server}/tools/call", {"name": tool, "arguments": arguments})

    def close(self) -> None:
        self.client.close()


def _prune(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _prune(item)
            for key, item in value.items()
            if item not in (None, "", [], {})
            and not any(noise in key.lower() for noise in _DROPPED_KEYS)
        }
    if isinstance(value, list):
        items = [_prune(item) for item in value[:MAX_LIST_ITEMS]]
        if len(value) > MAX_LIST_ITEMS:
            items.append(f"... {len(value) - MAX_LIST_ITEMS} more not shown")
        return items
    if isinstance(value, str) and len(value) > MAX_STRING:
        return value[:MAX_STRING] + "..."
    return value


def condense(result: dict[str, Any], limit: int = MAX_RESULT_CHARACTERS) -> str:
    """An MCP tool result as compact text: JSON pruned of pictures and links, capped."""
    texts = [
        part.get("text", "")
        for part in result.get("content", [])
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    parts = []
    for text in texts:
        try:
            parts.append(
                json.dumps(_prune(json.loads(text)), ensure_ascii=False, separators=(",", ":"))
            )
        except ValueError:
            parts.append(text)
    body = "\n".join(parts) or json.dumps(_prune(result), ensure_ascii=False)
    if result.get("isError"):
        body = "ERROR: " + body
    return body if len(body) <= limit else body[:limit] + " ...(truncated)"


@dataclass(frozen=True)
class Toolset:
    """The tools an agent may use: {server: {tool: description}}."""

    id: str
    description: str
    servers: dict[str, dict[str, str]]

    @classmethod
    def load(cls, path: Path) -> Toolset:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(id=data["id"], description=data["description"], servers=data["servers"])

    def allows(self, qualified: str) -> tuple[str, str] | None:
        """ "willys.get_promotions" -> ("willys", "get_promotions") when allowed."""
        server, _, tool = qualified.partition(".")
        return (server, tool) if tool in self.servers.get(server, {}) else None
