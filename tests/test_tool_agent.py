from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.execution.tool_agent import ToolAgentExecutor
from app.tools.search import (
    BraveSearch,
    DeepSeekSearch,
    SearchError,
    SearchResult,
    TavilySearch,
    build_search,
)
from tests.fakes import ScriptedChat


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


def test_deepseek_search_sends_a_short_query_and_prefers_the_summary() -> None:
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "A", "url": "https://a", "snippet": "s", "summary": "long"},
                    {"title": "B", "url": "https://b", "snippet": "only snippet"},
                ]
            },
        )

    search = DeepSeekSearch("ds", transport=httpx.MockTransport(handler))

    assert search.search("x" * 200) == [
        SearchResult("A", "https://a", "long"),
        SearchResult("B", "https://b", "only snippet"),
    ]
    (request,) = sent
    assert request.url == "https://api.deepseek.com/v1/tools/search"
    assert request.headers["authorization"] == "Bearer ds"
    assert json.loads(request.content) == {"query": "x" * 128, "count": 5}


def test_the_first_search_key_set_wins() -> None:
    def name(**keys) -> str | None:
        search = build_search(Settings(**keys))
        return search.name if search else None

    assert name() is None
    assert name(brave_search_api_key="b") == "brave"
    assert name(tavily_api_key="t", brave_search_api_key="b") == "tavily"
    assert name(deepseek_api_key="d", tavily_api_key="t") == "deepseek"
    assert Settings(deepseek_api_key="d").web_search_available


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


def test_a_follow_up_question_is_researched_in_the_chats_context() -> None:
    from app.execution.base import ConversationTurn

    chat = ScriptedChat(
        json.dumps({"action": "finish", "answer": "In chapter 3.", "sources": []}),
    )
    history = [
        ConversationTurn(
            "影之刃零有吸血武器吗", "有。核心吸血武器是【亡命斩马刀】……配饰【贪狼】也有攻击回血"
        ),
        # The research task starts before the reply that handed it off is posted.
        ConversationTurn("贪狼在哪里可以找到", ""),
    ]

    ToolAgentExecutor(chat, FakeSearch()).execute(
        task_id="t", request="贪狼在哪里可以找到", is_cancelled=lambda: False, history=history
    )

    sent = chat.requests[0]
    context = [m.content for m in sent if m.role == "system" and "The chat so far" in m.content]
    assert len(context) == 1
    assert "User: 影之刃零有吸血武器吗" in context[0]
    assert "Assistant: 有。核心吸血武器" in context[0]
    assert context[0].count("贪狼在哪里可以找到") == 0  # the question itself comes once, last
    assert (sent[-1].role, sent[-1].content) == ("user", "贪狼在哪里可以找到")


def test_a_first_question_has_no_chat_context() -> None:
    chat = ScriptedChat(json.dumps({"action": "finish", "answer": "A.", "sources": []}))

    _run(ToolAgentExecutor(chat, FakeSearch()))

    assert not any("The chat so far" in m.content for m in chat.requests[0])
