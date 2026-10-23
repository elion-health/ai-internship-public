import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


class Settings(BaseModel):
    """Settings loaded from environment variables."""

    braintrust_api_key: str | None = Field(default_factory=lambda: os.getenv("BRAINTRUST_API_KEY"))
    braintrust_project: str = Field(
        default_factory=lambda: os.getenv("BRAINTRUST_PROJECT", "ai-internship-public")
    )
    openrouter_api_key: str | None = Field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY"))
    langchain_api_key: str | None = Field(default_factory=lambda: os.getenv("LANGCHAIN_API_KEY"))
    langchain_project: str = Field(
        default_factory=lambda: os.getenv("BRAINTRUST_PROJECT", "ai-internship-public")
    )
    langsmith_api_key: str | None = Field(default_factory=lambda: os.getenv("LANGSMITH_API_KEY"))
    langsmith_project: str = Field(
        default_factory=lambda: os.getenv("LANGSMITH_PROJECT", "ai-internship")
    )
    llm_model: str = Field(default_factory=lambda: os.getenv("LLM_MODEL", "openai/gpt-5.4-mini"))
    discovery_llm_model: str = Field(
        default_factory=lambda: (
            os.getenv("DISCOVERY_LLM_MODEL") or os.getenv("LLM_MODEL", "openai/gpt-5.4-mini")
        )
    )
    ingestion_llm_model: str = Field(
        default_factory=lambda: (
            os.getenv("INGESTION_LLM_MODEL") or os.getenv("LLM_MODEL", "openai/gpt-5.4-mini")
        )
    )
    exa_api_key: str | None = Field(default_factory=lambda: os.getenv("EXA_API_KEY"))
    firecrawl_api_key: str | None = Field(default_factory=lambda: os.getenv("FIRECRAWL_API_KEY"))
    discovery_use_exa: bool = Field(
        default_factory=lambda: os.getenv("DISCOVERY_USE_EXA", "false").lower() == "true"
    )
    anthropic_api_key: str | None = Field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    neo4j_uri: str = Field(default_factory=lambda: os.getenv("NEO4J_URI", "bolt://localhost:7688"))
    neo4j_user: str = Field(default_factory=lambda: os.getenv("NEO4J_USER", "neo4j"))
    neo4j_password: str = Field(
        default_factory=lambda: os.getenv("NEO4J_PASSWORD", "local-research-password")
    )
    # ELI-1462: one raised ceiling for every vendor (was 10/10), instead of a
    # fixed per-vendor budget guessed ahead of time from a stale audit
    # snapshot. A real adaptive version (ask the agent for an expected
    # relationship count, use that to gate a second search pass) is a
    # deliberate follow-up -- parked for now per 2026-09-16 team review, not
    # built speculatively in the meantime.
    discovery_max_search_calls: int = Field(
        default_factory=lambda: int(os.getenv("DISCOVERY_MAX_SEARCH_CALLS", "25"))
    )
    discovery_max_scrape_calls: int = Field(
        default_factory=lambda: int(os.getenv("DISCOVERY_MAX_SCRAPE_CALLS", "15"))
    )
    discovery_max_crawl_pages: int = Field(
        default_factory=lambda: int(os.getenv("DISCOVERY_MAX_CRAWL_PAGES", "50"))
    )
    # Found during the 2026-09-16 budget review: DiscoverySession.map_site
    # had no cap at all before this -- unlike search/scrape/crawl, it never
    # checked a budget, so one site (or repeated calls) could rack up
    # unlimited map calls with zero enforcement.
    discovery_max_map_calls: int = Field(
        default_factory=lambda: int(os.getenv("DISCOVERY_MAX_MAP_CALLS", "5"))
    )
    # PR #73 review (Macroscope, High), verified: crawl_calls was tracked but
    # never checked against a cap -- crawl_page_cap only bounds pages kept
    # from a single call, not how many times crawl_site itself can be
    # called, so a retrying agent could trigger unbounded paid Firecrawl
    # crawls.
    discovery_max_crawl_calls: int = Field(
        default_factory=lambda: int(os.getenv("DISCOVERY_MAX_CRAWL_CALLS", "5"))
    )
    # Optional read-then-judge step before retaining externally discovered pages.
    # Off by default: measured benefits were narrow relative to run-to-run noise.
    discovery_evidence_judge: bool = Field(
        default_factory=lambda: os.getenv("DISCOVERY_EVIDENCE_JUDGE", "false").lower() == "true"
    )
    # ELI-1308: was 50 -- this same number caps both Firecrawl's /map discovery call
    # AND how many discovered pages get fetched, so a low value silently truncates
    # candidate discovery before real evidence pages (case studies, integrations
    # pages) are ever seen. Bumped to 1000 as a brute-force validation step: none of
    # the 7 known vendors has more than 948 pages, so this fetches their full site
    # rather than an arbitrary, unprioritized slice of it.
    research_first_party_page_limit: int = Field(
        default_factory=lambda: int(os.getenv("RESEARCH_FIRST_PARTY_PAGE_LIMIT", "1000"))
    )
    research_external_fetch_limit: int = Field(
        default_factory=lambda: int(os.getenv("RESEARCH_EXTERNAL_FETCH_LIMIT", "10"))
    )
    research_provider_timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("RESEARCH_PROVIDER_TIMEOUT_SECONDS", "60"))
    )
    # PR #73 review (Macroscope, High), verified against the Firecrawl SDK
    # source: crawl() takes its own overall job timeout (default None --
    # unbounded), entirely separate from research_provider_timeout_seconds,
    # which only bounds the FirecrawlApp client's individual HTTP requests.
    # Without this, a stuck Firecrawl crawl job could hang the run forever.
    # A crawl can cover many pages sequentially, so it gets a generous
    # budget of its own rather than reusing the 60s single-request timeout.
    research_crawl_timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("RESEARCH_CRAWL_TIMEOUT_SECONDS", "600"))
    )
    research_min_content_characters: int = Field(
        default_factory=lambda: int(os.getenv("RESEARCH_MIN_CONTENT_CHARACTERS", "200"))
    )
    research_render_retry_wait_ms: int = Field(
        default_factory=lambda: int(os.getenv("RESEARCH_RENDER_RETRY_WAIT_MS", "5000"))
    )
    research_interactive_evidence_limit: int = Field(
        default_factory=lambda: int(os.getenv("RESEARCH_INTERACTIVE_EVIDENCE_LIMIT", "5"))
    )
    # Pages within one target are fetched this many at a time. They are independent
    # of each other, and each one is almost entirely network wait, so the serial
    # fetch this replaces spent a measured 12.6 hours on 2,437 vendor pages.
    research_fetch_concurrency: int = Field(
        default_factory=lambda: int(os.getenv("RESEARCH_FETCH_CONCURRENCY", "8"))
    )
