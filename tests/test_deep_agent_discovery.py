import concurrent.futures
import sys
import threading
import time
import types

import pytest

import ai_internship.discovery.deep_agent as deep_agent
from ai_internship.discovery.deep_agent import (
    DeepAgentSourceDiscoveryEngine,
    DiscoverySession,
)
from ai_internship.discovery.providers import FetchedPage, FirecrawlDiscoveryProvider, SearchHit


def test_fetched_pages_do_not_share_default_metadata():
    first = FetchedPage(url="https://vendor.example/first", content="first")
    second = FetchedPage(url="https://vendor.example/second", content="second")

    first.metadata["source"] = "first"

    assert second.metadata == {}


def test_firecrawl_adapter_passes_request_timeout_to_client(monkeypatch):
    captured = {}

    class FirecrawlApp:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "firecrawl", types.SimpleNamespace(FirecrawlApp=FirecrawlApp))

    FirecrawlDiscoveryProvider(api_key="key", timeout=60)

    assert captured == {"api_key": "key", "timeout": 60}


class SearchProvider:
    def search(self, query, limit):
        # search_web appends " -site:<host>" exclusions to the query before
        # calling the provider; strip them back off so the fake URL it builds
        # still matches assertions written against the bare query.
        base_query = query.split(" -site:")[0]
        return [
            SearchHit(url=f"https://example.com/{base_query}", title=base_query, snippet="result")
        ]


class CrawlProvider:
    def crawl(self, url, max_depth, limit):
        return [
            FetchedPage(url="https://vendor.example/a", content="A"),
            FetchedPage(url="https://vendor.example/b", content="B"),
        ]


class ScrapeProvider:
    def scrape(self, url):
        return FetchedPage(url=url, content="ExampleScribe source content")


def test_scrape_tool_output_is_bounded_without_truncating_retained_page():
    content = "x" * (deep_agent.MAX_AGENT_TOOL_OUTPUT_CHARACTERS + 100)
    session = DiscoverySession(
        scrape_provider=type(
            "Provider", (), {"scrape": lambda self, url: FetchedPage(url=url, content=content)}
        )()
    )
    session.discovered_urls.add("https://example.com/source")

    output = session.scrape_url("https://example.com/source")

    assert len(output) == deep_agent.MAX_AGENT_TOOL_OUTPUT_CHARACTERS
    assert len(session.fetched_pages[0].content) == len(content)


def test_tool_calls_log_their_reason():
    # ELI-1480: this model produces zero reasoning tokens (verified directly
    # against the raw response) -- there's no visible "why" for any decision
    # otherwise. Force it structurally: every tool call requires and logs a
    # `reason`, auditable independent of any model's reasoning-mode support.
    session = DiscoverySession(
        search_provider=SearchProvider(),
        mapper=MapProvider(),
        scrape_provider=ScrapeProvider(),
        crawl_provider=CrawlProvider(),
    )
    session.search_web("query", 5, reason="checking for named customers")
    session.map_site("https://vendor.example", 10, reason="site looked promising")
    session.discovered_urls.add("https://example.com/source")
    session.scrape_url("https://example.com/source", reason="direct case study hit")
    session.crawl_site("https://vendor.example", 1, 10, reason="multiple linked pages likely")

    assert [entry["reason"] for entry in session.action_log] == [
        "checking for named customers",
        "site looked promising",
        "direct case study hit",
        "multiple linked pages likely",
    ]
    assert session.action_log[0]["tool"] == "search_web"
    assert session.action_log[0]["args"] == {"query": "query", "limit": 5}


def test_agent_tools_exhaust_search_budget():
    session = DiscoverySession(search_provider=SearchProvider(), search_cap=1, scrape_cap=1)

    assert "first" in session.search_web("first", 5)
    assert (
        session.search_web("second", 5) == "Search budget exhausted. Finalize with discovered URLs."
    )


def test_scrape_url_refuses_excluded_host_without_spending_budget():
    # ELI-1483: found via the 2026-09-16 funnel experiment -- LinkedIn was
    # touched 13/8/14 times across 3 real vendors and contributed zero real
    # selected evidence in any of them. Refusing before the budget check
    # means it doesn't even cost a scrape call.
    session = DiscoverySession(scrape_provider=ScrapeProvider(), scrape_cap=5)
    session.discovered_urls.add("https://www.linkedin.com/company/example")

    output = session.scrape_url("https://www.linkedin.com/company/example")

    assert output == "This host is excluded (not a customer/integration evidence source)."
    assert session.scrape_calls == 0


def test_crawl_site_refuses_excluded_host():
    session = DiscoverySession(crawl_provider=CrawlProvider())

    output = session.crawl_site("https://facebook.com/example", max_depth=1, limit=10)

    assert output == "This host is excluded (not a customer/integration evidence source)."
    assert session.crawl_calls == 0


def test_scrape_url_refuses_vendors_own_domain_without_spending_budget():
    # ELI-1462: FirstPartyAcquisition already crawls the vendor's own domain
    # exhaustively (research/acquisition.py) -- this external-discovery
    # session's separate budget shouldn't be spent duplicating that.
    session = DiscoverySession(scrape_provider=ScrapeProvider(), scrape_cap=5)
    session.vendor_url = "https://www.dumbo.example/"
    session.discovered_urls.add("https://dumbo.example/about")

    output = session.scrape_url("https://dumbo.example/about")

    assert output == (
        "This is the vendor's own domain -- already covered by first-party "
        "crawling, not this session's job."
    )
    assert session.scrape_calls == 0


