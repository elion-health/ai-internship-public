import asyncio
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from ai_internship.config import Settings
from ai_internship.discovery.providers import FetchedPage
from ai_internship.research import runtime
from ai_internship.research.contracts import ResearchRequest
from ai_internship.research.runtime import (
    CostTrackingBraintrustCallbackHandler,
    build_root_trace_metadata,
    root_trace_output,
)


def test_root_metadata_includes_result_affecting_settings_and_request():
    settings = Settings(
        discovery_llm_model="discovery-model",
        ingestion_llm_model="ingestion-model",
        discovery_max_search_calls=1,
        discovery_max_scrape_calls=2,
        discovery_max_crawl_pages=3,
        discovery_max_crawl_calls=13,
        research_first_party_page_limit=4,
        research_external_fetch_limit=5,
        research_provider_timeout_seconds=6,
        research_crawl_timeout_seconds=60,
        research_min_content_characters=7,
        research_render_retry_wait_ms=8,
        research_interactive_evidence_limit=9,
        research_fetch_concurrency=4,
    )
    request = ResearchRequest(
        name="ExampleScribe",
        url="https://www.abridge.com/",
        profiles=["customers", "integrations"],
        force_profiles=["customers"],
    )

    assert build_root_trace_metadata(settings, {"request": request}) == {
        "models": {"discovery": "discovery-model", "ingestion": "ingestion-model"},
        "limits": {
            "discovery_max_search_calls": 1,
            "discovery_max_scrape_calls": 2,
            "discovery_max_crawl_pages": 3,
            "discovery_max_crawl_calls": 13,
            "research_first_party_page_limit": 4,
            "research_external_fetch_limit": 5,
            "research_provider_timeout_seconds": 6,
            "research_crawl_timeout_seconds": 60,
            "research_min_content_characters": 7,
            "research_render_retry_wait_ms": 8,
            "research_interactive_evidence_limit": 9,
            "research_fetch_concurrency": 4,
        },
        "request": {
            "name": "ExampleScribe",
            "url": "https://www.abridge.com/",
            "profiles": ["customers", "integrations"],
            "mode": "initial",
            "force_profiles": ["customers"],
        },
    }


def test_root_trace_output_is_the_final_extraction_summary():
    assert root_trace_output(
        {
            "status": "completed",
            "trace_output": {
                "status": "completed",
                "extractions": [{"profile": "customers", "outcome": "succeeded"}],
            },
        }
    ) == {
        "status": "completed",
        "extractions": [{"profile": "customers", "outcome": "succeeded"}],
    }


@pytest.mark.asyncio
async def test_root_completion_schedules_non_blocking_flush(monkeypatch):
    flushed = asyncio.Event()
    handler = CostTrackingBraintrustCallbackHandler(Settings())

    async def flush():
        flushed.set()

    monkeypatch.setattr(runtime, "flush_braintrust", flush)
    monkeypatch.setattr(handler, "_end_span", lambda *args, **kwargs: None)

    handler.on_chain_end(
        {"trace_output": {"status": "completed", "extractions": []}}, run_id=uuid4()
    )

    await asyncio.wait_for(flushed.wait(), timeout=0.1)


def test_chain_end_with_no_outputs_is_forwarded_to_base_handler():
    handler = CostTrackingBraintrustCallbackHandler(Settings())

    handler.on_chain_end(None, run_id=uuid4())


def test_chain_end_truncates_large_page_content_before_forwarding_to_base_handler(monkeypatch):
    # LangGraph passes the node's full return value as `outputs`. External-
    # discovery nodes return FetchedPage objects with uncapped `content`, and
    # Braintrust logs `output` verbatim -- an untruncated page here is exactly
    # what produced the 413 "Request Too Long" errors during the ELI-1309
    # first-10-products run (payloads up to 48MB against a 10MB API limit).
    handler = CostTrackingBraintrustCallbackHandler(Settings())
    captured = {}
    monkeypatch.setattr(handler, "_end_span", lambda run_id, **kwargs: captured.update(kwargs))

    huge_page = FetchedPage(url="https://example.com", content="x" * 50_000)
    handler.on_chain_end({"external_discoveries": [huge_page]}, run_id=uuid4())

    logged_content = captured["output"]["external_discoveries"][0]["content"]
    assert len(logged_content) < 50_000
    assert "truncated" in logged_content


