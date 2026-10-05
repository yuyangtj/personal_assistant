from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app.execution.grocery_agent import GroceryAgentExecutor
from app.tools.klarna import KlarnaTools, ToolGateway
from app.tools.mcp import Toolset
from app.work.purchases import PurchaseService
from tests.test_triage import ScriptedChat

HEADER = "date,time,merchant,amount,currency,original_amount,payment_type,status,date_display\n"
EXPORT = HEADER + (
    "2026-09-19,12:28,Uniqlo,499,SEK,,Pay in 30 days,Pending,Yesterday\n"
    "2026-09-02,09:10,Apotea,173,SEK,,Pay in 30 days,,2 Sep\n"
    "2026-08-30,18:00,Uniqlo,299,SEK,,Pay in 30 days,Cancelled,30 Aug\n"
    "2026-08-14,20:05,SJ,388,SEK,,Monthly Invoice,,14 Aug\n"
    "2026-08-14,20:05,SJ,388,SEK,,Monthly Invoice,,14 Aug\n"
)


def _import(client: TestClient, text: str) -> dict:
    return client.post("/purchases/import", json={"csv": "﻿" + text}).json()


def test_an_export_is_imported_and_a_newer_one_merges_without_duplicates(
    client: TestClient,
) -> None:
    first = _import(client, EXPORT)
    # The next export: the pending purchase went through, and one new purchase.
    newer = EXPORT.replace("Pending", "") + "2026-09-25,10:00,Willys,512,SEK,,,,Today\n"
    second = _import(client, newer)

    assert (first["added"], first["updated"]) == (5, 0)  # the two identical SJ tickets both kept
    assert (second["added"], second["updated"], second["unchanged"]) == (1, 1, 4)
    listed = client.get("/purchases?merchant=uniqlo").json()
    assert [p["status"] for p in listed["purchases"]] == ["", "Cancelled"]
    assert listed["overview"]["count"] == 6


def test_a_file_that_is_not_a_klarna_export_is_refused(client: TestClient) -> None:
    response = client.post("/purchases/import", json={"csv": "name,price\nmilk,12\n"})

    assert response.status_code == 422
    assert "doesn't look like a Klarna export" in response.json()["detail"]


def test_spending_leaves_out_cancelled_purchases(client: TestClient) -> None:
    _import(client, EXPORT)
    service = PurchaseService(client.app.state.database)

    august = service.spending(start=date(2026, 8, 1), end=date(2026, 8, 31))
    per_merchant = service.spending(group_by="merchant")

    assert august["total"] == "776.00" and august["purchases"] == 2
    assert august["not_counted"]["count"] == 1
    assert per_merchant["groups"][0] == {"group": "SJ", "total": "776.00", "purchases": 2}
    assert service.spending()["pending"] == 1


def test_the_shopping_agent_answers_from_klarna_without_the_store_gateway(
    client: TestClient,
) -> None:
    _import(client, EXPORT)
    gateway = ToolGateway(KlarnaTools(PurchaseService(client.app.state.database)))
    model = ScriptedChat(
        json.dumps(
            {"action": "tool", "tool": "klarna.spending", "arguments": {"merchant": "uniqlo"}}
        ),
        json.dumps({"action": "finish", "answer": "499 kr at Uniqlo."}),
    )
    agent = GroceryAgentExecutor(model, gateway, Toolset.load(Path("toolsets/groceries.yaml")))

    output = agent.execute(task_id="t", request="Uniqlo spending?", is_cancelled=lambda: False)

    assert output.output["reply"] == "499 kr at Uniqlo."
    catalog = model.requests[0][1].content
    assert "klarna.spending" in catalog and "willys." not in catalog  # stores not connected
    seen = model.requests[1][-1].content
    assert '"total":"499.00"' in seen and "data_covers" in seen


def test_klarna_questions_go_to_the_shopping_agent(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()["id"]

    client.post(
        f"/chat-sessions/{chat}/messages",
        json={"content": "How much did I spend with Klarna last month?"},
    )

    (task,) = client.get(f"/chat-sessions/{chat}/tasks").json()["tasks"]
    assert task["required_capabilities"] == ["groceries"]
