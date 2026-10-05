"""Klarna purchases as tools for the shopping agent, answered from the server's own database.

They look like an MCP server ("klarna") to the agent, so it uses them the same way as the
Willys and Lidl tools; nothing leaves the server except the rows a question needs.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from app.tools.mcp import McpError, McpGateway
from app.work.purchases import PurchaseService

SERVER = "klarna"

_DATES = {
    "from": {"type": "string", "description": "First day, YYYY-MM-DD"},
    "to": {"type": "string", "description": "Last day, YYYY-MM-DD"},
    "merchant": {"type": "string", "description": "Part of the merchant's name"},
}
TOOLS = [
    {
        "name": "spending",
        "inputSchema": {
            "properties": {
                **_DATES,
                "group_by": {"type": "string", "enum": ["merchant", "month"]},
            }
        },
    },
    {
        "name": "purchases",
        "inputSchema": {"properties": {**_DATES, "limit": {"type": "integer"}}},
    },
    {"name": "upload_export", "inputSchema": {"properties": {}}},
]
UPLOAD = "klarna-purchases"
#: Data whose newest purchase is older than this gets an offer to upload a newer export.
STALE_AFTER = timedelta(days=14)


def _date(value: Any, name: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise McpError(f"{name} must be YYYY-MM-DD, not {value!r}") from error


def _text(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


class KlarnaTools:
    def __init__(self, purchases: PurchaseService, *, today: Callable[[], date] = date.today):
        self.purchases = purchases
        self.today = today

    def list_tools(self) -> list[dict[str, Any]]:
        return TOOLS

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool == "upload_export":
            return _text(
                {
                    "offer_upload": UPLOAD,
                    "note": "An upload button is shown with your answer; ask the user to pick "
                    "their Klarna export (CSV) with it.",
                }
            )
        overview = self.purchases.overview()
        if not overview["count"]:
            return _text(
                {
                    "purchases": 0,
                    "offer_upload": UPLOAD,
                    "note": "No Klarna export imported yet. An upload button is shown with your "
                    "answer; ask the user to upload their Klarna export (CSV) with it.",
                }
            )
        span = {
            "start": _date(arguments.get("from"), "from"),
            "end": _date(arguments.get("to"), "to"),
            "merchant": (str(arguments.get("merchant") or "").strip() or None),
        }
        # The data is only as fresh as the last export; say so with every answer.
        known: dict[str, Any] = {"data_covers": f"{overview['first']} to {overview['last']}"}
        if self.today() - overview["last"] > STALE_AFTER:
            known["offer_upload"] = UPLOAD
            known["note"] = (
                f"The data ends {overview['last']}. An upload button for a newer export is "
                "shown with your answer; mention it when the question needs newer purchases."
            )
        if tool == "spending":
            group_by = arguments.get("group_by")
            if group_by not in (None, "", "merchant", "month"):
                raise McpError("group_by must be merchant or month")
            return _text(known | self.purchases.spending(**span, group_by=group_by or None))
        if tool == "purchases":
            try:
                limit = max(1, min(int(arguments.get("limit") or 25), 25))
            except (TypeError, ValueError):
                limit = 25
            rows = self.purchases.purchases(**span, limit=limit)
            return _text(
                known
                | {
                    "purchases": [
                        {
                            "date": row.occurred_on.isoformat(),
                            "time": row.occurred_time,
                            "merchant": row.merchant,
                            "amount": f"{row.amount} {row.currency}",
                            "payment": row.payment_type,
                            "status": row.status or "ok",
                        }
                        for row in rows
                    ]
                }
            )
        raise McpError(f"klarna has no tool {tool!r}")


class ToolGateway:
    """The agent's tools: Klarna from the database, the store servers through MCP."""

    def __init__(self, klarna: KlarnaTools, mcp: McpGateway | None = None):
        self.klarna = klarna
        self.mcp = mcp

    def connected(self, server: str) -> bool:
        return server == SERVER or self.mcp is not None

    def list_tools(self, server: str) -> list[dict[str, Any]]:
        if server == SERVER:
            return self.klarna.list_tools()
        if self.mcp is None:
            raise McpError(f"{server} is not connected")
        return self.mcp.list_tools(server)

    def call(self, server: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if server == SERVER:
            return self.klarna.call(tool, arguments)
        if self.mcp is None:
            raise McpError(f"{server} is not connected")
        return self.mcp.call(server, tool, arguments)
