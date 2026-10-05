from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.chat.triage import Triager
from app.execution.grocery_agent import GroceryAgentExecutor
from app.tools.mcp import McpError, McpGateway, Toolset, condense
from tests.test_triage import ScriptedChat, suggestion

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


def _agent(gateway, *replies) -> tuple[GroceryAgentExecutor, ScriptedChat]:
    model = ScriptedChat(*replies)
    return GroceryAgentExecutor(model, gateway, TOOLSET, today=lambda: date(2026, 10, 5)), model


def _run(agent: GroceryAgentExecutor, request: str) -> dict:
    return agent.execute(task_id="t", request=request, is_cancelled=lambda: False).output


# --- condensing store results -------------------------------------------------------


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


def test_the_agent_answers_from_the_store_tools() -> None:
    gateway = FakeGateway(
        {"willys.get_promotions": _text({"offers": [{"name": "Kaffe", "price": "49,90 kr"}]})}
    )
    agent, model = _agent(
        gateway,
        json.dumps(
            {"action": "tool", "tool": "willys.get_promotions", "arguments": {"query": "kaffe"}}
        ),
        json.dumps({"action": "finish", "answer": "Willys has coffee at 49,90 kr this week."}),
    )

    output = _run(agent, "is coffee on offer at willys?")

    assert output["reply"] == "Willys has coffee at 49,90 kr this week."
    assert gateway.calls == [("willys", "get_promotions", {"query": "kaffe"})]
    catalog = model.requests[0][1].content
    assert "willys.get_promotions" in catalog and "query (string)" in catalog
    assert "lidl.get_historical_receipts" in catalog
    assert "Today is 2026-10-05" in model.requests[0][0].content
    assert "Kaffe" in model.requests[1][-1].content


def test_the_agent_cannot_call_tools_outside_its_toolset() -> None:
    gateway = FakeGateway({})
    agent, model = _agent(
        gateway,
        json.dumps({"action": "tool", "tool": "lidl.resolve_store", "arguments": {}}),
        json.dumps({"action": "tool", "tool": "willys.add_to_cart", "arguments": {}}),
        json.dumps({"action": "finish", "answer": "I can't do that."}),
    )

    _run(agent, "add milk to my cart")

    assert gateway.calls == []
    assert "not an available tool" in model.requests[1][-1].content
    assert "not an available tool" in model.requests[2][-1].content


def test_a_failing_tool_is_reported_to_the_model() -> None:
    gateway = FakeGateway({})
    agent, model = _agent(
        gateway,
        json.dumps({"action": "tool", "tool": "lidl.get_historical_receipts", "arguments": {}}),
        json.dumps({"action": "finish", "answer": "Lidl isn't reachable right now."}),
    )

    output = _run(agent, "what did I buy at lidl?")

    assert "ERROR:" in model.requests[1][-1].content
    assert output["tool_calls"] == [{"tool": "lidl.get_historical_receipts", "arguments": {}}]


# --- routing from chat ------------------------------------------------------------------


def test_grocery_questions_go_to_the_grocery_agent(client: TestClient) -> None:
    state = client.app.state
    state.triager = Triager(state.repository_registry, groceries_available=True)
    chat = client.post("/chat-sessions", json={}).json()["id"]

    posted = client.post(
        f"/chat-sessions/{chat}/messages", json={"content": "What's on offer at Willys this week?"}
    ).json()

    assert posted["decision"]["handled"] is True
    (task,) = client.get(f"/chat-sessions/{chat}/tasks").json()["tasks"]
    assert task["required_capabilities"] == ["groceries"]


def test_the_model_can_flag_grocery_questions() -> None:
    from app.repositories import RepositoryRegistry

    repositories = RepositoryRegistry.from_directory("repositories")
    chat = ScriptedChat(suggestion(intent="answer", needs_grocery_tools=True))

    decision = Triager(repositories, chat, groceries_available=True).decide(
        "how much did I spend on food last month?"
    )

    assert decision.capabilities == ("groceries",)


def test_without_the_gateway_grocery_questions_are_ordinary_chat() -> None:
    from app.repositories import RepositoryRegistry

    decision = Triager(RepositoryRegistry.from_directory("repositories")).decide(
        "What's on offer at Willys this week?"
    )

    assert "groceries" not in decision.capabilities


def test_the_grocery_agent_is_routable() -> None:
    from app.capabilities import CapabilityRegistry

    chosen = CapabilityRegistry.from_directory("capabilities").select(["groceries"])

    assert chosen.id == "grocery-agent"


def test_minimax_native_tool_calls_are_understood() -> None:
    gateway = FakeGateway({"willys.get_purchase_history": _text({"configured": False})})
    agent, _ = _agent(
        gateway,
        '[TOOL_CALL] {tool => "willys.get_purchase_history", arguments => '
        '{"fromDate": "2026-09-28", "toDate": "2026-10-04"}} [/TOOL_CALL]',
        json.dumps({"action": "finish", "answer": "Your Willys account isn't connected yet."}),
    )

    output = _run(agent, "what did I buy at willys last week?")

    assert gateway.calls == [
        ("willys", "get_purchase_history", {"fromDate": "2026-09-28", "toDate": "2026-10-04"})
    ]
    assert output["reply"] == "Your Willys account isn't connected yet."


def test_an_agent_that_never_answers_gets_a_polite_reply_not_a_failure() -> None:
    call = json.dumps({"action": "tool", "tool": "willys.get_promotions", "arguments": {}})
    gateway = FakeGateway({"willys.get_promotions": _text({"offers": []})})
    agent, model = _agent(gateway, *[call] * 10)

    output = _run(agent, "offers?")

    assert output["reply"].startswith("Sorry, I couldn't get a clear answer")
    assert len(gateway.calls) == 1  # repeats are answered from the earlier result
    assert "Last step" in model.requests[-1][-1].content
