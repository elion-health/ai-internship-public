"""ReAct + Synthesizer CustomerAgent — standalone version.

Architecture:
  Phase 1 — Forced checklist searches injected into context, then a free
             ReAct loop (customers-v3 research prompt).

  Phase 2 — A single synthesizer LLM call reads:
             (a) the raw text from the forced pre-searches,
             (b) the raw text from the ReAct loop's own searches, and
             (c) the Phase 1 ReAct output.
             It extracts any customers the ReAct loop missed and merges
             everything into one final deduplicated list.

Why two phases: the forced searches already fetch pages containing
eVisitNB, RocketDoctor, UTMHealthcare, etc. The ReAct loop often fails
to extract from pre-context. The synthesizer is a cheap single LLM call
(no Exa) that reads the raw content and pulls out what was missed.

Exa notes: this agent talks to Exa's neural search. Domain scoping uses the
include_domains parameter (NOT inline site: operators, which Exa ignores), and
the vendor homepage is fetched via get_contents (NOT by passing the URL as a
search query, which returns similar pages rather than the page itself).
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
RESEARCH_PROMPT_SLUG = "customers-v3"
SYNTHESIZER_PROMPT_SLUG = "customers-synthesizer-extraction"

DEFAULT_MODEL = "openai/gpt-5-mini"
DEFAULT_NUM_RESULTS = 8
DEFAULT_MAX_SEARCHES = 12  # free ReAct searches; the forced checklist runs on top

# Exa 400s if include_domains is combined with these categories (people allows
# LinkedIn-only). We defensively drop include_domains in those cases.
_CATEGORY_NO_DOMAIN = {"company", "people"}

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
    """Tracks issued queries; dedupes ONLY on an identical normalized
    (query, include_domains, category) key.

    Fuzzy near-duplicate matching was removed on purpose: Exa returns materially
    different results for different phrasings, so rephrasing the same target from
    a new angle is productive and must be allowed through. We only block
    byte-for-byte repeats of the same query + filters.
    """

    def __init__(self):
        self._cache: dict[str, str] = {}
        self._issued: list[str] = []
        self._hits = 0

    @staticmethod
    def _key(
        query: str, include_domains: list[str] | None = None, category: str | None = None
    ) -> str:
        q = re.sub(r"[^a-z0-9\s]", "", query.lower().strip())
        q = re.sub(r"\s+", " ", q)
        d = ",".join(sorted(include_domains)) if include_domains else ""
        return f"{q}|{d}|{category or ''}"

    def get_cached(
        self, query: str, include_domains: list[str] | None = None, category: str | None = None
    ) -> str | None:
        key = self._key(query, include_domains, category)
        if key in self._cache:
            self._hits += 1
            return f"[CACHED — identical query already run] {self._cache[key]}"
        return None

    def record(
        self,
        query: str,
        result: str,
        include_domains: list[str] | None = None,
        category: str | None = None,
    ) -> None:
        self._issued.append(query)
        self._cache[self._key(query, include_domains, category)] = result

    def summary(self) -> str:
        return f"Searches issued: {len(self._issued)}, cache hits (deduped): {self._hits}"


def _extract_domain(product_url: str) -> str:
    try:
        netloc = urlparse(product_url).netloc
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return product_url


def _search_kwargs(
    include_domains: list[str] | None, category: str | None, num_results: int
) -> tuple[dict, list[str] | None]:
    """Build search_and_contents kwargs, defensively dropping include_domains
    where the chosen category would 400. Returns (kwargs, effective_domains)."""
    eff_domains = include_domains
    if category in _CATEGORY_NO_DOMAIN and include_domains:
        linkedin_ok = category == "people" and all("linkedin.com" in d for d in include_domains)
        if not linkedin_ok:
            eff_domains = None
    kwargs: dict = {"num_results": num_results, "text": True}
    if eff_domains:
        kwargs["include_domains"] = eff_domains
    if category:
        kwargs["category"] = category
    return kwargs, eff_domains


def _build_forced_queries(
    product_url: str,
    product_name: str,
    vendor_name: str | None,
) -> list[dict]:
    """The mandatory pre-search checklist. Always runs in full, independent of
    the ReAct free-search budget. Domain scoping uses include_domains — the
    Exa-native equivalent of the old site: operators, which did nothing."""
    domain = _extract_domain(product_url)
    name = vendor_name or product_name
    return [
        {
            "query": f"customer case studies and named clients of {name}",
            "include_domains": [domain],
        },
        {
            "query": f"page describing a customer or deployment of {name}",
            "include_domains": [domain],
        },
        {
            "query": f"press release announcing a healthcare provider selecting or going live on {name}",  # noqa: E501
            "include_domains": ["prnewswire.com"],
        },
        {
            "query": f"press release announcing a hospital or clinic adopting {name}",
            "include_domains": ["businesswire.com"],
        },
        {"query": f"news announcement of a health system implementing {name}", "category": "news"},
        {
            "query": f"clinic or health network describing their use of {name}",
            "include_domains": ["linkedin.com"],
        },
    ]


def _fetch_homepage(exa_client: Exa, product_url: str) -> str:
    """Fetch the vendor homepage via get_contents. Passing the URL as a search
    query returns *similar* pages, not the page itself."""
    try:
        res = exa_client.get_contents([product_url], text=True)
        results = getattr(res, "results", None) or []
        if results and (results[0].text or ""):
            r = results[0]
            return f"URL: {product_url}\nTitle: {r.title or ''}\n{(r.text or '')[:4000]}"
        return f"URL: {product_url}\n(No content extracted.)"
    except Exception as e:
        return f"URL: {product_url}\nError fetching homepage: {e}"


def _run_forced_searches(
    exa_client: Exa,
    product_url: str,
    queries: list[dict],
    num_results: int,
    memory: SearchMemory,
) -> str:
    parts = ["=== PRE-SEARCHED SOURCES (mandatory checklist) ===\n"]
    parts.append("--- Vendor homepage (full content) ---")
    parts.append(_fetch_homepage(exa_client, product_url))
    parts.append("")

    for spec in queries:
        q = spec["query"]
        category = spec.get("category")
        kwargs, eff_domains = _search_kwargs(spec.get("include_domains"), category, num_results)

        label = q
        if eff_domains:
            label += f"  [include_domains={eff_domains}]"
        if category:
            label += f"  [category={category}]"
        parts.append(f"Query: {label}")

        try:
            results = exa_client.search_and_contents(q, **kwargs)
            if not results.results:
                snippet = "No results found."
            else:
                snippet = "\n---\n".join(
                    f"  URL: {r.url}\n  Title: {r.title or ''}\n  {(r.text or '')[:600]}"
                    for r in results.results
                )
            memory.record(q, snippet, eff_domains, category)
            parts.append(snippet)
        except Exception as e:
            parts.append(f"Error: {e}")
        parts.append("")

    parts.append("=== END PRE-SEARCHED SOURCES ===\n")
    return "\n".join(parts)


def _make_exa_tool(
    exa_client: Exa, num_results: int, memory: SearchMemory, max_calls: int = 999
) -> object:
    state = {"calls": 0}

    @tool
    def exa_search(
        query: str,
        include_domains: list[str] | None = None,
        category: str | None = None,
    ) -> str:
        """Search the web with Exa neural search for customers, case studies,
        press releases, and news about a health-IT product.

        Write `query` as a natural-language description of the page you want
        (e.g. "case study of a clinic that deployed X for patient intake"),
        NOT as keywords and NOT with site:/quotes — Exa ignores those.

        To restrict to specific sites, pass `include_domains`
        (e.g. ["prnewswire.com"]) instead of writing site: in the query text.
        Optionally set `category`: "company", "news", "people", or
        "research paper". Note: with category "company"/"people" you cannot also
        pass include_domains (it will be dropped). Vary phrasing across calls; do
        not issue an identical query twice."""
        state["calls"] += 1
        if state["calls"] > max_calls:
            return (
                "SEARCH BUDGET EXHAUSTED. Do not call this tool again. "
                "Output your final customer list as valid JSON now."
            )
        kwargs, eff_domains = _search_kwargs(include_domains, category, num_results)
        cached = memory.get_cached(query, eff_domains, category)
        if cached:
            return cached
        try:
            results = exa_client.search_and_contents(query, **kwargs)
        except Exception as e:
            return f"Search error: {e}"
        if not results.results:
            result_text = "No results found."
        else:
            result_text = "\n\n---\n\n".join(
                f"URL: {r.url}\nTitle: {r.title or ''}\n{(r.text or '')[:800]}"
                for r in results.results
            )
        memory.record(query, result_text, eff_domains, category)
        return result_text

    return exa_search


def _extract_tool_outputs(messages) -> str:
    """Pull the ReAct loop's raw search results (ToolMessages) out of the message
    list, so the synthesizer sees what the loop actually retrieved."""
    chunks = []
    for m in messages:
        if getattr(m, "type", None) == "tool" and getattr(m, "content", None):
            chunks.append(str(m.content))
    return "\n\n---\n\n".join(chunks)


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


def _load_prompt(settings: Settings, slug: str) -> str:
    p = load_prompt(
        project=BRAINTRUST_PROJECT,
        slug=slug,
        api_key=settings.braintrust_api_key,
    )
    return p.prompt_data.prompt.messages[0].content


def _run_synthesizer(
    llm: ChatOpenAI,
    system_prompt: str,
    react_output: CustomerAnalysis,
    pre_search_context: str,
    react_raw: str,
    product_name: str,
    product_url: str,
    vendor_name: str | None,
) -> CustomerAnalysis:
    vendor_info = f" sold by {vendor_name}" if vendor_name else ""
    react_json = json.dumps(
        {"customers": [c.model_dump() for c in react_output.customers]}, indent=2
    )
    user_message = (
        f"Product: {product_name}{vendor_info}\n"
        f"Product website: {product_url}\n\n"
        f"## Research agent output\n"
        f"The ReAct research agent found these customers:\n"
        f"{react_json}\n\n"
        f"## Raw source material — forced pre-searches\n"
        f"{pre_search_context}\n\n"
        f"## Raw source material — the research agent's own searches\n"
        f"These are the pages the agent retrieved during its ReAct loop. Extract "
        f"any additional customers visible here that the agent's JSON missed.\n"
        f"{react_raw or '(none captured)'}\n\n"
        f"Merge everything into one final deduplicated list. "
        f"Output the final merged customer list as valid JSON only."
    )
    response = llm.invoke(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
    )
    return _parse_output(response.content)


class ReactExaMemorySynthAgent:
    """ReAct search + synthesizer pass — standalone, no shared imports.

    Phase 1: Forced checklist searches (always run in full) injected into
             context, then a free ReAct loop driven by the customers-v3 prompt.
             `max_searches` governs ONLY the free loop.
    Phase 2: Synthesizer LLM call reads the forced-search content, the ReAct
             loop's raw search results, and Phase 1 output; extracts missed
             customers; returns the final merged list.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        settings: Settings | None = None,
        max_searches: int = DEFAULT_MAX_SEARCHES,
        num_results: int = DEFAULT_NUM_RESULTS,
        reasoning_effort: str | None = None,
    ):
        self.model = model
        self.settings = settings or Settings()
        self.max_searches = max_searches
        self.num_results = num_results
        self.reasoning_effort = reasoning_effort
        self._exa_client = Exa(api_key=self.settings.exa_api_key)

        llm_kwargs: dict = {
            "model": model,
            "api_key": self.settings.openrouter_api_key,
            "base_url": "https://openrouter.ai/api/v1",
            "timeout": 600,
        }
        if reasoning_effort is not None:
            llm_kwargs["extra_body"] = {"reasoning": {"effort": reasoning_effort}}
        self._llm = ChatOpenAI(**llm_kwargs)

        self._research_prompt = _load_prompt(self.settings, RESEARCH_PROMPT_SLUG) + _SCHEMA_ADDENDUM
        self._synthesizer_prompt = _load_prompt(self.settings, SYNTHESIZER_PROMPT_SLUG)

    def analyze(
        self,
        product_url: str,
        product_name: str,
        vendor_name: str | None = None,
    ) -> CustomerAnalysis:
        metadata = {
            "agent": "ReactExaMemorySynthAgent",
            "product_url": product_url,
            "product_name": product_name,
            "vendor_name": vendor_name,
            "model": self.model,
        }
        cost_tracker = CostTrackingBraintrustCallbackHandler(logger=None)

        with braintrust.start_span(name="ReactExaMemorySynthAgent", metadata=metadata) as span:
            # Phase 1: forced checklist (always full) + free ReAct loop
            memory = SearchMemory()
            forced_queries = _build_forced_queries(product_url, product_name, vendor_name)
            pre_search_context = _run_forced_searches(
                self._exa_client, product_url, forced_queries, self.num_results, memory
            )
            # max_searches governs ONLY the free ReAct loop
            remaining = self.max_searches

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

            exa_tool = _make_exa_tool(
                self._exa_client, self.num_results, memory, max_calls=remaining
            )
            react_agent = create_react_agent(
                model=self._llm, tools=[exa_tool], prompt=self._research_prompt
            )
            result = react_agent.invoke(
                {"messages": [{"role": "user", "content": user_message}]},
                config={
                    "recursion_limit": remaining * 3 + 10,
                    "callbacks": [cost_tracker],
                },
            )
            react_messages = result["messages"]
            raw_final = react_messages[-1].content
            print(f"[PHASE 1 RAW len={len(raw_final)}] {raw_final[:800]}")
            tool_call_count = sum(1 for m in react_messages if getattr(m, "type", None) == "tool")
            print(f"[PHASE 1 TOOL CALLS] {tool_call_count}")
            react_result = _parse_output(raw_final)
            react_raw = _extract_tool_outputs(react_messages)
            print(f"[PHASE 1 react_raw len={len(react_raw)}]")

            phase1_names = [c.name for c in react_result.customers]
            print(f"[PHASE 1 ReAct] {product_name}: {phase1_names if phase1_names else 'none'}")

            # Phase 2: synthesizer sees forced searches + ReAct raw results
            final_result = _run_synthesizer(
                llm=self._llm,
                system_prompt=self._synthesizer_prompt,
                react_output=react_result,
                pre_search_context=pre_search_context,
                react_raw=react_raw,
                product_name=product_name,
                product_url=product_url,
                vendor_name=vendor_name,
            )

            log_customer_root_span(
                span,
                input={
                    "product_name": product_name,
                    "product_url": product_url,
                    "vendor_name": vendor_name,
                },
                metadata={
                    **metadata,
                    "search_memory": memory.summary(),
                    "react_customers_found": len(react_result.customers),
                    "final_customers_found": len(final_result.customers),
                },
                output=final_result.model_dump(),
                cost_tracker=cost_tracker,
            )
            return final_result
