"""Shared dependency wiring for the stateful research graph, reused by both
the CLI entrypoint and evaluation scripts."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import braintrust
from braintrust.integrations.langchain import BraintrustCallbackHandler, set_global_handler
from braintrust.integrations.langchain.callbacks import (
    _get_metrics_from_response,
    _get_model_name_from_response,
)
from langchain_openai import ChatOpenAI
from neo4j import AsyncDriver, AsyncGraphDatabase

from ai_internship.config import Settings
from ai_internship.discovery.deep_agent import (
    DeepAgentSourceDiscoveryEngine,
    DiscoverySession,
    build_evidence_judge,
)
from ai_internship.discovery.exa_provider import ExaDiscoveryProvider
from ai_internship.discovery.providers import FirecrawlDiscoveryProvider, MergedSearchProvider
from ai_internship.discovery.store import DiscoveryStore
from ai_internship.enrichment.graph import IngestionDependencies, build_ingestion_graph
from ai_internship.enrichment.profiles import LLMAssertionExtractor
from ai_internship.graph.graphiti_client import build_graphiti_client
from ai_internship.graph.local import LocalGraphAdapter
from ai_internship.research.acquisition import FirstPartyAcquisition
from ai_internship.research.graph import ResearchDependencies, build_research_graph

if TYPE_CHECKING:
    from graphiti_core import Graphiti

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
BRAINTRUST_PROJECT = "ai-internship"


def build_root_trace_metadata(settings: Settings, inputs: dict) -> dict[str, object]:
    request = inputs.get("request", {})
    if hasattr(request, "model_dump"):
        request = request.model_dump(mode="json")
    return {
        "models": {
            "discovery": settings.discovery_llm_model,
            "ingestion": settings.ingestion_llm_model,
        },
        "limits": {
            "discovery_max_search_calls": settings.discovery_max_search_calls,
            "discovery_max_scrape_calls": settings.discovery_max_scrape_calls,
            "discovery_max_crawl_pages": settings.discovery_max_crawl_pages,
            "discovery_max_crawl_calls": settings.discovery_max_crawl_calls,
            "research_first_party_page_limit": settings.research_first_party_page_limit,
            "research_external_fetch_limit": settings.research_external_fetch_limit,
            "research_provider_timeout_seconds": settings.research_provider_timeout_seconds,
            "research_crawl_timeout_seconds": settings.research_crawl_timeout_seconds,
            "research_min_content_characters": settings.research_min_content_characters,
            "research_render_retry_wait_ms": settings.research_render_retry_wait_ms,
            "research_interactive_evidence_limit": settings.research_interactive_evidence_limit,
            "research_fetch_concurrency": settings.research_fetch_concurrency,
        },
        "request": {
            "name": request.get("name"),
            "url": request.get("url"),
            "profiles": request.get("profiles", []),
            "mode": request.get("mode", "initial"),
            "force_profiles": request.get("force_profiles", []),
        },
    }


def root_trace_output(outputs: dict) -> dict[str, object]:
    return outputs["trace_output"]


TRACE_MAX_STRING_LENGTH = 4_000


def sanitize_for_trace(value: object) -> object:
    """Shrink a LangGraph chain input/output before it's sent to Braintrust as
    a trace span. LangGraph passes the full accumulated graph state into every
    node's start/end callback, and external-discovery state can carry full
    scraped page content (FetchedPage.content/raw_content, uncapped by the
    tool-output truncation that only applies to what the discovery agent
    itself sees) -- logging that verbatim node after node is what produced
    payloads up to 48MB against Braintrust's 10MB request limit (413s seen
    during the ELI-1309 first-10-products run). This is the same failure mode
    the LANGSMITH_TRACING disable below already called out for LangSmith;
    Braintrust needed the equivalent guard.
    """
    if isinstance(value, str):
        if len(value) <= TRACE_MAX_STRING_LENGTH:
            return value
        return f"{value[:TRACE_MAX_STRING_LENGTH]}...[truncated, {len(value)} chars total]"
    if isinstance(value, dict):
        return {key: sanitize_for_trace(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_for_trace(item) for item in value]
    if hasattr(value, "model_dump"):
        return sanitize_for_trace(value.model_dump(mode="json"))
    return value


async def flush_braintrust() -> None:
    await asyncio.get_running_loop().run_in_executor(None, braintrust.flush)


class CostTrackingBraintrustCallbackHandler(BraintrustCallbackHandler):
    """Attach OpenRouter cost and stateful-research context to Braintrust spans."""

    def __init__(self, settings: Settings):
        super().__init__()
        self._settings = settings
        self._flush_tasks: set[asyncio.Task] = set()

    def on_chain_start(
        self,
        serialized,
        inputs,
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        name=None,
        metadata=None,
        **kwargs,
    ):
        if parent_run_id is None and "request" in inputs:
            metadata = {
                **(metadata or {}),
                "ai_internship": build_root_trace_metadata(self._settings, inputs),
            }
        return super().on_chain_start(
            serialized,
            sanitize_for_trace(inputs),
            run_id=run_id,
            parent_run_id=parent_run_id,
            tags=tags,
            name=name,
            metadata=metadata,
            **kwargs,
        )

    def on_chain_end(self, outputs, *, run_id, parent_run_id=None, tags=None, **kwargs):
        if not isinstance(outputs, dict) or "trace_output" not in outputs:
            return super().on_chain_end(
                sanitize_for_trace(outputs),
                run_id=run_id,
                parent_run_id=parent_run_id,
                tags=tags,
                **kwargs,
            )
        self._end_span(
            run_id,
            output=sanitize_for_trace(root_trace_output(outputs)),
            tags=tags,
            metadata=kwargs,
        )
        task = asyncio.get_running_loop().create_task(flush_braintrust())
        self._flush_tasks.add(task)
        task.add_done_callback(self._flush_tasks.discard)

    def on_llm_end(self, response, *, run_id, parent_run_id=None, tags=None, **kwargs):
        if run_id not in self.spans:
            return
        metrics = _get_metrics_from_response(response)
        cost = _extract_openrouter_cost(response)
        if cost is not None:
            metrics["cost"] = cost
        self._end_span(
            run_id,
            output=sanitize_for_trace(response),
            metrics=metrics,
            tags=tags,
            metadata={"model": _get_model_name_from_response(response), **kwargs},
        )


def setup_braintrust_tracing(settings: Settings) -> CostTrackingBraintrustCallbackHandler | None:
    """Route LangChain/LangGraph traces (and per-run OpenRouter cost) to Braintrust.

    This module imports Braintrust before Studio starts its event loop, avoiding
    its package-level synchronous schema scan during a graph invocation.
    """
    # This workflow uses Braintrust as its single trace sink; LangSmith's
    # automatic graph snapshots include full source content and can exceed API limits.
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    if not settings.braintrust_api_key:
        return None
    braintrust.init_logger(project=settings.braintrust_project, api_key=settings.braintrust_api_key)
    handler = CostTrackingBraintrustCallbackHandler(settings)
    set_global_handler(handler)
    return handler


def _extract_openrouter_cost(response) -> float | None:
    for generations in getattr(response, "generations", []) or []:
        for generation in generations:
            message = getattr(generation, "message", None)
            response_metadata = getattr(message, "response_metadata", None) or {}
            token_usage = response_metadata.get("token_usage") or {}
            cost = response_metadata.get("cost") or token_usage.get("cost")
            if isinstance(cost, int | float):
                return float(cost)
    return None


def build_stateful_research_graph(
    settings: Settings,
    *,
    first_party_class: type[FirstPartyAcquisition] = FirstPartyAcquisition,
) -> tuple[object, AsyncDriver, Graphiti]:
    """Build the compiled stateful_research graph, Neo4j driver, and Graphiti client.

    `first_party_class` exists for the external-discovery A/B harness, which needs
    the first-party crawl stubbed while everything downstream stays real. It takes
    the same constructor arguments as FirstPartyAcquisition; production never
    passes it.

    Callers are responsible for closing both the returned driver and the Graphiti
    client. The Graphiti client owns its own internal Neo4j driver that must be
    closed via ``await graphiti_client.close()``.
    """
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    provider = FirecrawlDiscoveryProvider(
        api_key=settings.firecrawl_api_key,
        timeout=settings.research_provider_timeout_seconds,
        crawl_timeout=settings.research_crawl_timeout_seconds,
    )
    search_provider = provider
    if settings.discovery_use_exa and settings.exa_api_key:
        search_provider = MergedSearchProvider(
            provider, ExaDiscoveryProvider(api_key=settings.exa_api_key)
        )
    discovery_model = ChatOpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=settings.openrouter_api_key,
        model=settings.discovery_llm_model,
    )
    ingestion_model = ChatOpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=settings.openrouter_api_key,
        model=settings.ingestion_llm_model,
    )

    def session_factory():
        return DiscoverySession(
            search_provider=search_provider,
            scrape_provider=provider,
            mapper=provider,
            # Found 2026-09-16: this was never passed at all, so crawl_site
            # was dead in production -- the agent could only ever read the
            # single page a search hit pointed to, never explore further
            # pages on that same external site. provider now implements
            # SiteCrawler (see FirecrawlDiscoveryProvider.crawl).
            crawl_provider=provider,
            search_cap=settings.discovery_max_search_calls,
            scrape_cap=settings.discovery_max_scrape_calls,
            crawl_page_cap=settings.discovery_max_crawl_pages,
            map_cap=settings.discovery_max_map_calls,
            crawl_cap=settings.discovery_max_crawl_calls,
        )

    store = DiscoveryStore(driver)
    graphiti_client = build_graphiti_client(settings)
    ingestion_graph = build_ingestion_graph(
        IngestionDependencies(
            store=store,
            graph=LocalGraphAdapter(driver, graphiti_client),
            extractor=LLMAssertionExtractor(ingestion_model),
            chat_model=ingestion_model,
            graphiti_clients=graphiti_client.clients,
        )
    )
    graph = build_research_graph(
        ResearchDependencies(
            store=store,
            first_party=first_party_class(
                store,
                provider,
                provider,
                first_party_page_limit=settings.research_first_party_page_limit,
                external_fetch_limit=settings.research_external_fetch_limit,
                provider_timeout_seconds=settings.research_provider_timeout_seconds,
                min_content_characters=settings.research_min_content_characters,
                render_retry_wait_ms=settings.research_render_retry_wait_ms,
                interactive_evidence_limit=settings.research_interactive_evidence_limit,
                fetch_concurrency=settings.research_fetch_concurrency,
            ),
            ingestion_graph=ingestion_graph,
            embedder=graphiti_client.clients.embedder,
            ingestion_model_name=settings.ingestion_llm_model,
            external_engine=DeepAgentSourceDiscoveryEngine(
                session_factory(),
                model=discovery_model,
                session_factory=session_factory,
                judge=(
                    build_evidence_judge(discovery_model)
                    if settings.discovery_evidence_judge
                    else None
                ),
            ),
        )
    )
    return graph, driver, graphiti_client
