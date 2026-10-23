"""Bounded tools and deterministic discovery engine for public-web acquisition."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
import threading
from collections.abc import Sequence
from typing import Callable
from urllib.parse import urlsplit

from deepagents import create_deep_agent
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import merge_configs
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ai_internship.enrichment.profiles import PROFILES
from ai_internship.public_assets import load_prompt, project_name

from .contracts import DiscoveryCandidate, ExternalDiscoveryResult, canonicalize_url
from .providers import FetchedPage, ScrapeProvider, SearchProvider, SiteCrawler, SiteMapper

logger = logging.getLogger(__name__)

BRAINTRUST_PROJECT = project_name()

# Discovery-instruction prose (what kinds of pages to look for) lives in
# Braintrust, not here -- ELI-1309: this replaces two separately-hardcoded,
# largely-redundant instruction blocks (profiles.py's Profile.discovery_
# instructions and this module's old PROFILE_DISCOVERY_INSTRUCTIONS) that
# both got concatenated into the same prompt. Mirrors the same
# braintrust.load_prompt pattern already used for extraction/grounding
# prompts (see enrichment/profiles.py, enrichment/grounding.py).
DISCOVERY_PROMPT_SLUGS = {
    "customers": "customer-discovery-f006",
    "integrations": "integration-discovery-c2a2",
}


async def _load_discovery_prompt(slug: str):
    return await asyncio.get_running_loop().run_in_executor(
        None,
        functools.partial(load_prompt, slug=slug),
    )


PROFILE_SELECTION_CUES = {
    "customers": ("case study", "clinic", "customer", "deploy", "implement", "partner", "uses"),
    "integrations": ("api", "connect", "ehr", "emr", "integrat", "marketplace", "partner", "sync"),
}
PROFILE_SEED_TERMS = {
    "customers": (
        "customers OR clients OR implemented OR deployed OR selected OR case study OR "
        "testimonial OR newsroom OR press"
    ),
    "integrations": (
        "integration OR integrates OR API OR EHR OR EMR OR marketplace OR partner OR "
        "connected OR sync"
    ),
}

# Touched repeatedly across every vendor in a real funnel experiment
# (2026-09-16, 3 real vendors, live search API) -- LinkedIn alone showed up
# 13/8/14 times -- while contributing ZERO real selected evidence in any of
# the three. Structurally these are social networks, job boards, and app
# directories, not customer/integration evidence sources -- excluded outright
# rather than left to score low on cue-word matching, since low-scoring still
# costs a real scrape call.
EXCLUDED_HOSTS = (
    "linkedin.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "indeed.com",
    "glassdoor.com",
    "ambitionbox.com",
    "appbrain.com",
    "buildingtech.pro",
    # ELI-1541: 11+ relations from one run, none real -- elpdata invents
    # sample names, hgdata sells guessed usage as data, bebee is a job board.
    "elpdata.com",
    "hgdata.com",
    "bebee.com",
)


def _excluded_hosts() -> tuple[str, ...]:
    """Add caller-specified reference publishers to the generic exclusions."""
    configured = os.getenv("DISCOVERY_EXCLUDED_HOSTS", "").split(",")
    return EXCLUDED_HOSTS + tuple(host.strip().lower() for host in configured if host.strip())


def _is_excluded_host(url: str) -> bool:
    host = urlsplit(canonicalize_url(url)).hostname or ""
    return any(host == excluded or host.endswith(f".{excluded}") for excluded in _excluded_hosts())


def _is_own_vendor_domain(url: str, vendor_url: str) -> bool:
    """True when url's host is the vendor's own domain or a subdomain of it --
    FirstPartyAcquisition already covers this ground exhaustively; this session's
    separate search/scrape/crawl/map budget shouldn't be spent duplicating it."""
    vendor_host = urlsplit(canonicalize_url(vendor_url)).hostname or ""
    host = urlsplit(canonicalize_url(url)).hostname or ""
    if not vendor_host or not host:
        return False
    return host == vendor_host or host.endswith(f".{vendor_host}")


# Cheap look-before-you-crawl check: URL-path keywords that suggest a page is
# actually customer/integration evidence, not just any page on the site.
# crawl_site fetches real content for potentially many pages per call --
# unlike scrape_url (one page, the agent already chose it) it can otherwise
# "wander into" a site's irrelevant pages wholesale. No cue match among a
# site's own page list means don't bother crawling it at all.
CRAWL_RELEVANCE_CUES = (
    "customer",
    "client",
    "case-stud",
    "casestud",
    "testimonial",
    "press",
    "news",
    "partner",
    "integrat",
    "success",
    "story",
    "stories",
    "deploy",
    "implement",
)