def test_crawl_site_refuses_vendors_own_domain():
    session = DiscoverySession(crawl_provider=CrawlProvider())
    session.vendor_url = "https://www.dumbo.example/"

    output = session.crawl_site("https://dumbo.example/press", max_depth=1, limit=10)

    assert output == (
        "This is the vendor's own domain -- already covered by first-party "
        "crawling, not this session's job."
    )
    assert session.crawl_calls == 0


def test_map_site_refuses_vendors_own_domain():
    session = DiscoverySession(mapper=MapProvider())
    session.vendor_url = "https://www.dumbo.example/"

    output = session.map_site("https://dumbo.example/", limit=10)

    assert output == (
        "This is the vendor's own domain -- already covered by first-party "
        "crawling, not this session's job."
    )
    assert session.map_calls == 0


class MapProvider:
    def map_site(self, url, limit):
        return [f"{url}/page-{i}" for i in range(limit)]


def test_map_site_exhausts_its_own_budget():
    # Found during the 2026-09-16 budget review: unlike search/scrape/crawl,
    # map_site never checked any cap at all -- one site (or repeated calls)
    # could rack up unlimited map calls with zero enforcement.
    session = DiscoverySession(mapper=MapProvider(), map_cap=1)

    assert "page-0" in session.map_site("https://vendor.example", 3)
    assert (
        session.map_site("https://vendor.example", 3)
        == "Map budget exhausted. Finalize with discovered URLs."
    )


def test_search_includes_snippets_and_enforces_quoted_target():
    class CapturingSearchProvider:
        def __init__(self):
            self.queries = []

        def search(self, query, limit):
            self.queries.append(query)
            return [
                SearchHit(
                    url="https://example.com/c3",
                    title="C3 Partners",
                    snippet="FirstHx is available alongside C3 EMR.",
                )
            ]

    provider = CapturingSearchProvider()
    session = DiscoverySession(search_provider=provider, target_name="FirstHx")

    output = session.search_web("integration partners", 5)

    assert len(provider.queries) == 1
    assert provider.queries[0].startswith('"FirstHx" integration partners')
    assert " -site:linkedin.com" in provider.queries[0]
    assert "C3 Partners" in output
    assert "FirstHx is available alongside C3 EMR." in output


def test_repeated_search_hit_preserves_existing_metadata():
    class RepeatedSearchProvider:
        def __init__(self):
            self.calls = 0

        def search(self, query, limit):
            self.calls += 1
            return [
                SearchHit(
                    url="https://example.com/partners",
                    title="Partners" if self.calls == 1 else None,
                    snippet="FirstHx integrates with C3 EMR" if self.calls == 1 else None,
                )
            ]

    session = DiscoverySession(search_provider=RepeatedSearchProvider())
    session.search_web("first", 5)
    session.search_web("second", 5)

    candidate = session.candidate_for("https://example.com/partners")
    assert candidate.title == "Partners"
    assert candidate.snippet == "FirstHx integrates with C3 EMR"


def test_agent_crawl_collects_every_successful_page():
    session = DiscoverySession(crawl_provider=CrawlProvider(), crawl_page_cap=2)

    session.crawl_site("https://vendor.example", max_depth=1, limit=2)

    assert [page.url for page in session.fetched_pages] == [
        "https://vendor.example/a",
        "https://vendor.example/b",
    ]


def test_firecrawl_adapter_returns_main_content_and_raw_html():
    class Client:
        def search(self, query, limit):
            return type(
                "Response",
                (),
                {
                    "web": [
                        type("Hit", (), {"url": "https://a", "title": "A", "description": "B"})()
                    ]
                },
            )()

        def scrape(self, url, formats, **kwargs):
            assert formats == ["markdown", "rawHtml"]
            assert kwargs == {"only_main_content": True, "only_clean_content": True}
            return type(
                "Response",
                (),
                {
                    "markdown": "main content",
                    "raw_html": "<html>full page</html>",
                    "metadata": {"title": "A"},
                },
            )()

    provider = FirecrawlDiscoveryProvider(client=Client())

    assert provider.search("query", 1) == [SearchHit(url="https://a", title="A", snippet="B")]
    page = provider.scrape("https://a")

    assert page.content == "main content"
    assert page.raw_content == "<html>full page</html>"


def test_firecrawl_adapter_normalizes_pydantic_metadata():
    class Metadata:
        def model_dump(self):
            return {"title": "A"}

    class Client:
        def scrape(self, url, formats, **kwargs):
            return type("Response", (), {"markdown": "content", "metadata": Metadata()})()

    page = FirecrawlDiscoveryProvider(client=Client()).scrape("https://a")

    assert page.title == "A"
    assert page.metadata == {"title": "A"}


def test_firecrawl_adapter_uses_main_content_when_client_lacks_cleaning_option():
    class Client:
        def scrape(self, url, formats, only_main_content):
            assert formats == ["markdown", "rawHtml"]
            assert only_main_content is True
            return type("Response", (), {"markdown": "content", "metadata": {}})()

    assert FirecrawlDiscoveryProvider(client=Client()).scrape("https://a").content == "content"


