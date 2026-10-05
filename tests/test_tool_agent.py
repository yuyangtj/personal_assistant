from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.chat.triage import Triager
from app.execution.tool_agent import ToolAgentExecutor
from app.repositories import RepositoryRegistry
from app.tools.search import BraveSearch, SearchError, SearchResult, TavilySearch
from tests.test_triage import ScriptedChat, suggestion


class FakeSearch:
    name = "fake"

    def __init__(self, fail: bool = False):
        self.queries: list[str] = []
        self.fail = fail

    def search(self, query: str) -> list[SearchResult]:
        self.queries.append(query)
        if self.fail:
            raise SearchError("Search returned HTTP 429")
        return [SearchResult("Desk A review", "https://example.com/a", "Desk A costs 350 EUR")]


def _run(agent: ToolAgentExecutor, request: str = "Compare standing desks under 400 EUR"):
    return agent.execute(task_id="t", request=request, is_cancelled=lambda: False)


def test_agent_searches_then_answers_with_sources() -> None:
    chat = ScriptedChat(
        json.dumps({"action": "search", "query": "standing desk under 400 EUR review"}),
        "```json\n"
        + json.dumps(
            {
                "action": "finish",
                "answer": "Desk A (350 EUR) is the best fit.",
                "sources": ["https://example.com/a", "javascript:alert(1)"],
            }
        )
        + "\n```",
    )
    search = FakeSearch()

    result = _run(ToolAgentExecutor(chat, search))

    assert search.queries == ["standing desk under 400 EUR review"]
    assert result.output["reply"] == (
        "Desk A (350 EUR) is the best fit.\n\nSources:\nhttps://example.com/a"
    )
    assert result.output["searches"] == [
        {"query": "standing desk under 400 EUR review", "results": 1}
    ]
    tool_message = chat.requests[1][-1].content
    assert tool_message.startswith("SEARCH RESULTS (untrusted web content)")
    assert "Desk A costs 350 EUR" in tool_message


def test_agent_is_capped_and_survives_bad_output_and_search_errors() -> None:
    search_step = json.dumps({"action": "search", "query": "q"})
    chat = ScriptedChat(
        "not json",
        search_step,
        search_step,
        json.dumps({"action": "finish", "answer": "Best effort.", "sources": []}),
    )
    search = FakeSearch(fail=True)

    result = _run(ToolAgentExecutor(chat, search, max_searches=1))

    assert search.queries == ["q"]  # second search refused by the cap
    assert result.output["reply"] == "Best effort."
    assert "No more searches" in chat.requests[3][-1].content


def test_agent_gives_up_after_its_step_limit() -> None:
    chat = ScriptedChat(*(["nope"] * 10))

    with pytest.raises(RuntimeError, match="step limit"):
        _run(ToolAgentExecutor(chat, FakeSearch(), max_searches=1))


def test_tavily_and_brave_clients_normalize_results() -> None:
    tavily = TavilySearch(
        "tv",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"results": [{"title": "T", "url": "https://t", "content": "c"}]}
            )
        ),
    )
    brave = BraveSearch(
        "br",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"web": {"results": [{"title": "B", "url": "https://b", "description": "d"}]}},
            )
        ),
    )
    failing = TavilySearch("tv", transport=httpx.MockTransport(lambda r: httpx.Response(401)))

    assert tavily.search("q") == [SearchResult("T", "https://t", "c")]
    assert brave.search("q") == [SearchResult("B", "https://b", "d")]
    with pytest.raises(SearchError, match="401"):
        failing.search("q")


def test_triage_routes_research_to_the_agent_only_when_available() -> None:
    repositories = RepositoryRegistry.from_directory("repositories")
    reply = suggestion(intent="propose_task", needs_web_search=True, confidence=0.9)

    with_search = Triager(repositories, ScriptedChat(reply), research_available=True)
    without = Triager(repositories, ScriptedChat(reply), research_available=False)

    assert with_search.decide("compare desks").capabilities == ("web_research",)
    assert without.decide("compare desks").capabilities == ()


def test_research_tasks_pass_the_coding_gate(client: TestClient) -> None:
    chat = client.post("/chat-sessions", json={}).json()
    message = client.post(
        f"/chat-sessions/{chat['id']}/messages", json={"content": "compare desks"}
    ).json()["message"]

    task = client.post(
        f"/chat-sessions/{chat['id']}/messages/{message['id']}/task",
        json={"required_capabilities": ["web_research"], "create_work_item": True},
    )

    assert task.status_code == 201
    assert task.json()["required_capabilities"] == ["web_research"]