def test_chain_start_truncates_large_page_content_before_forwarding_to_base_handler(monkeypatch):
    handler = CostTrackingBraintrustCallbackHandler(Settings())
    captured = {}
    monkeypatch.setattr(
        handler, "_start_span", lambda parent_run_id, run_id, **kwargs: captured.update(kwargs)
    )

    huge_page = FetchedPage(url="https://example.com", content="x" * 50_000)
    handler.on_chain_start({}, {"external_discoveries": [huge_page]}, run_id=uuid4())

    logged_content = captured["event"]["input"]["external_discoveries"][0]["content"]
    assert len(logged_content) < 50_000
    assert "truncated" in logged_content


def test_llm_end_truncates_large_response_content_before_forwarding_to_base_handler(monkeypatch):
    # Same failure mode as the chain_start/chain_end cases above, but for LLM
    # spans: a many-turn deep-agent conversation can carry a lot of scraped
    # content into its final response, and on_llm_end logs `response` raw --
    # this produced the one remaining 25MB payload seen on Mabel AI even
    # after the chain-level fix (ELI-1309, second first-10-products run).
    handler = CostTrackingBraintrustCallbackHandler(Settings())
    run_id = uuid4()
    handler.spans[run_id] = object()
    captured = {}
    monkeypatch.setattr(handler, "_end_span", lambda run_id, **kwargs: captured.update(kwargs))

    response = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="x" * 50_000))]])
    handler.on_llm_end(response, run_id=run_id)

    logged_content = captured["output"]["generations"][0][0]["message"]["content"]
    assert len(logged_content) < 50_000
    assert "truncated" in logged_content


def test_runtime_uses_merged_search_provider_when_exa_is_enabled(monkeypatch):
    firecrawl = object()
    exa = object()
    driver = object()
    captured = {}

    monkeypatch.setattr(runtime.AsyncGraphDatabase, "driver", lambda *args, **kwargs: driver)
    monkeypatch.setattr(runtime, "FirecrawlDiscoveryProvider", lambda **kwargs: firecrawl)
    monkeypatch.setattr(runtime, "ExaDiscoveryProvider", lambda **kwargs: exa)
    monkeypatch.setattr(
        runtime,
        "MergedSearchProvider",
        lambda *providers: captured.setdefault("providers", providers) or object(),
    )
    monkeypatch.setattr(runtime, "ChatOpenAI", lambda **kwargs: object())
    monkeypatch.setattr(runtime, "DiscoveryStore", lambda driver: object())
    graphiti = type("Graphiti", (), {"clients": type("Clients", (), {"embedder": object()})()})()
    monkeypatch.setattr(runtime, "build_graphiti_client", lambda settings: graphiti)
    monkeypatch.setattr(runtime, "LocalGraphAdapter", lambda driver, graphiti: object())
    monkeypatch.setattr(runtime, "LLMAssertionExtractor", lambda model: object())
    monkeypatch.setattr(runtime, "build_ingestion_graph", lambda dependencies: object())
    monkeypatch.setattr(runtime, "FirstPartyAcquisition", lambda *args, **kwargs: object())

    def external_engine(session, **kwargs):
        captured["search_provider"] = session.search_provider
        return object()

    monkeypatch.setattr(runtime, "DeepAgentSourceDiscoveryEngine", external_engine)
    monkeypatch.setattr(runtime, "build_research_graph", lambda dependencies: object())

    runtime.build_stateful_research_graph(
        Settings(discovery_use_exa=True, exa_api_key="exa-key", firecrawl_api_key="firecrawl-key")
    )

    assert captured["providers"] == (firecrawl, exa)
    assert captured["search_provider"] is not firecrawl
