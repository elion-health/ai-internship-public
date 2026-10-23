"""Abstract interface for web search providers."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class SearchResult:
    url: str
    title: str
    content: str


@runtime_checkable
class SearchProvider(Protocol):
    def search(self, query: str, num_results: int = 10) -> list[SearchResult]: ...