def test_firecrawl_adapter_reads_camel_case_raw_html():
    class Client:
        def scrape(self, url, **kwargs):
            return type(
                "Response",
                (),
                {"markdown": "content", "rawHtml": "<html>content</html>", "metadata": {}},
            )()

    assert FirecrawlDiscoveryProvider(client=Client()).scrape("https://a").raw_content == (
        "<html>content</html>"
    )


def test_firecrawl_adapter_normalizes_mapped_link_objects():
    class Client:
        def map(self, url, limit):
            link = type("Link", (), {"url": "https://a.example/page"})()
            return type("Response", (), {"links": [link]})()

    assert FirecrawlDiscoveryProvider(client=Client()).map_site("https://a.example", 10) == [
        "https://a.example/page"
    ]


def test_firecrawl_adapter_crawls_multiple_pages():
    # Found 2026-09-16: crawl() didn't exist on this provider at all, so
    # DiscoverySession's crawl_site tool was never wired to anything real in
    # production -- the external-discovery agent could only ever read the
    # single page a search hit pointed to.
    class Client:
        def crawl(self, url, *, max_discovery_depth, limit, formats, only_main_content, timeout):
            assert max_discovery_depth == 2
            assert limit == 10
            assert formats == ["markdown", "rawHtml"]
            assert only_main_content is True
            assert timeout is None
            first = type(
                "Doc",
                (),
                {
                    "markdown": "first page",
                    "raw_html": "<html>first</html>",
                    "metadata": {"title": "First", "source_url": "https://a.example/one"},
                },
            )()
            second = type(
                "Doc",
                (),
                {
                    "markdown": "second page",
                    "raw_html": "<html>second</html>",
                    "metadata": {"title": "Second", "url": "https://a.example/two"},
                },
            )()
            return type("Response", (), {"data": [first, second]})()

    pages = FirecrawlDiscoveryProvider(client=Client()).crawl(
        "https://a.example", max_depth=2, limit=10
    )

    assert [page.url for page in pages] == ["https://a.example/one", "https://a.example/two"]
    assert pages[0].content == "first page"
    assert pages[0].raw_content == "<html>first</html>"
    assert pages[0].title == "First"


@pytest.fixture(autouse=True)
def judge_prompt_without_braintrust(monkeypatch):
    """The judge's question lives in Braintrust, which tests cannot reach. Serve
    the two questions locally so these tests exercise the judge rather than the
    skip-when-unavailable path -- which has its own test below."""

    async def question(profile: str) -> str | None:
        return {
            "customers": "Decide whether the page is EVIDENCE of a customer or partner "
            "relationship: a named organisation uses the vendor.",
            "integrations": "Decide whether the page is EVIDENCE of a product integration "
            "with a NAMED other product.",
        }.get(profile)

    monkeypatch.setattr(deep_agent, "_load_judge_prompt", question)


@pytest.mark.asyncio
async def test_deep_agent_keeps_only_urls_returned_by_approved_tools():
    class Agent:
        async def ainvoke(self, input):
            return {
                "structured_response": {
                    "sources": [
                        {"url": "https://example.com/first", "reason": "approved"},
                        {"url": "https://untrusted.example/second", "reason": "reject"},
                    ]
                }
            }

    session = DiscoverySession(
        search_provider=SearchProvider(),
        scrape_provider=ScrapeProvider(),
    )
    engine = DeepAgentSourceDiscoveryEngine(session=session, agent=Agent())

    candidates = await engine.discover("first")

    assert [candidate.url for candidate in candidates] == ["https://example.com/first"]
    assert session.fetched_pages == []


@pytest.mark.asyncio
async def test_deep_agent_runs_sync_provider_calls_in_threads(monkeypatch):
    class Agent:
        async def ainvoke(self, input):
            return {"structured_response": {"sources": [{"url": "https://example.com/first"}]}}

    calls = []

    async def to_thread(function, *args, **kwargs):
        calls.append(function.__name__)
        return function(*args, **kwargs)

    monkeypatch.setattr(deep_agent.asyncio, "to_thread", to_thread)
    session = DiscoverySession(
        search_provider=SearchProvider(),
        scrape_provider=ScrapeProvider(),
    )

    await DeepAgentSourceDiscoveryEngine(session=session, agent=Agent()).discover("first")

    assert calls == ["search_web"]


@pytest.mark.asyncio
async def test_deep_agent_does_not_scrape_selected_candidates_after_selection():
    class Agent:
        async def ainvoke(self, input):
            return {"structured_response": {"sources": [{"url": "https://example.com/first"}]}}

    class FailingScrapeProvider:
        def scrape(self, url, *, wait_for_ms=0):
            raise RuntimeError("Website Not Supported")

    session = DiscoverySession(
        search_provider=SearchProvider(),
        scrape_provider=FailingScrapeProvider(),
    )

    candidates = await DeepAgentSourceDiscoveryEngine(session=session, agent=Agent()).discover(
        "first"
    )

    assert [candidate.url for candidate in candidates] == ["https://example.com/first"]
    assert session.scrape_failures == []


