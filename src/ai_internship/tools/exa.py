"""Exa search provider."""

from exa_py import Exa

from ai_internship.tools.base import SearchProvider, SearchResult


class ExaSearchProvider(SearchProvider):
    """Web search via Exa neural search API."""

    def __init__(self, api_key: str):
        self._client = Exa(api_key=api_key)

    def search(self, query: str, num_results: int = 10) -> list[SearchResult]:
        """Search the web and return results with full text content.

        Args:
            query: Search query string.
            num_results: Number of results to return.

        Returns:
            List of SearchResult objects.
        """
        response = self._client.search_and_contents(query, num_results=num_results, text=True)
        return [
            SearchResult(
                url=r.url,
                title=r.title or "",
                content=r.text or "",
            )
            for r in response.results
        ]
