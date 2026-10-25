"""The store gateway the chat agent's shopping tools go through."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.tools.mcp import McpError, McpGateway, Toolset, condense

TOOLSET = Toolset.load(Path("toolsets/groceries.yaml"))


def _text(payload) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


class FakeGateway:
    """Stands in for the mcp-tools gateway: canned store data per tool."""

    def __init__(self, results: dict[str, dict]):
        self.results = results
        self.calls: list[tuple[str, str, dict]] = []

    def list_tools(self, server: str) -> list[dict]:
        return [
            {
                "name": "get_promotions",
                "inputSchema": {"properties": {"query": {"type": "string"}}},
            },
            {
                "name": "get_purchase_history",
                "inputSchema": {"properties": {"fromDate": {"type": "string"}}},
            },
        ]

    def call(self, server: str, tool: str, arguments: dict) -> dict:
        self.calls.append((server, tool, arguments))
        result = self.results.get(f"{server}.{tool}")
        if result is None:
            raise McpError(f"/{server}/tools/call failed (502): boom")
        return result


def test_store_results_are_condensed_for_the_model() -> None:
    offers = [
        {
            "name": f"Item {n}",
            "price": "19,40 kr",
            "imageUrl": "https://img/x.png",
            "url": "https://x",
        }
        for n in range(40)
    ]

    text = condense(_text({"source": "willys.se", "offers": offers, "description": "x" * 900}))

    assert "imageUrl" not in text and "https://" not in text
    assert "Item 24" in text and "Item 25" not in text
    assert "15 more not shown" in text
    assert len(condense(_text({"blob": ["y" * 290] * 25}), limit=500)) <= 520


def test_a_tool_error_is_marked() -> None:
    result = {"isError": True, "content": [{"type": "text", "text": "login not configured"}]}

    assert condense(result).startswith("ERROR: login not configured")


# --- the gateway client -----------------------------------------------------------------


def test_the_gateway_client_sends_the_token_and_reports_failures() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/willys/tools/list":
            return httpx.Response(200, json={"tools": [{"name": "get_promotions"}]})
        return httpx.Response(502, json={"error": "willys exited (1)"})

    gateway = McpGateway("http://mcp-tools:8790/", "secret", transport=httpx.MockTransport(handler))

    assert gateway.list_tools("willys") == [{"name": "get_promotions"}]
    assert seen[0].headers["authorization"] == "Bearer secret"
    with pytest.raises(McpError, match="willys exited"):
        gateway.call("willys", "get_promotions", {})


# --- the grocery agent ------------------------------------------------------------------