def test_discovery_session_records_search_provenance_for_each_url():
    session = DiscoverySession(search_provider=SearchProvider())

    session.search_web("customers", 5)

    assert session.provenance_for("https://example.com/customers") == {
        "queries": ["customers"],
        "discovery_methods": ["search"],
        "provider_call_ids": ["search:1"],
    }


@pytest.mark.asyncio
async def test_external_discovery_never_selects_an_excluded_host():
    # ELI-1483: even if the agent directly picks an excluded-host URL as a
    # "kept source" (possible from a bare search hit, without ever scraping
    # it -- scrape_url's own refusal doesn't cover that path), the final
    # selection must still drop it.
    class SearchProvider:
        def search(self, query, limit):
            return [
                SearchHit(
                    url="https://linkedin.com/company/firsthx",
                    title="FirstHx | LinkedIn",
                    snippet="FirstHx company page on LinkedIn.",
                )
            ]

    class Agent:
        async def ainvoke(self, input, config=None):
            return {
                "structured_response": {
                    "sources": [{"url": "https://linkedin.com/company/firsthx"}]
                }
            }

    request = type(
        "Request",
        (),
        {"name": "FirstHx", "url": "https://firsthx.example", "profiles": ["customers"]},
    )()
    engine = DeepAgentSourceDiscoveryEngine(
        session=DiscoverySession(search_provider=SearchProvider()),
        agent=Agent(),
    )

    result = await engine.discover_external(request=request, existing_urls=[], run_id="run-1")

    assert result.selected_candidates == []


@pytest.mark.asyncio
async def test_external_discovery_never_selects_the_vendors_own_domain():
    # ELI-1462: FirstPartyAcquisition already crawls the vendor's own domain
    # exhaustively -- this external-discovery session's separate budget
    # shouldn't spend selections on it, even if the agent picks a page
    # there directly (possible straight from a search hit, without ever
    # calling scrape_url on it -- that refusal doesn't cover this path).
    class SearchProvider:
        def search(self, query, limit):
            return [
                SearchHit(
                    url="https://firsthx.example/customers",
                    title="FirstHx customers",
                    snippet="FirstHx is used by many clinics.",
                )
            ]

    class Agent:
        async def ainvoke(self, input, config=None):
            return {
                "structured_response": {"sources": [{"url": "https://firsthx.example/customers"}]}
            }

    request = type(
        "Request",
        (),
        {"name": "FirstHx", "url": "https://firsthx.example", "profiles": ["customers"]},
    )()
    engine = DeepAgentSourceDiscoveryEngine(
        session=DiscoverySession(search_provider=SearchProvider()),
        agent=Agent(),
    )

    result = await engine.discover_external(request=request, existing_urls=[], run_id="run-1")

    assert result.selected_candidates == []


@pytest.mark.asyncio
async def test_external_discovery_returns_prefetched_selected_pages():
    class Agent:
        async def ainvoke(self, input, config=None):
            return {
                "structured_response": {"sources": [{"url": "https://newswire.example/customer"}]}
            }

    session = DiscoverySession(scrape_provider=ScrapeProvider())
    session.discovered_urls.add("https://newswire.example/customer")
    session.scrape_url("https://newswire.example/customer")
    engine = DeepAgentSourceDiscoveryEngine(session=session, agent=Agent())
    request = type(
        "Request",
        (),
        {
            "name": "Example",
            "url": "https://example.com",
            "profiles": [],
        },
    )()

    result = await engine.discover_external(request=request, existing_urls=[], run_id="run-1")

    assert [page.url for page in result.prefetched_pages] == ["https://newswire.example/customer"]


@pytest.mark.asyncio
async def test_external_discovery_keeps_target_specific_profile_hit_when_agent_misses_it():
    class IntegrationSearchProvider:
        def search(self, query, limit):
            return [
                SearchHit(
                    url="https://communitycareconnection.com/partners",
                    title="C3 Partners",
                    snippet="FirstHx supports patient intake and C3 EMR is our native integration.",
                )
            ]

    class Agent:
        async def ainvoke(self, input, config=None):
            return {"structured_response": {"sources": []}}

    request = type(
        "Request",
        (),
        {"name": "FirstHx", "url": "https://firsthx.com", "profiles": ["integrations"]},
    )()
    engine = DeepAgentSourceDiscoveryEngine(
        session=DiscoverySession(search_provider=IntegrationSearchProvider()),
        agent=Agent(),
    )

    result = await engine.discover_external(request=request, existing_urls=[], run_id="run-1")

    assert [candidate.url for candidate in result.selected_candidates] == [
        "https://communitycareconnection.com/partners"
    ]
    assert result.selected_candidates[0].snippet.startswith("FirstHx supports")


@pytest.mark.asyncio
async def test_external_discovery_fallback_uses_whole_target_and_cue_words():
    class SearchProvider:
        def search(self, query, limit):
            return [
                SearchHit(
                    url="https://example.com/unrelated",
                    title="Female capital markets",
                    snippet="Unrelated reporting",
                )
            ]

    class Agent:
        async def ainvoke(self, input, config=None):
            return {"structured_response": {"sources": []}}

    request = type(
        "Request",
        (),
        {"name": "Ema", "url": "https://ema.example", "profiles": ["integrations"]},
    )()
    engine = DeepAgentSourceDiscoveryEngine(
        session=DiscoverySession(search_provider=SearchProvider()),
        agent=Agent(),
    )

    result = await engine.discover_external(request=request, existing_urls=[], run_id="run-1")

    assert result.selected_candidates == []


