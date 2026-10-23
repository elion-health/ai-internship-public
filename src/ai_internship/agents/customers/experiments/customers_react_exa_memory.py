"""ReAct CustomerAgent with forced checklist + search memory/dedup.

Two improvements over customers_react_exa.py:

1. Forced searches — before the agent runs freely, a fixed set of high-yield
   queries (homepage, case studies, wire services, LinkedIn) are pre-executed
   and their results injected into the agent's initial context. The agent
   never skips these sources because they run in code, not via instruction.

2. Search memory — the exa_search tool tracks all issued queries. If the
   model tries to issue a query that is semantically too similar to one
   already run (>70% word overlap after normalization), the cached result is
   returned immediately and the search budget is not consumed. This prevents
   the repetition-spiral observed with gpt-5-mini.
"""

import json
import re
from urllib.parse import urlparse

import braintrust
from exa_py import Exa
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from ai_internship.agents.customers.costs import CostTrackingBraintrustCallbackHandler
from ai_internship.agents.customers.tracing import log_customer_root_span
from ai_internship.config import Settings
from ai_internship.public_assets import load_prompt, project_name
from ai_internship.schemas import CustomerAnalysis

BRAINTRUST_PROJECT = project_name()
PROMPT_SLUG = "customers-v3"

DEFAULT_MODEL = "openai/gpt-5-mini"
DEFAULT_NUM_RESULTS = 8
DEFAULT_MAX_SEARCHES = 10

_SCHEMA_ADDENDUM = """
Output format — respond with valid JSON only, no text outside the JSON:

{
  "customers": [
    {
      "name": "string",
      "website": "string or empty string",
      "reasoning": "string",
      "date": "YYYY or null",
      "confidence": "high | medium | low",
      "supporting_evidence": [
        {"url": "string or null", "snippet": "string or null"}
      ]
    }
  ]
}

confidence levels:
  high   = found on vendor's own website/case-study page OR 2+ independent sources
  medium = mentioned once with a specific source URL
  low    = mentioned once with no URL or only inferred from generic language
"""


class SearchMemory:
    """Tracks issued queries and returns cached results for near-duplicate searches."""

    def __init__(self):
        self._cache: dict[str, str] = {}  # normalized_query -> result
        self._issued: list[str] = []
        self._hits = 0  # number of dedup cache hits

    @staticmethod
    def _normalize(query: str) -> str:
        q = re.sub(r"[^a-z0-9\s]", "", query.lower().strip())
        return q

    def _similarity(self, a: str, b: str) -> float:
        words_a = set(a.split())
        words_b = set(b.split())
        if not words_a or not words_b:
            return 0.0
        return len(words_a & words_b) / max(len(words_a), len(words_b))

    def get_cached(self, query: str) -> str | None:
        norm = self._normalize(query)
        for cached_norm, result in self._cache.items():
            if self._similarity(norm, cached_norm) >= 0.70:
                self._hits += 1
                return f"[CACHED — similar query already run] {result}"
        return None

    def record(self, query: str, result: str) -> None:
        self._issued.append(query)
        self._cache[self._normalize(query)] = result

    def summary(self) -> str:
        return f"Searches issued: {len(self._issued)}, cache hits (deduped): {self._hits}"


def _extract_domain(product_url: str) -> str:
    try:
        netloc = urlparse(product_url).netloc
        return netloc.removeprefix("www.")
    except Exception:
        return product_url


