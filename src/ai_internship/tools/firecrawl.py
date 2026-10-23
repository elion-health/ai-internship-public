"""Firecrawl search provider."""

import warnings

with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r'Field name "json" in "MonitorPage(Diff|Snapshot)" shadows an attribute',
        category=UserWarning,
    )
    from firecrawl import FirecrawlApp

from ai_internship.tools.base import SearchProvider, SearchResult


class FirecrawlSearchProvider(SearchProvider):
    """Web search via Firecrawl API."""

    def __init__(self, api_key: str):
        self._client = FirecrawlApp(api_key=api_key)

    def search(self, query: str, num_results: int = 10) -> list[SearchResult]:
        """Search the web and return results.

        Args:
            query: Search query string.
            num_results: Number of results to return.

        Returns:
            List of SearchResult objects.
        """
        # search() returns SearchData with .web (List[SearchResultWeb])
        # SearchResultWeb has: url, title, description
        response = self._client.search(query, limit=num_results)
        web_results = response.web or []
        return [
            SearchResult(
                url=item.url or "",
                title=item.title or "",
                content=item.description or "",
            )
            for item in web_results
        ]