@pytest.mark.asyncio
async def test_external_discovery_fallback_keeps_target_pages_without_profile_cue():
    class SearchProvider:
        def search(self, query, limit):
            return [
                SearchHit(
                    url="https://example.com/customer-list",
                    title="FirstHx customers",
                    snippet="Organizations listed by the product.",
                )
            ]

    class Agent:
        async def ainvoke(self, input, config=None):
            return {"structured_response": {"sources": []}}

    request = type(
        "Request",
        (),
        {"name": "FirstHx", "url": "https://firsthx.example", "profiles": ["customers"]},
    )()
    engine = DeepAgentSourceDiscoveryEngine(
        session=DiscoverySession(search_provider=SearchProvider()),
        agent=Agent(),
    )

    result = await engine.discover_external(request=request, existing_urls=[], run_id="run-1")

    assert [candidate.url for candidate in result.selected_candidates] == [
        "https://example.com/customer-list"
    ]


@pytest.mark.asyncio
async def test_external_discovery_prompt_pulls_profile_instructions_from_braintrust(monkeypatch):
    # The discovery-instruction prose itself now lives in Braintrust (slugs
    # customer-discovery-f006 / integration-discovery-c2a2, ELI-1309) rather
    # than in this repo -- so this asserts the wiring (discover_external loads
    # the right slug and folds its content into the prompt), not the prose.
    queries = []
    prompts = []

    async def fake_load_discovery_prompt(slug):
        return types.SimpleNamespace(
            build=lambda: {"messages": [{"content": f"BRAINTRUST INSTRUCTIONS FOR {slug}"}]}
        )

    monkeypatch.setattr(deep_agent, "_load_discovery_prompt", fake_load_discovery_prompt)

    class SearchProvider:
        def search(self, query, limit):
            queries.append(query)
            return []

    class Agent:
        async def ainvoke(self, input, config=None):
            prompts.append(input["messages"][0]["content"])
            return {"structured_response": {"sources": []}}

    request = type(
        "Request",
        (),
        {"name": "FirstHx", "url": "https://firsthx.example", "profiles": ["customers"]},
    )()
    engine = DeepAgentSourceDiscoveryEngine(
        session=DiscoverySession(search_provider=SearchProvider()),
        agent=Agent(),
    )

    await engine.discover_external(request=request, existing_urls=[], run_id="run-1")

    # Seed query is unaffected by this migration -- still built from the local
    # PROFILE_SEED_TERMS, not Braintrust.
    assert "testimonial" in queries[0]
    assert "BRAINTRUST INSTRUCTIONS FOR customer-discovery-f006" in prompts[0]


@pytest.mark.asyncio
async def test_agent_system_prompt_loads_from_braintrust_once_and_is_cached(monkeypatch):
    # The agent's own system prompt (tool-use instructions, not the per-profile
    # discovery instructions above) also lives in Braintrust now
    # (discovery-system-prompt-32a6), not hardcoded. Building the agent needs
    # this text, so it's deferred to first use inside discover() (async)
    # instead of the sync constructor, and cached across calls in one run.
    prompt_loads = []
    build_calls = []

    async def fake_load_discovery_prompt(slug):
        prompt_loads.append(slug)
        return types.SimpleNamespace(
            build=lambda: {"messages": [{"content": f"SYSTEM PROMPT FOR {slug}"}]}
        )

    class FakeAgent:
        async def ainvoke(self, input, config=None):
            return {"structured_response": {"sources": []}}

    def fake_create_deep_agent(*, model, tools, system_prompt, response_format):
        build_calls.append(system_prompt)
        return FakeAgent()

    monkeypatch.setattr(deep_agent, "_load_discovery_prompt", fake_load_discovery_prompt)
    monkeypatch.setattr(deep_agent, "create_deep_agent", fake_create_deep_agent)

    engine = DeepAgentSourceDiscoveryEngine(
        session=DiscoverySession(search_provider=SearchProvider()), model=object()
    )

    await engine.discover("first query")
    await engine.discover("second query")

    assert deep_agent.DISCOVERY_SYSTEM_PROMPT_SLUG in prompt_loads
    assert prompt_loads.count(deep_agent.DISCOVERY_SYSTEM_PROMPT_SLUG) == 1
    assert build_calls == [f"SYSTEM PROMPT FOR {deep_agent.DISCOVERY_SYSTEM_PROMPT_SLUG}"]