def _looks_relevant_for_crawl(url: str) -> bool:
    path = urlsplit(canonicalize_url(url)).path.lower()
    return any(cue in path for cue in CRAWL_RELEVANCE_CUES)


MAX_AGENT_TOOL_OUTPUT_CHARACTERS = 20_000

DISCOVERY_SYSTEM_PROMPT_SLUG = "discovery-system-prompt-32a6"


def canonical_url(url: str) -> str:
    """Compatibility alias for the shared URL identity policy."""
    return canonicalize_url(url)


def _mentions_target(candidate: DiscoveryCandidate, target_name: str) -> bool:
    text = f"{candidate.title or ''} {candidate.snippet or ''}".lower()
    return bool(re.search(rf"(?<!\w){re.escape(target_name.lower())}(?!\w)", text))


class EvidenceJudgement(BaseModel):
    """Is this page evidence that named organisations are customers/partners of the vendor?"""

    is_evidence: bool = Field(
        description=(
            "True only if the page explicitly states that at least one NAMED organisation (not "
            "the vendor itself) uses, selected, deployed, implemented, or partnered with the "
            "vendor or its product. False for the vendor's generic marketing with no named "
            "organisation, job posts, directories/listings, unrelated pages, or pages that only "
            "mention the vendor."
        )
    )
    organisations: list[str] = Field(
        default_factory=list,
        description=(
            "Organisations the page names as customers/users/partners of the vendor. Empty if none."
        ),
    )
    why: str = Field(description="One short sentence quoting or paraphrasing the decisive passage.")


# ELI-1490: a judge at the keep decision. Paired on all 61 vendors, this cut
# kept pages 875 -> 522 and raised the share naming a golden customer from 32%
# to 48%, losing 6 of 74 hard-to-find customers (3 on appsruntheworld.com, a
# host the team had already removed as untrustworthy). It is a code step, not
# a prompt instruction: ELI-1488 measured prompt strategy being followed ~1 in
# 10 times, code always.
# One question per profile. The customers prompt asks for organisation-to-vendor
# adoption, which is not what an integrations page states -- judged with it, a
# page saying the vendor connects to a named EHR is rejected as "no customer
# named". Extraction's two profiles are HealthSystem -> Adopts -> Product and
# Product -> Integrates -> Product, so the judge gets one question per profile.
JUDGE_PROMPT_SLUGS = {
    "customers": "customer-evidence-judge-05d7",
    "integrations": "integration-evidence-judge-e9c5",
}


async def _load_judge_prompt(profile: str) -> str | None:
    """The judge's question, from Braintrust, like every other prompt we run.

    Returns None when it can't be loaded -- Braintrust unreachable, slug gone.
    The caller then skips judging and keeps the pages, which is the behaviour
    without the judge at all. Judging with an empty question would reject
    everything instead.
    """
    slug = JUDGE_PROMPT_SLUGS.get(profile)
    if slug is None:
        return None
    try:
        prompt = await asyncio.get_running_loop().run_in_executor(
            None,
            functools.partial(load_prompt, slug=slug),
        )
        question = prompt.build()["messages"][0]["content"]
    except Exception as error:  # noqa: BLE001
        logger.warning("evidence judge prompt %s unavailable: %s", slug, repr(error))
        return None
    return question or None


EVIDENCE_JUDGE_TEXT_CHARACTERS = 8_000
EVIDENCE_JUDGE_CONCURRENCY = 6


def build_evidence_judge(model):  # noqa: ANN001, ANN201
    """The judge is the discovery model at temperature 0 with structured output.

    Returns None (judge off) for a model that cannot do structured output, so a
    misconfigured model degrades to today's behaviour instead of failing startup.
    """
    if not hasattr(model, "with_structured_output"):
        logger.warning("evidence judge disabled: model %r lacks structured output", model)
        return None
    judge_model = model
    try:
        judge_model = model.bind(temperature=0)
    except Exception:  # noqa: BLE001 -- not every chat model accepts bind(temperature)
        pass
    return judge_model.with_structured_output(EvidenceJudgement, method="function_calling")


