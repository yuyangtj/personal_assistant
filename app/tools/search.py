"""Web search for the research agent: DeepSeek, Tavily or Brave Search, whichever key is set."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.config import Settings

MAX_RESULTS = 5
MAX_SNIPPET_CHARACTERS = 600


class SearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class DeepSeekSearch:
    """DeepSeek Search: https://api-docs.deepseek.com/guides/search/"""

    name = "deepseek"

    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(
            base_url="https://api.deepseek.com",
            timeout=20,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    def search(self, query: str) -> list[SearchResult]:
        try:
            # The query may be at most 128 characters.
            response = self._client.post(
                "/v1/tools/search", json={"query": query[:128], "count": MAX_RESULTS}
            )
        except httpx.HTTPError as error:
            raise SearchError(f"Search failed: {type(error).__name__}") from error
        if response.status_code != 200:
            raise SearchError(f"Search returned HTTP {response.status_code}")
        return [
            SearchResult(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                # The summary is longer than the snippet, when there is one.
                snippet=str(item.get("summary") or item.get("snippet") or "")[
                    :MAX_SNIPPET_CHARACTERS
                ],
            )
            for item in (response.json().get("results") or [])[:MAX_RESULTS]
        ]


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


def build_search(settings: Settings) -> DeepSeekSearch | TavilySearch | BraveSearch | None:
    if settings.deepseek_api_key:
        return DeepSeekSearch(settings.deepseek_api_key)
    if settings.tavily_api_key:
        return TavilySearch(settings.tavily_api_key)
    if settings.brave_search_api_key:
        return BraveSearch(settings.brave_search_api_key)
    return None
