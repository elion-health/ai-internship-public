"""Exa-backed search, used only to supplement Firecrawl's search results."""

from __future__ import annotations

from ai_internship.discovery.providers import SearchHit


class ExaDiscoveryProvider:
    """Adapter that presents Exa search results through the local discovery contracts."""

    def __init__(self, api_key: str, client=None):
        if client is None:
            if not api_key:
                raise ValueError("EXA_API_KEY is required for Exa-backed discovery")
            from exa_py import Exa

            client = Exa(api_key=api_key)
        self._client = client

    def search(self, query: str, limit: int) -> list[SearchHit]:
        response = self._client.search(query, num_results=limit)
        return [
            SearchHit(url=result.url, title=result.title, snippet=None)
            for result in response.results or []
            if result.url
        ]