@pytest.mark.asyncio
async def test_external_discovery_survives_discovery_prompt_load_failure(monkeypatch):
    # PR #70 review (Macroscope, High): a Braintrust outage during
    # _load_discovery_prompt() must not abort discover_external before any
    # search even happens -- that would zero out external discovery for
    # every product any time Braintrust hiccups. NB: the reviewer's suggested
    # fix (fall back to PROFILES[profile].discovery_instructions) doesn't
    # apply -- that field was deleted in this same PR as the whole point of
    # the Braintrust migration; falling back to it would resurrect the
    # redundant local copy this ticket removed. Skip the failed profile's
    # contribution instead and keep going.
    agent_invoked = False

    async def failing_load_discovery_prompt(slug):
        raise RuntimeError("braintrust unreachable")

    monkeypatch.setattr(deep_agent, "_load_discovery_prompt", failing_load_discovery_prompt)

    class SearchProvider:
        def search(self, query, limit):
            return []

    class Agent:
        async def ainvoke(self, input, config=None):
            nonlocal agent_invoked
            agent_invoked = True
            return {"structured_response": {"sources": []}}

    request = type(
        "Request",
        (),
        {"name": "FirstHx", "url": "https://firsthx.example", "profiles": ["customers"]},
    )()
    engine = DeepAgentSourceDiscoveryEngine(
        session=DiscoverySession(search_provider=SearchProvider()),
        agent=Agent(),
    )

    result = await engine.discover_external(request=request, existing_urls=[], run_id="run-1")

    assert agent_invoked is True
    assert ("discovery_prompt", "braintrust unreachable") in result.failures


@pytest.mark.asyncio
async def test_external_discovery_skips_domain_scoped_seed_when_budget_too_low_for_agent():
    # PR #70 review (Macroscope): with a low DISCOVERY_MAX_SEARCH_CALLS, the
    # two unconditional seed calls could consume the entire search budget,
    # leaving the ReAct agent no real search opportunity of its own.
    queries = []

    class SearchProvider:
        def search(self, query, limit):
            queries.append(query)
            # A real hit populates discovered_urls, so discover()'s own
            # internal seed-if-empty check doesn't independently re-fire the
            # generic seed a second time -- keeps this test isolated to the
            # domain-scoped-seed budget guard being tested here.
            return [SearchHit(url="https://example.com/customer", title="FirstHx", snippet="x")]

    class Agent:
        async def ainvoke(self, input, config=None):
            return {"structured_response": {"sources": []}}

    request = type(
        "Request",
        (),
        {"name": "FirstHx", "url": "https://firsthx.example", "profiles": ["customers"]},
    )()
    engine = DeepAgentSourceDiscoveryEngine(
        session=DiscoverySession(search_provider=SearchProvider(), search_cap=2),
        agent=Agent(),
    )

    await engine.discover_external(request=request, existing_urls=[], run_id="run-1")

    # Only the generic seed fired -- the domain-scoped seed was skipped so
    # the agent still has at least one real search call of its own left.
    assert len(queries) == 1


@pytest.mark.asyncio
async def test_external_discovery_result_carries_the_action_log_with_reasons():
    # PR #74 review (Macroscope): the required per-call reasons lived only on
    # the session, which normal runs discard. They ride on the result now.
    class Agent:
        async def ainvoke(self, input, config=None):
            return {"structured_response": {"sources": []}}

    request = type(
        "Request",
        (),
        {"name": "FirstHx", "url": "https://firsthx.example", "profiles": ["customers"]},
    )()
    engine = DeepAgentSourceDiscoveryEngine(
        session=DiscoverySession(search_provider=SearchProvider()), agent=Agent()
    )

    result = await engine.discover_external(request=request, existing_urls=[], run_id="run-1")

    assert result.action_log, "the system seed searches must be logged"
    assert result.action_log[0]["tool"] == "search_web"
    assert all(entry["reason"] for entry in result.action_log)
    assert result.action_log == engine.session.action_log


def test_configured_reference_host_is_excluded_as_evidence(monkeypatch):
    monkeypatch.setenv("DISCOVERY_EXCLUDED_HOSTS", " Reference.Example , mirror.example ")
    assert deep_agent._is_excluded_host("https://reference.example/products/demo/customers")
    assert deep_agent._is_excluded_host("https://www.reference.example/products/demo")
    assert deep_agent._is_excluded_host("https://mirror.example/list")
    assert not deep_agent._is_excluded_host("https://notreference.example/news")

    session = DiscoverySession(scrape_provider=ScrapeProvider(), scrape_cap=5)
    session.discovered_urls.add("https://reference.example/products/demo/customers")

    output = session.scrape_url("https://reference.example/products/demo/customers")

    assert output == "This host is excluded (not a customer/integration evidence source)."
    assert session.scrape_calls == 0


def test_configured_reference_hosts_are_excluded_from_search(monkeypatch):
    monkeypatch.setenv("DISCOVERY_EXCLUDED_HOSTS", "reference.example")
    queries = []

    class Provider:
        def search(self, query, limit):
            queries.append(query)
            return [SearchHit(url="https://reference.example/list", title="List", snippet="")]

    session = DiscoverySession(search_provider=Provider())
    session.search_web("ExampleProduct customers", limit=5)
    assert " -site:reference.example" in queries[0]
    assert "https://reference.example/list" not in session.discovered_urls


class PromptCapturingAgent:
    def __init__(self):
        self.prompts = []

    async def ainvoke(self, input, config=None):
        self.prompts.append(input["messages"][0]["content"])
        return {"structured_response": {"sources": []}}


def _request():
    return type(
        "Request",
        (),
        {"name": "FirstHx", "url": "https://firsthx.example", "profiles": ["customers"]},
    )()