class KeptSource(BaseModel):
    url: str
    title: str | None = None
    reason: str | None = None


class KeptSources(BaseModel):
    sources: list[KeptSource] = Field(default_factory=list)


class DiscoverySession:
    """Application-owned tool budgets and fetched-page collection for an agent run."""

    def __init__(
        self,
        search_provider: SearchProvider | None = None,
        scrape_provider: ScrapeProvider | None = None,
        mapper: SiteMapper | None = None,
        crawl_provider: SiteCrawler | None = None,
        search_cap: int = 10,
        scrape_cap: int = 10,
        crawl_page_cap: int = 50,
        map_cap: int = 5,
        crawl_cap: int = 5,
        target_name: str = "",
        vendor_url: str = "",
    ):
        self.search_provider = search_provider
        self.scrape_provider = scrape_provider
        self.mapper = mapper
        self.crawl_provider = crawl_provider
        self.search_cap = search_cap
        self.scrape_cap = scrape_cap
        self.crawl_page_cap = crawl_page_cap
        self.map_cap = map_cap
        # PR #73 review (Macroscope, High), verified: crawl_calls was
        # tracked but never checked against any cap (unlike search/scrape/
        # map), so a retrying agent could trigger unbounded paid Firecrawl
        # crawls even after crawl_page_cap stopped any of them from keeping
        # new pages.
        self.crawl_cap = crawl_cap
        self.target_name = target_name
        # ELI-1462 (found mid-conversation, 2026-09-16): this is the EXTERNAL
        # discovery agent -- FirstPartyAcquisition (research/acquisition.py)
        # already crawls the vendor's own domain exhaustively (up to 1000
        # pages), separately. Nothing previously stopped this agent from
        # spending its own separate budget re-reading the vendor's own site
        # too -- pure wasted duplication of work another system already
        # does. vendor_url lets scrape_url/crawl_site refuse it outright.
        self.vendor_url = vendor_url
        self.search_calls = 0
        self.scrape_calls = 0
        self.map_calls = 0
        self.crawl_calls = 0
        self.discovered_urls: set[str] = set()
        self._provenance: dict[str, dict[str, list[str]]] = {}
        self._search_hits = {}
        self.fetched_pages: list[FetchedPage] = []
        self.scrape_failures: list[tuple[str, str]] = []
        # Found 2026-09-16: this model produces zero reasoning tokens (verified
        # directly against the raw response, not assumed) -- there's no visible
        # "why" for any tool call otherwise. Rather than depend on a specific
        # model's reasoning-mode support, force it structurally: every tool call
        # requires a `reason` argument (see _build_tools), logged here
        # unconditionally, the same way KeptSource already requires a reason
        # for final picks.
        self.action_log: list[dict] = []
        # LangChain runs all tool calls the model requests in one turn
        # concurrently (asyncio.gather -> to_thread), so "check cap, call
        # provider, increment" raced and live runs exceeded every cap (28/25
        # searches, 17/15 scrapes, 6/5 maps). Each tool reserves its slot
        # under this lock before calling the provider.
        self._lock = threading.Lock()

    def search_web(self, query: str, limit: int, reason: str = "") -> str:
        self.action_log.append(
            {"tool": "search_web", "args": {"query": query, "limit": limit}, "reason": reason}
        )
        quoted_target = f'"{self.target_name}"'
        if self.target_name and quoted_target.lower() not in query.lower():
            query = f"{quoted_target} {query}"
        # -site: exclusions where the provider supports the operator, so
        # excluded hosts don't consume result slots in the first place. The
        # code check on returned hits below stays authoritative regardless.
        provider_query = query + "".join(f" -site:{host}" for host in _excluded_hosts())
        # Budget check and slot reservation are one atomic step: LangChain
        # runs a turn's tool calls concurrently, so a separate check and
        # increment raced past the cap.
        with self._lock:
            if self.search_calls >= self.search_cap:
                return "Search budget exhausted. Finalize with discovered URLs."
            if self.search_provider is None:
                return "Search provider unavailable."
            self.search_calls += 1
            call_number = self.search_calls
        hits = self.search_provider.search(provider_query, limit)
        # The -site: operator above is best-effort (not every provider
        # supports it); this check is authoritative regardless of provider.
        hits = [hit for hit in hits if not _is_excluded_host(hit.url)]
        for hit in hits:
            canonical = canonical_url(hit.url)
            previous = self._search_hits.get(canonical)
            if previous:
                hit = hit.model_copy(
                    update={
                        "title": hit.title or previous.title,
                        "snippet": hit.snippet or previous.snippet,
                    }
                )
            self._search_hits[canonical] = hit
            self._record_discovery(
                hit.url, query=query, method="search", call_id=f"search:{call_number}"
            )
        return "\n\n".join(
            f"{hit.title or hit.url}\nURL: {hit.url}\nSnippet: {hit.snippet or '(none)'}"
            for hit in hits
        )

    def map_site(self, url: str, limit: int, reason: str = "") -> str:
        self.action_log.append(
            {"tool": "map_site", "args": {"url": url, "limit": limit}, "reason": reason}
        )
        # Found during the 2026-09-16 budget review: unlike search_web/
        # scrape_url/crawl_site, this tool never checked any cap at all --
        # one site (or the agent calling it repeatedly) could rack up
        # unlimited map calls with zero enforcement.
        if _is_own_vendor_domain(url, self.vendor_url):
            return (
                "This is the vendor's own domain -- already covered by first-party "
                "crawling, not this session's job."
            )
        with self._lock:
            if self.map_calls >= self.map_cap:
                return "Map budget exhausted. Finalize with discovered URLs."
            if self.mapper is None:
                return "Site map provider unavailable."
            self.map_calls += 1
            call_number = self.map_calls
        urls = self.mapper.map_site(url, limit)
        for item in urls:
            self._record_discovery(item, method="map", call_id=f"map:{call_number}")
        return "\n".join(urls)

    def scrape_url(self, url: str, reason: str = "") -> str:
        self.action_log.append({"tool": "scrape_url", "args": {"url": url}, "reason": reason})
        if _is_excluded_host(url):
            return "This host is excluded (not a customer/integration evidence source)."
        if _is_own_vendor_domain(url, self.vendor_url):
            return (
                "This is the vendor's own domain -- already covered by first-party "
                "crawling, not this session's job."
            )
        canonical = canonical_url(url)
        with self._lock:
            if self.scrape_calls >= self.scrape_cap:
                return "Scrape budget exhausted. Finalize with discovered URLs."
            if self.scrape_provider is None:
                return "Scrape provider unavailable."
            if canonical not in self.discovered_urls:
                return "URL was not discovered by an approved tool."
            self.scrape_calls += 1
        page = self.scrape_provider.scrape(url)
        self._collect(page)
        return page.content[:MAX_AGENT_TOOL_OUTPUT_CHARACTERS]

    def scrape_for_judge(self, url: str) -> bool:
        """Read a page for the evidence judge, outside the agent's scrape budget.

        That budget exists to stop the search loop running forever. The judge is
        not a loop -- it sees each selected page once -- and judging from a search
        snippet is exactly what it was added to stop.

        The host guards still apply: an excluded host or the vendor's own domain
        is not read here any more than it is by the agent.
        """
        if _is_excluded_host(url) or _is_own_vendor_domain(url, self.vendor_url):
            return False
        if self.scrape_provider is None:
            return False
        try:
            self._collect(self.scrape_provider.scrape(url))
        except Exception as error:  # noqa: BLE001 -- one unreadable page is not fatal
            logger.warning("judge read failed for {}: {}", url, repr(error))
            return False
        return True

    def crawl_site(self, url: str, max_depth: int, limit: int, reason: str = "") -> str:
        self.action_log.append(
            {
                "tool": "crawl_site",
                "args": {"url": url, "max_depth": max_depth, "limit": limit},
                "reason": reason,
            }
        )
        if _is_excluded_host(url):
            return "This host is excluded (not a customer/integration evidence source)."
        if _is_own_vendor_domain(url, self.vendor_url):
            return (
                "This is the vendor's own domain -- already covered by first-party "
                "crawling, not this session's job."
            )
        if self.crawl_provider is None:
            return "Crawl provider unavailable."
        # Look before wandering in: map the site's own page list first (cheap,
        # no content fetched) and only proceed if something on it actually
        # looks like customer/integration evidence. An empty map (mapper
        # unavailable, or the site genuinely has nothing else linked) doesn't
        # block the crawl -- only a real list of irrelevant-looking pages does.
        # Budget first, map second (PR #82 review, Macroscope): the look-ahead
        # is itself a paid provider call, so checking it after the cap let a
        # retrying agent keep mapping for free once the crawl budget was gone.
        with self._lock:
            if self.crawl_calls >= self.crawl_cap:
                return "Crawl budget exhausted. Finalize with discovered URLs."
        if self.mapper is not None:
            mapped_urls = self.mapper.map_site(url, 30)
            if mapped_urls and not any(_looks_relevant_for_crawl(u) for u in mapped_urls):
                return (
                    "Mapped this site first and found nothing that looks like customer/"
                    "integration evidence -- skipping the crawl to avoid wasting budget."
                )
        with self._lock:
            if self.crawl_calls >= self.crawl_cap:
                return "Crawl budget exhausted. Finalize with discovered URLs."
            remaining = self.crawl_page_cap - len(self.fetched_pages)
            self.crawl_calls += 1
            call_number = self.crawl_calls
        for page in self.crawl_provider.crawl(url, max_depth, min(limit, max(remaining, 0))):
            self._record_discovery(page.url, method="crawl", call_id=f"crawl:{call_number}")
            self._collect(page)
        return "\n".join(page.url for page in self.fetched_pages)

    def _collect(self, page: FetchedPage) -> None:
        # Called from scrape_url and crawl_site after each releases the lock,
        # so without one here two concurrent calls can both pass the cap check
        # before either appends -- the same over-cap race the tool counters are
        # locked against.
        canonical = canonical_url(page.url)
        with self._lock:
            if len(self.fetched_pages) >= self.crawl_page_cap or any(
                canonical_url(item.url) == canonical for item in self.fetched_pages
            ):
                return
            self.discovered_urls.add(canonical)
            self.fetched_pages.append(page)

    def _record_discovery(
        self, url: str, *, method: str, call_id: str, query: str | None = None
    ) -> None:
        canonical = canonical_url(url)
        self.discovered_urls.add(canonical)
        provenance = self._provenance.setdefault(
            canonical,
            {"queries": [], "discovery_methods": [], "provider_call_ids": []},
        )
        if query and query not in provenance["queries"]:
            provenance["queries"].append(query)
        if method not in provenance["discovery_methods"]:
            provenance["discovery_methods"].append(method)
        if call_id not in provenance["provider_call_ids"]:
            provenance["provider_call_ids"].append(call_id)

    def provenance_for(self, url: str) -> dict[str, list[str]]:
        return dict(
            self._provenance.get(
                canonical_url(url),
                {"queries": [], "discovery_methods": [], "provider_call_ids": []},
            )
        )

    def candidate_for(self, url: str) -> DiscoveryCandidate:
        hit = self._search_hits.get(canonical_url(url))
        return DiscoveryCandidate(
            url=url,
            title=hit.title if hit else None,
            snippet=hit.snippet if hit else None,
            **self.provenance_for(url),
        )