def _build_forced_queries(
    product_url: str,
    product_name: str,
    vendor_name: str | None,
    max_searches: int,
) -> list[str]:
    """Generate the mandatory checklist queries from product metadata."""
    domain = _extract_domain(product_url)
    name = vendor_name or product_name
    queries = [
        product_url,  # homepage — logos, testimonials
        f"site:{domain} case study",  # vendor case studies
        f"site:{domain} implements",  # /customer-implements-product pages
        f'site:prnewswire.com "{name}"',  # wire service announcements
        f'site:businesswire.com "{name}"',  # wire service announcements
        f'site:linkedin.com/pulse "{name}"',  # Canadian / regional announcements
    ]
    # Use at most half the search budget for forced queries
    cap = max(1, max_searches // 2)
    return queries[:cap]


def _run_forced_searches(
    exa_client: Exa,
    queries: list[str],
    num_results: int,
    memory: SearchMemory,
) -> str:
    """Execute forced queries and return a formatted results block."""
    parts = ["=== PRE-SEARCHED SOURCES (mandatory checklist) ===\n"]
    for query in queries:
        parts.append(f"Query: {query}")
        try:
            results = exa_client.search_and_contents(query, num_results=num_results, text=True)
            if not results.results:
                snippet = "No results found."
            else:
                snippets = []
                for r in results.results:
                    snippets.append(
                        f"  URL: {r.url}\n  Title: {r.title or ''}\n  {(r.text or '')[:600]}"
                    )
                snippet = "\n---\n".join(snippets)
            memory.record(query, snippet)
            parts.append(snippet)
        except Exception as e:
            parts.append(f"Error: {e}")
        parts.append("")
    parts.append("=== END PRE-SEARCHED SOURCES ===\n")
    return "\n".join(parts)


def _make_exa_tool(exa_client: Exa, num_results: int, memory: SearchMemory) -> object:
    @tool
    def exa_search(query: str) -> str:
        """Search the web using Exa neural search. Use this to find customers,
        case studies, press releases, and news about a health IT product.
        Call this multiple times with different queries to be thorough."""
        cached = memory.get_cached(query)
        if cached:
            return cached
        try:
            results = exa_client.search_and_contents(query, num_results=num_results, text=True)
        except Exception as e:
            return f"Search error: {e}"
        if not results.results:
            result_text = "No results found."
        else:
            parts = []
            for r in results.results:
                snippet = (r.text or "")[:800]
                parts.append(f"URL: {r.url}\nTitle: {r.title or ''}\n{snippet}")
            result_text = "\n\n---\n\n".join(parts)
        memory.record(query, result_text)
        return result_text

    return exa_search


def _parse_output(text: str) -> CustomerAnalysis:
    for pattern in [r"```json\s*([\s\S]*?)```", r"```\s*([\s\S]*?)```"]:
        match = re.search(pattern, text)
        if match:
            try:
                raw = json.loads(match.group(1).strip())
                if isinstance(raw, list):
                    raw = {"customers": raw}
                return CustomerAnalysis.model_validate(raw)
            except Exception:
                pass
    try:
        raw = json.loads(text.strip())
        if isinstance(raw, list):
            raw = {"customers": raw}
        return CustomerAnalysis.model_validate(raw)
    except Exception:
        pass
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return CustomerAnalysis.model_validate(json.loads(text[start : i + 1]))
                    except Exception:
                        break
    start = text.find("[")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    try:
                        return CustomerAnalysis.model_validate(
                            {"customers": json.loads(text[start : i + 1])}
                        )
                    except Exception:
                        break
    return CustomerAnalysis(customers=[])


class ReactExaMemoryAgent:
    """LangGraph ReAct agent with forced checklist searches + query deduplication.

    Before the agent's free ReAct loop begins, a fixed set of high-yield
    queries (homepage, case studies, wire services, LinkedIn) are pre-executed
    and injected into the agent's context. The agent then uses its remaining
    search budget for additional exploration, and a dedup layer prevents
    wasting budget on near-identical queries.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        settings: Settings | None = None,
        num_results: int = DEFAULT_NUM_RESULTS,
        max_searches: int = DEFAULT_MAX_SEARCHES,
    ):
        self.model = model
        self.settings = settings or Settings()
        self.num_results = num_results
        self.max_searches = max_searches
        self._exa_client = Exa(api_key=self.settings.exa_api_key)
        self._llm = ChatOpenAI(
            model=model,
            api_key=self.settings.openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
            timeout=600,
        )
        self._system_prompt = (
            load_prompt(
                project=BRAINTRUST_PROJECT,
                slug=PROMPT_SLUG,
                api_key=self.settings.braintrust_api_key,
            )
            .prompt_data.prompt.messages[0]
            .content
        ) + _SCHEMA_ADDENDUM

    def analyze(
        self,
        product_url: str,
        product_name: str,
        vendor_name: str | None = None,
    ) -> CustomerAnalysis:
        metadata = {
            "agent": "ReactExaMemoryAgent",
            "product_url": product_url,
            "product_name": product_name,
            "vendor_name": vendor_name,
            "model": self.model,
        }
        memory = SearchMemory()
        cost_tracker = CostTrackingBraintrustCallbackHandler(logger=None)

        with braintrust.start_span(name="ReactExaMemoryAgent", metadata=metadata) as span:
            # Pre-run mandatory checklist searches
            forced_queries = _build_forced_queries(
                product_url, product_name, vendor_name, self.max_searches
            )
            pre_search_context = _run_forced_searches(
                self._exa_client, forced_queries, self.num_results, memory
            )
            remaining = self.max_searches - len(forced_queries)

            vendor_info = f" sold by {vendor_name}" if vendor_name else ""
            user_message = (
                f"Find all customers for this product: {product_name}{vendor_info}\n"
                f"Product website: {product_url}\n\n"
                f"{pre_search_context}\n"
                f"The above sources have already been searched. Use your remaining "
                f"{remaining} tool calls to search additional sources not yet covered. "
                f"Do not repeat any of the queries above. After at most {remaining} "
                f"additional tool calls, STOP and output your final answer as valid "
                f"JSON only — no explanatory text outside the JSON."
            )

            exa_tool = _make_exa_tool(self._exa_client, self.num_results, memory)
            agent = create_react_agent(
                model=self._llm, tools=[exa_tool], prompt=self._system_prompt
            )

            result = agent.invoke(
                {"messages": [{"role": "user", "content": user_message}]},
                config={
                    "recursion_limit": remaining * 3 + 10,
                    "callbacks": [cost_tracker],
                },
            )

            final_message = result["messages"][-1].content
            parsed = _parse_output(final_message)
            log_customer_root_span(
                span,
                input={
                    "product_name": product_name,
                    "product_url": product_url,
                    "vendor_name": vendor_name,
                },
                metadata={**metadata, "search_memory": memory.summary()},
                output=parsed.model_dump(),
                cost_tracker=cost_tracker,
            )
            return parsed