@pytest.mark.asyncio
async def test_known_entities_are_handed_to_the_agent_as_leads_not_targets():
    # ELI-1485: names first-party already confirmed become search leads --
    # "<known>" "<vendor>" reaches roundup pages that name additional customers.
    agent = PromptCapturingAgent()
    engine = DeepAgentSourceDiscoveryEngine(
        session=DiscoverySession(search_provider=SearchProvider()), agent=agent
    )

    await engine.discover_external(
        request=_request(),
        existing_urls=[],
        run_id="run-1",
        known_entities=["Ochsner Health", "Rush Health"],
    )

    prompt = agent.prompts[0]
    assert "Ochsner Health; Rush Health" in prompt
    assert "Do not spend budget re-proving these" in prompt
    assert '"<confirmed name>" "FirstHx"' in prompt
    assert "NOT in the confirmed list" in prompt


@pytest.mark.asyncio
async def test_prompt_is_unchanged_when_no_known_entities_are_given():
    # Default off: production behaviour unchanged.
    agent = PromptCapturingAgent()
    engine = DeepAgentSourceDiscoveryEngine(
        session=DiscoverySession(search_provider=SearchProvider()), agent=agent
    )

    await engine.discover_external(request=_request(), existing_urls=[], run_id="run-1")

    assert "Already confirmed" not in agent.prompts[0]
    assert "re-proving" not in agent.prompts[0]


class SlowCountingProvider:
    """Every call takes a moment, like a real HTTP call, so concurrent callers overlap."""

    def __init__(self):
        self.calls = 0
        self._lock = threading.Lock()

    def _tick(self):
        with self._lock:
            self.calls += 1
        time.sleep(0.02)

    def search(self, query, limit):
        self._tick()
        return [SearchHit(url=f"https://example.com/{abs(hash(query))}", title=query)]

    def scrape(self, url, *, wait_for_ms=0):
        self._tick()
        return FetchedPage(url=url, content="content")

    def map_site(self, url, limit):
        self._tick()
        return [f"{url}/page-{i}" for i in range(3)]


def _run_concurrently(fn, args_list):
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        return list(pool.map(lambda args: fn(*args), args_list))


def test_search_budget_holds_under_concurrent_tool_calls():
    # ELI-1487: LangChain runs a turn's tool calls concurrently; live runs read
    # 28/25 searches because check -> call -> increment had no lock.
    provider = SlowCountingProvider()
    session = DiscoverySession(search_provider=provider, search_cap=5)

    outputs = _run_concurrently(
        session.search_web, [(f"topic{i} subject{i}", 5, "reason") for i in range(30)]
    )

    assert session.search_calls == 5
    assert provider.calls == 5
    assert sum(1 for o in outputs if o.startswith("Search budget exhausted")) == 25


def test_scrape_budget_holds_under_concurrent_tool_calls():
    provider = SlowCountingProvider()
    session = DiscoverySession(scrape_provider=provider, scrape_cap=5)
    urls = [f"https://newswire.example/story-{i}" for i in range(30)]
    session.discovered_urls.update(deep_agent.canonical_url(u) for u in urls)

    _run_concurrently(session.scrape_url, [(u, "reason") for u in urls])

    assert session.scrape_calls == 5
    assert provider.calls == 5
    assert len(session.fetched_pages) == 5


def test_map_budget_holds_under_concurrent_tool_calls():
    provider = SlowCountingProvider()
    session = DiscoverySession(mapper=provider, map_cap=2)

    _run_concurrently(
        session.map_site, [(f"https://site{i}.example", 10, "reason") for i in range(20)]
    )

    assert session.map_calls == 2
    assert provider.calls == 2


class FakeJudge:
    """Accepts pages whose text mentions 'selected'; records what it saw."""

    def __init__(self, fail=False):
        self.seen = []
        self.system_prompts = []
        self.fail = fail

    async def ainvoke(self, messages):
        if self.fail:
            raise RuntimeError("judge down")
        self.system_prompts.append(messages[0][1])
        user = messages[1][1]
        self.seen.append(user)
        accepted = "selected" in user.lower()
        return deep_agent.EvidenceJudgement(
            is_evidence=accepted,
            organisations=["Ochsner Health"] if accepted else [],
            why="fake",
        )


class PickingAgent:
    """Returns the given URLs as the agent's picks."""

    def __init__(self, urls):
        self.urls = urls

    async def ainvoke(self, input, config=None):
        return {"structured_response": {"sources": [{"url": u} for u in self.urls]}}


class RecordingScrapeProvider:
    def __init__(self, texts):
        self.texts = texts
        self.scraped = []

    def scrape(self, url, *, wait_for_ms=0):
        self.scraped.append(url)
        return FetchedPage(url=url, content=self.texts.get(url, "nothing here"))


def _seed_session(session, urls):
    for u in urls:
        session._record_discovery(u, method="search", call_id="search:1")


@pytest.mark.asyncio
async def test_judge_reads_unread_picks_then_keeps_only_accepted_pages():
    good, bad = "https://newswire.example/ochsner", "https://newswire.example/marketing"
    scraper = RecordingScrapeProvider(
        {good: "Ochsner Health selected FirstHx.", bad: "FirstHx is great."}
    )
    session = DiscoverySession(
        search_provider=SearchProvider(), scrape_provider=scraper, scrape_cap=15
    )
    _seed_session(session, [good, bad])
    judge = FakeJudge()
    engine = DeepAgentSourceDiscoveryEngine(
        session=session, agent=PickingAgent([good, bad]), judge=judge
    )

    result = await engine.discover_external(request=_request(), existing_urls=[], run_id="run-1")

    # both picks were unread -> read before judging, so the judge saw page text
    assert set(scraper.scraped) == {good, bad}
    assert all("Basis: page text" in seen for seen in judge.seen)
    assert [c.url for c in result.selected_candidates] == [good]
    by_url = {j["url"]: j for j in result.judgements}
    assert by_url[deep_agent.canonical_url(good)]["accepted"] is True
    assert by_url[deep_agent.canonical_url(good)]["organisations"] == ["Ochsner Health"]
    assert by_url[deep_agent.canonical_url(bad)]["accepted"] is False


