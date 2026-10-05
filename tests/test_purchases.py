from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app.chat.uploads import upload_block
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
TOOLSET = Toolset.load(Path("toolsets/groceries.yaml"))


def _service(client: TestClient) -> PurchaseService:
    return PurchaseService(client.app.state.database)


def _agent(client: TestClient, *replies: str, today=date(2026, 9, 25)):
    gateway = ToolGateway(KlarnaTools(_service(client), today=lambda: today))
    model = ScriptedChat(*replies)
    return GroceryAgentExecutor(model, gateway, TOOLSET), model


def _call(tool: str, **arguments) -> str:
    return json.dumps({"action": "tool", "tool": f"klarna.{tool}", "arguments": arguments})


def _finish(answer: str) -> str:
    return json.dumps({"action": "finish", "answer": answer})


def _offer(client: TestClient) -> tuple[str, str, str]:
    """A chat whose last assistant message offers a Klarna upload, as the agent replies."""
    chat = client.post("/chat-sessions", json={}).json()["id"]
    service = client.app.state.task_service
    posted = service.append_chat_message(
        chat,
        content="Upload your export:",
        role="assistant",
        blocks=[upload_block("klarna-purchases")],
    )
    return chat, posted.message.id, posted.message.blocks[0]["id"]


def _upload(client: TestClient, chat: str, message: str, block: str, text: str):
    return client.post(
        f"/chat-sessions/{chat}/messages/{message}/uploads/{block}",
        json={"filename": "klarna.csv", "content": "﻿" + text},
    )


def test_an_export_uploaded_in_the_chat_is_imported_and_answered(client: TestClient) -> None:
    chat, message, block = _offer(client)

    response = _upload(client, chat, message, block, EXPORT)

    assert response.status_code == 200
    assert response.json()["reply"].startswith("Imported your Klarna export: 5 new purchases")
    messages = client.get(f"/chat-sessions/{chat}/messages").json()["messages"]
    assert [m["content"] for m in messages[-2:]][0] == "📎 klarna.csv"
    offered = next(m for m in messages if m["id"] == message)["blocks"][0]
    assert offered["state"] == "done" and offered["result"].startswith("Imported")
    # The button is used up; a newer export needs a new offer.
    assert _upload(client, chat, message, block, EXPORT).status_code == 409


def test_a_wrong_file_keeps_the_button_open(client: TestClient) -> None:
    chat, message, block = _offer(client)

    refused = _upload(client, chat, message, block, "name,price\nmilk,12\n")

    assert refused.status_code == 422
    assert "doesn't look like a Klarna export" in refused.json()["detail"]
    assert _upload(client, chat, message, block, EXPORT).status_code == 200


def test_only_offered_uploads_are_accepted(client: TestClient) -> None:
    chat, message, _ = _offer(client)

    assert _upload(client, chat, message, "made-up", EXPORT).status_code == 404
    other = client.post("/chat-sessions", json={}).json()["id"]
    assert _upload(client, other, message, "made-up", EXPORT).status_code == 404


def test_a_newer_export_merges_without_duplicates(client: TestClient) -> None:
    service = _service(client)
    first = service.import_klarna_csv(EXPORT)
    newer = EXPORT.replace("Pending", "") + "2026-09-25,10:00,Willys,512,SEK,,,,Today\n"
    second = service.import_klarna_csv(newer)

    assert (first.added, first.updated) == (5, 0)  # the two identical SJ tickets both kept
    assert (second.added, second.updated, second.unchanged) == (1, 1, 4)
    assert [p.status for p in service.purchases(merchant="uniqlo")] == ["", "Cancelled"]


def test_spending_leaves_out_cancelled_purchases(client: TestClient) -> None:
    service = _service(client)
    service.import_klarna_csv(EXPORT)

    august = service.spending(start=date(2026, 8, 1), end=date(2026, 8, 31))
    per_merchant = service.spending(group_by="merchant")

    assert august["total"] == "776.00" and august["purchases"] == 2
    assert august["not_counted"]["count"] == 1
    assert per_merchant["groups"][0] == {"group": "SJ", "total": "776.00", "purchases": 2}
    assert service.spending()["pending"] == 1


def test_with_no_purchases_the_agent_offers_an_upload(client: TestClient) -> None:
    agent, _ = _agent(client, _call("spending"), _finish("Upload your Klarna export first."))

    output = agent.execute(task_id="t", request="Klarna spending?", is_cancelled=lambda: False)

    (block,) = output.output["blocks"]
    assert (block["type"], block["target"]) == ("upload", "klarna-purchases")


def test_the_agent_answers_from_klarna_without_the_store_gateway(client: TestClient) -> None:
    _service(client).import_klarna_csv(EXPORT)
    agent, model = _agent(client, _call("spending", merchant="uniqlo"), _finish("499 kr."))

    output = agent.execute(task_id="t", request="Uniqlo spending?", is_cancelled=lambda: False)

    assert output.output["reply"] == "499 kr." and "blocks" not in output.output
    catalog = model.requests[0][1].content
    assert "klarna.spending" in catalog and "willys." not in catalog  # stores not connected
    seen = model.requests[1][-1].content
    assert '"total":"499.00"' in seen and "data_covers" in seen


def test_stale_data_and_asking_to_upload_both_offer_the_button(client: TestClient) -> None:
    _service(client).import_klarna_csv(EXPORT)
    stale, _ = _agent(client, _call("purchases"), _finish("ok"), today=date(2026, 11, 1))
    asked, _ = _agent(client, _call("upload_export"), _finish("Use the button."))

    for agent in (stale, asked):
        output = agent.execute(task_id="t", request="q", is_cancelled=lambda: False)
        assert output.output["blocks"][0]["target"] == "klarna-purchases"


def test_klarna_questions_go_to_the_shopping_agent(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()["id"]

    client.post(
        f"/chat-sessions/{chat}/messages",
        json={"content": "How much did I spend with Klarna last month?"},
    )

    (task,) = client.get(f"/chat-sessions/{chat}/tasks").json()["tasks"]
    assert task["required_capabilities"] == ["groceries"]