class DeepAgentSourceDiscoveryEngine:
    """DeepAgent source selector whose tools remain bounded by ``DiscoverySession``."""

    def __init__(
        self,
        session: DiscoverySession,
        model=None,
        agent=None,
        session_factory: Callable[[], DiscoverySession] | None = None,
        judge=None,
    ):
        self.session = session
        self._model = model
        self._provided_agent = agent
        self._session_factory = session_factory
        # Optional evidence judge (see build_evidence_judge). None = off.
        self._judge = judge
        # Building the agent needs the system prompt from Braintrust, which is
        # an async fetch -- deferred to _ensure_agent(), called from discover()
        # (already async), instead of blocking a sync constructor.
        self._agent = agent

    def _build_tools(self) -> list:
        """The four discovery tools, bound to this engine's session."""

        @tool
        async def search_web(query: str, reason: str, limit: int = 5) -> str:
            """Search the public web for candidate sources. `reason` is required: one short
            sentence on why this specific search is worth running now."""
            return await asyncio.to_thread(self.session.search_web, query, limit, reason)

        @tool
        async def map_site(url: str, reason: str, limit: int = 10) -> str:
            """List the URLs a site has (e.g. its case-study or press pages) without fetching
            their content -- use this to see what a promising site offers before deciding which
            pages are worth scraping or crawling, instead of guessing individual URLs. `reason`
            is required: one short sentence on why this site is worth mapping now. Do not call
            this on the vendor's own domain -- it's covered separately and this always refuses,
            wasting a turn."""
            return await asyncio.to_thread(self.session.map_site, url, limit, reason)

        @tool
        async def scrape_url(url: str, reason: str) -> str:
            """Fetch the full content of one URL that an approved discovery tool returned.
            `reason` is required: one short sentence on why this specific page is worth reading.
            Do not call this on the vendor's own domain -- it's covered separately and this
            always refuses, wasting a turn."""
            return await asyncio.to_thread(self.session.scrape_url, url, reason)

        @tool
        async def crawl_site(url: str, reason: str, max_depth: int = 1, limit: int = 10) -> str:
            """Fetch several of a site's linked pages at once, starting from url -- use this
            instead of repeated scrape_url calls when a site (a customer's own page, a
            newsroom, a press room) likely has more than one relevant page linked from the one
            you already found. `reason` is required: one short sentence on why this site is
            worth crawling now. Do not call this on the vendor's own domain -- it's covered
            separately and this always refuses, wasting a turn."""
            return await asyncio.to_thread(self.session.crawl_site, url, max_depth, limit, reason)

        return [search_web, map_site, scrape_url, crawl_site]

    async def _ensure_agent(self):
        if self._agent is not None:
            return self._agent
        if self._model is None:
            raise ValueError("a chat model is required when no DeepAgent instance is supplied")
        system_prompt = await _load_discovery_prompt(DISCOVERY_SYSTEM_PROMPT_SLUG)
        self._agent = create_deep_agent(
            model=self._model,
            tools=self._build_tools(),
            system_prompt=system_prompt.build()["messages"][0]["content"],
            response_format=KeptSources,
        )
        return self._agent

    async def discover(
        self,
        query: str,
        scope_url: str | None = None,
        *,
        config: RunnableConfig | None = None,
        seed_query: str | None = None,
    ) -> list[DiscoveryCandidate]:
        # Seed the bounded frontier before model selection so it has approved URLs to choose from.
        if query and not self.session.discovered_urls:
            await asyncio.to_thread(
                self.session.search_web,
                seed_query or query,
                5,
                "system seed (fixed, not the model's choice): frontier was empty, seeding "
                "before the agent starts",
            )
        elif scope_url and not self.session.discovered_urls:
            await asyncio.to_thread(
                self.session.map_site,
                scope_url,
                self.session.scrape_cap,
                "system seed (fixed, not the model's choice): frontier was empty, mapping "
                "the given scope before the agent starts",
            )
        prompt = (
            f"Find public sources for: {query}" if query else f"Discover sources under: {scope_url}"
        )
        invocation = {"messages": [{"role": "user", "content": prompt}]}
        agent = await self._ensure_agent()
        result = (
            await agent.ainvoke(invocation, config=config)
            if config is not None
            else await agent.ainvoke(invocation)
        )
        selection = KeptSources.model_validate(result.get("structured_response", {}))
        approved_urls = self.session.discovered_urls
        selected = [
            self.session.candidate_for(source.url).model_copy(
                update={"title": source.title or self.session.candidate_for(source.url).title}
            )
            for source in selection.sources
            if canonical_url(source.url) in approved_urls
        ]
        return selected

    async def discover_external(
        self,
        *,
        request,
        existing_urls: list[str],
        run_id: str,
        config: RunnableConfig | None = None,
        known_entities: Sequence[str] = (),
    ) -> ExternalDiscoveryResult:
        """Run one fresh, profile-aware external research pass.

        `known_entities` (ELI-1485): organisation names already confirmed for this
        target -- normally what first-party acquisition found on the vendor's own
        site. Handed to the agent as search *leads*, not as things to re-prove:
        pages that name one known customer (press releases, case-study roundups)
        tend to name several, so `"<known>" "<vendor>"` is the shortest route to
        the customers we still lack. Empty by default: no behaviour change.
        """
        engine = self
        if self._session_factory is not None:
            engine = DeepAgentSourceDiscoveryEngine(
                self._session_factory(),
                model=self._model,
                agent=self._provided_agent,
                session_factory=self._session_factory,
                judge=self._judge,
            )
        instruction_parts = []
        pre_agent_failures: list[tuple[str, str]] = []
        for candidate_profile in request.profiles:
            slug = DISCOVERY_PROMPT_SLUGS.get(candidate_profile)
            if slug is None:
                continue
            try:
                discovery_prompt = await _load_discovery_prompt(slug)
                instruction_parts.append(discovery_prompt.build()["messages"][0]["content"])
            except Exception as exc:
                # PR #70 review (Macroscope, High): a Braintrust outage here
                # must not abort discover_external before any search even
                # happens. NB: the reviewer's suggested fallback -- reuse
                # PROFILES[profile].discovery_instructions -- no longer
                # exists; that field was deleted in this same PR as the
                # point of the Braintrust migration, so resurrecting it would
                # undo that. Skip this profile's contribution and keep going
                # with whatever instructions the other requested profiles
                # (if any) did load.
                pre_agent_failures.append(("discovery_prompt", str(exc) or type(exc).__name__))
        instructions = " ".join(instruction_parts)
        profile = request.profiles[0] if request.profiles else ""
        prompt = (
            f"Research public sources for {request.name} at {request.url}. "
            f"{instructions} "
            "Use the available search, scrape, map, and crawl budget for breadth first, then "
            "inspect the most promising pages -- map_site or crawl_site a promising site "
            "instead of only searching and scraping one page at a time from it. "
            f"Do not return known URLs: {', '.join(existing_urls) or '(none)'}."
        )
        if known_entities:
            prompt += (
                " Already confirmed for this target (found on its own site): "
                + "; ".join(known_entities)
                + ". Do not spend budget re-proving these. Use them as leads: search "
                f'"<confirmed name>" "{request.name}" to reach third-party pages (press '
                "releases, case-study roundups, the customer's own site) that name "
                "ADDITIONAL organisations, and return pages that evidence organisations "
                "NOT in the confirmed list."
            )
        engine.session.target_name = request.name
        engine.session.vendor_url = request.url
        seed_query = f'"{request.name}" {PROFILE_SEED_TERMS.get(profile, instructions)}'
        existing = {canonical_url(url) for url in existing_urls}
        # ELI-1462, parked (2026-09-16 team review): a multi-round loop that
        # stops based on off-site-evidence signal was here, but "have we
        # found enough" isn't reliably knowable from what a round happened to
        # find -- there's no ground truth at runtime. Simplifying to what the
        # team agreed: one raised budget for every vendor, single pass, no
        # adaptive stopping. The real follow-up (ask the agent for an
        # expected relationship count up front, use that to gate a second,
        # broader-search pass) is a separate, deliberate redesign -- not
        # this loop tuned further.
        child_config = merge_configs(
            config or {},
            {
                "run_name": f"external_research:{request.name}",
                "tags": ["external-research"],
                "metadata": {
                    "target_name": request.name,
                    "run_id": run_id,
                    "profiles": request.profiles,
                    "profile_versions": {
                        profile: PROFILES[profile].version
                        for profile in request.profiles
                        if profile in PROFILES
                    },
                    "research_round": 1,
                },
            },
        )
        selected = await engine.discover(prompt, config=child_config, seed_query=seed_query)
        observed = [
            engine.session.candidate_for(url)
            for url in sorted(engine.session.discovered_urls)
            if url not in existing
        ]
        selected = [
            candidate
            for candidate in selected
            if canonical_url(candidate.url) not in existing
            and not _is_excluded_host(candidate.url)
            and not _is_own_vendor_domain(candidate.url, request.url)
        ]
        selected_by_url = {canonical_url(candidate.url): candidate for candidate in selected}
        cues = PROFILE_SELECTION_CUES.get(profile, ())
        fallback = []
        for candidate in observed:
            if len(selected_by_url) >= engine.session.scrape_cap:
                break
            if _is_excluded_host(candidate.url):
                continue
            if _is_own_vendor_domain(candidate.url, request.url):
                continue
            if _mentions_target(candidate, request.name):
                text = f"{candidate.title or ''} {candidate.snippet or ''}".lower()
                score = sum(bool(re.search(rf"(?<!\w){re.escape(cue)}", text)) for cue in cues)
                fallback.append((score, candidate.url, candidate))
        for _, _, candidate in sorted(fallback, key=lambda item: (-item[0], item[1])):
            if len(selected_by_url) >= engine.session.scrape_cap:
                break
            selected_by_url.setdefault(canonical_url(candidate.url), candidate)
        selected = list(selected_by_url.values())
        # ELI-1490: read, then judge, then keep. Half of what reached extraction
        # in the 61-vendor baseline was never read by the agent (top-up pages
        # chosen from a two-line snippet) while half the read budget went
        # unused; and only 1 in 3 kept pages named a real customer. So spend
        # the remaining reads on the unread picks, then keep only pages the
        # judge accepts as evidence. Judge failures keep the page (fail open).
        #
        # PR #82 review (Macroscope, High), verified: the customers prompt only
        # recognizes a NAMED ORGANISATION as a customer/partner, so an
        # "integrations" page ("Vendor X integrates with Acme") names no such
        # organisation and would be silently rejected. The fix is one question
        # per profile (JUDGE_PROMPTS), not skipping the profile -- skipping it
        # left integrations untested, which is what the 61-vendor A/B exposed.
        judgements: list[dict] = []
        if engine._judge is not None and profile in JUDGE_PROMPT_SLUGS:
            judgements = await engine._judge_selection(selected, request.name, request.url, profile)
            # No judgements means the judge could not run at all. Keeping the
            # agent's selection untouched is the no-judge behaviour; filtering
            # against an empty set would throw every page away.
            if judgements:
                accepted = {j["url"] for j in judgements if j["accepted"]}
                selected = [c for c in selected if canonical_url(c.url) in accepted]
        selected_urls = {canonical_url(candidate.url) for candidate in selected}
        # The required per-call reasons used to live only on the session,
        # which normal runs discard. Ride them on the result and emit them,
        # so the audit trail survives the run.
        action_log = list(engine.session.action_log)
        logger.info(
            "external discovery %s run=%s actions=%s",
            request.name,
            run_id,
            json.dumps(action_log, default=str),
        )
        return ExternalDiscoveryResult(
            observed_candidates=observed,
            selected_candidates=selected,
            prefetched_pages=[
                page
                for page in engine.session.fetched_pages
                if canonical_url(page.url) in selected_urls
            ],
            failures=pre_agent_failures + engine.session.scrape_failures,
            search_calls=engine.session.search_calls,
            scrape_calls=engine.session.scrape_calls,
            action_log=action_log,
            judgements=judgements,
        )

    async def _judge_selection(  # noqa: ANN001
        self, selected, target_name: str, vendor_url: str, profile: str
    ) -> list[dict]:
        """Read every unread pick, then judge every pick.

        The agent can select a page without opening it -- about half of what it
        keeps, historically. Those are read here before judging: deciding from a
        two-line search snippet is the thing this judge exists to stop, so the
        read is not optional and does not draw on the agent's search budget.
        """
        system_prompt = await _load_judge_prompt(profile)
        if system_prompt is None:
            logger.warning("evidence judge skipped for %s: no question available", profile)
            return []
        fetched = {canonical_url(p.url): p for p in self.session.fetched_pages}
        unread = [c for c in selected if canonical_url(c.url) not in fetched]
        for candidate in unread:
            await asyncio.to_thread(self.session.scrape_for_judge, candidate.url)
        if unread:
            fetched = {canonical_url(p.url): p for p in self.session.fetched_pages}

        semaphore = asyncio.Semaphore(EVIDENCE_JUDGE_CONCURRENCY)

        async def judge_one(candidate) -> dict:  # noqa: ANN001
            canonical = canonical_url(candidate.url)
            page = fetched.get(canonical)
            if page is not None:
                text, basis = f"{page.title or ''}\n{page.content or ''}", "page text"
            else:
                text, basis = f"{candidate.title or ''}\n{candidate.snippet or ''}", "title+snippet"
            prompt = (
                f"Vendor: {target_name} ({vendor_url})\nPage URL: {candidate.url}\n"
                f"Basis: {basis}\n\nPage text:\n{text[:EVIDENCE_JUDGE_TEXT_CHARACTERS]}"
            )
            record = {"url": canonical, "basis": basis}
            async with semaphore:
                try:
                    verdict = await self._judge.ainvoke(
                        [("system", system_prompt), ("user", prompt)]
                    )
                    record.update(
                        accepted=bool(verdict.is_evidence),
                        organisations=list(verdict.organisations),
                        why=verdict.why,
                    )
                except Exception as exc:  # noqa: BLE001
                    # Fail open: a judge outage must never empty the selection.
                    record.update(
                        accepted=True, organisations=[], why=f"judge unavailable: {exc!r}"
                    )
            return record

        return list(await asyncio.gather(*(judge_one(c) for c in selected)))