@pytest.mark.asyncio
async def test_judge_asks_the_integrations_question_for_the_integrations_profile():
    """One question per profile, not the customers question applied to both.

    An integrations page names a product, not an adopting organisation, so the
    customers prompt rejects it as "no customer named" -- which is how the judge
    ended up skipped for integrations entirely, leaving it untested.
    """
    url = "https://newswire.example/acme"
    scraper = RecordingScrapeProvider({url: "FirstHx integrates with Acme."})
    session = DiscoverySession(
        search_provider=SearchProvider(), scrape_provider=scraper, scrape_cap=15
    )
    _seed_session(session, [url])
    judge = FakeJudge()
    request = type(
        "Request",
        (),
        {"name": "FirstHx", "url": "https://firsthx.example", "profiles": ["integrations"]},
    )()
    engine = DeepAgentSourceDiscoveryEngine(session=session, agent=PickingAgent([url]), judge=judge)

    result = await engine.discover_external(request=request, existing_urls=[], run_id="run-1")

    assert judge.seen, "the judge must run for integrations, not be skipped"
    assert result.judgements, "integrations judgements must be recorded like customers'"
    # The question has to be the integrations one -- asked the customers question,
    # a page naming an EHR is rejected for naming no customer.
    asked = judge.system_prompts[0].lower()
    assert "product integration" in asked
    assert "customer or partner relationship" not in asked


@pytest.mark.asyncio
async def test_pages_are_kept_when_the_judge_question_cannot_be_loaded(monkeypatch):
    """Braintrust unreachable means no question, and no question means no judge.
    Keeping the agent's selection is the no-judge behaviour; filtering against
    nothing would throw every page away."""
    url = "https://newswire.example/story"
    session = DiscoverySession(
        search_provider=SearchProvider(), scrape_provider=RecordingScrapeProvider({url: "x"})
    )
    _seed_session(session, [url])

    async def unavailable(profile: str) -> None:
        return None

    monkeypatch.setattr(deep_agent, "_load_judge_prompt", unavailable)
    engine = DeepAgentSourceDiscoveryEngine(
        session=session, agent=PickingAgent([url]), judge=FakeJudge()
    )

    result = await engine.discover_external(request=_request(), existing_urls=[], run_id="run-1")

    assert [c.url for c in result.selected_candidates] == [url]


@pytest.mark.asyncio
async def test_judge_reads_every_pick_even_with_no_search_budget_left():
    """The agent's scrape budget bounds its search loop. The judge is not a loop,
    and judging from a two-line snippet is what it exists to stop, so it reads
    every selected page regardless of what the agent has already spent."""
    urls = [f"https://newswire.example/p{i}" for i in range(3)]
    scraper = RecordingScrapeProvider({u: "selected" for u in urls})
    session = DiscoverySession(
        search_provider=SearchProvider(), scrape_provider=scraper, scrape_cap=1
    )
    _seed_session(session, urls)
    judge = FakeJudge()
    engine = DeepAgentSourceDiscoveryEngine(session=session, agent=PickingAgent(urls), judge=judge)

    result = await engine.discover_external(request=_request(), existing_urls=[], run_id="run-1")

    assert len(scraper.scraped) == 3
    assert session.scrape_calls == 0  # the agent's budget is untouched
    assert [j["basis"] for j in result.judgements] == ["page text"] * 3


@pytest.mark.asyncio
async def test_judge_failure_keeps_the_page():
    url = "https://newswire.example/story"
    session = DiscoverySession(
        search_provider=SearchProvider(), scrape_provider=RecordingScrapeProvider({url: "x"})
    )
    _seed_session(session, [url])
    engine = DeepAgentSourceDiscoveryEngine(
        session=session, agent=PickingAgent([url]), judge=FakeJudge(fail=True)
    )

    result = await engine.discover_external(request=_request(), existing_urls=[], run_id="run-1")

    assert [c.url for c in result.selected_candidates] == [url]
    assert result.judgements[0]["accepted"] is True
    assert "judge unavailable" in result.judgements[0]["why"]


@pytest.mark.asyncio
async def test_no_judge_means_no_judging_and_no_extra_reads():
    url = "https://newswire.example/story"
    scraper = RecordingScrapeProvider({url: "x"})
    session = DiscoverySession(search_provider=SearchProvider(), scrape_provider=scraper)
    _seed_session(session, [url])
    engine = DeepAgentSourceDiscoveryEngine(session=session, agent=PickingAgent([url]))

    result = await engine.discover_external(request=_request(), existing_urls=[], run_id="run-1")

    assert scraper.scraped == []
    assert result.judgements == []
    assert [c.url for c in result.selected_candidates] == [url]
