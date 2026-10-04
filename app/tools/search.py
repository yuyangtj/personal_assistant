"""Web search for the research agent: Tavily or Brave Search, whichever key is set."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

MAX_RESULTS = 5
MAX_SNIPPET_CHARACTERS = 600


class SearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class TavilySearch:
    name = "tavily"

    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(
            base_url="https://api.tavily.com",
            timeout=20,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    def search(self, query: str) -> list[SearchResult]:
        try:
            response = self._client.post(
                "/search", json={"query": query, "max_results": MAX_RESULTS}
            )
        except httpx.HTTPError as error:
            raise SearchError(f"Search failed: {type(error).__name__}") from error
        if response.status_code != 200:
            raise SearchError(f"Search returned HTTP {response.status_code}")
        return [
            SearchResult(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                snippet=str(item.get("content") or "")[:MAX_SNIPPET_CHARACTERS],
            )
            for item in (response.json().get("results") or [])[:MAX_RESULTS]
        ]


class BraveSearch:
    name = "brave"

    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(
            base_url="https://api.search.brave.com",
            timeout=20,
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            transport=transport,
        )

    def search(self, query: str) -> list[SearchResult]:
        try:
            response = self._client.get(
                "/res/v1/web/search", params={"q": query, "count": MAX_RESULTS}
            )
        except httpx.HTTPError as error:
            raise SearchError(f"Search failed: {type(error).__name__}") from error
        if response.status_code != 200:
            raise SearchError(f"Search returned HTTP {response.status_code}")
        results = ((response.json().get("web") or {}).get("results") or [])[:MAX_RESULTS]
        return [
            SearchResult(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                snippet=str(item.get("description") or "")[:MAX_SNIPPET_CHARACTERS],
            )
            for item in results
        ]


def build_search(*, tavily_api_key: str | None, brave_api_key: str | None):
    if tavily_api_key:
        return TavilySearch(tavily_api_key)
    if brave_api_key:
        return BraveSearch(brave_api_key)
    return None
