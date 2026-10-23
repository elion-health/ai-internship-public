"""Multi-agent CustomerAgent: orchestrator + 4 parallel sub-agents, each with fresh context.

Architecture:
  Python orchestrator spawns 4 ReAct sub-agents concurrently, each covering one
  slice of the v3 search strategy:
    1. Vendor site       — site:[vendor-domain] queries (v3 Steps 1-2)
    2. Wire services     — prnewswire / businesswire / globenewswire (v3 Step 3)
    3. LinkedIn & news   — LinkedIn Pulse + product announcement searches (v3 Step 4)
    4. Expanded          — broad health-system + fallback searches (v3 Step 5)

  Each sub-agent has a completely fresh context window — max ~4 searches × 8 results
  × 800 chars ≈ 25K chars. The orchestrator merges all four lists and deduplicates.

Why this fixes the single-agent context overflow:
  Some products concentrate their evidence on a few vendor pages.
  The single ReAct agent accumulated 15 searches
  of full snippets in one context, hit GPT-5's output limit mid-JSON, and returned
  empty results. Here, each sub-agent's context stays small regardless of product.
"""

import asyncio
import json
import re

import braintrust
from exa_py import Exa
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from ai_internship.agents.customers.tracing import log_customer_root_span
from ai_internship.config import Settings
from ai_internship.public_assets import load_prompt, project_name
from ai_internship.schemas import CustomerAnalysis

BRAINTRUST_PROJECT = project_name()
SUB_AGENT_PROMPT_SLUG = "customers-v3"
SYNTH_PROMPT_SLUG = "customers-synthesizer-v6"

DEFAULT_MODEL = "openai/gpt-5"
DEFAULT_NUM_RESULTS = 8
DEFAULT_MAX_SEARCHES_PER_AGENT = 4  # 4 agents × 4 = 16 total searches

# Each tuple: (label, search focus description given to the sub-agent in user message)
_SUB_AGENT_TASKS = [
    (
        "vendor-site",
        "You MUST search ONLY the vendor's own website. Use queries like:\n"
        "  site:[vendor-domain] case study\n"
        "  site:[vendor-domain] customers\n"
        "  site:[vendor-domain] press release\n"
        '  site:[vendor-domain] "trusted by"\n'
        "  site:[vendor-domain] success story\n"
        "  site:[vendor-domain] testimonial\n"
        "  site:[vendor-domain] news\n"
        "These map to Steps 1-2 of the search strategy in your instructions.",
    ),
    (
        "wire-services",
        "You MUST search ONLY press release wires. Use queries like:\n"
        '  site:prnewswire.com "[product name]"\n'
        '  site:prnewswire.com "[vendor name]"\n'
        '  site:businesswire.com "[product name]"\n'
        '  site:businesswire.com "[vendor name]"\n'
        '  site:globenewswire.com "[vendor name]"\n'
        "These map to Step 3 of the search strategy in your instructions.",
    ),
    (
        "linkedin-news",
        "You MUST search ONLY LinkedIn Pulse and general news/announcements. Use queries like:\n"
        '  site:linkedin.com/pulse "[vendor name]"\n'
        '  "[product name]" selects\n'
        '  "[product name]" deployment\n'
        '  "[product name]" go-live\n'
        '  "[vendor name]" customer announcement\n'
        '  "[product name]" case study\n'
        "These map to Step 4 of the search strategy in your instructions.",
    ),
    (
        "expanded",
        "You MUST search broadly for customer mentions not covered by vendor site or press releases. "  # noqa: E501
        "Use queries like:\n"
        '  "[vendor name]" customers\n'
        '  "[vendor name]" clients\n'
        '  "[vendor name]" "health system"\n'
        '  "[vendor name]" "health plan"\n'
        '  "[product name]" "health system" OR "hospital" OR "clinic"\n'
        '  site:g2.com "[vendor name]"\n'
        "These map to Step 5 of the search strategy in your instructions.",
    ),
]


def _make_exa_tool(exa_client: Exa, num_results: int):
    @tool
    def exa_search(query: str) -> str:
        """Search the web using Exa neural search. Use this to find customers,
        case studies, press releases, and news about a health IT product.
        Call this multiple times with different queries to be thorough."""
        results = exa_client.search_and_contents(query, num_results=num_results, text=True)
        if not results.results:
            return "No results found."
        parts = []
        for r in results.results:
            snippet = (r.text or "")[:800]
            parts.append(f"URL: {r.url}\nTitle: {r.title or ''}\n{snippet}")
        return "\n\n---\n\n".join(parts)

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
    # Fallback: synthesizer may return a bare JSON array
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
                        )  # noqa: E501
                    except Exception:
                        break
    return CustomerAnalysis(customers=[])


async def _synthesize(
    llm: ChatOpenAI,
    analyses: list[CustomerAnalysis],
    labels: list[str],
    product_name: str,
    vendor_name: str | None,
    synth_prompt: str,
) -> CustomerAnalysis:
    """LLM synthesizer (Scatter-Gather reduce step).

    Receives all sub-agent outputs as JSON, applies v4 synthesizer prompt
    (full classification rules, no search strategy), and returns one clean
    deduplicated CustomerAnalysis. Single LLM call, no tools.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    sections = []
    for label, analysis in zip(labels, analyses):
        customers_json = json.dumps(
            [
                {
                    "name": c.name,
                    "website": c.website,
                    "reasoning": c.reasoning,
                    "date": c.date,
                    "supporting_evidence": [
                        {"url": e.url, "snippet": e.snippet} for e in c.supporting_evidence
                    ],
                }
                for c in analysis.customers
            ],
            indent=2,
        )
        sections.append(
            f"=== Agent: {label} ({len(analysis.customers)} candidates) ===\n{customers_json}"
        )  # noqa: E501

    vendor_info = f" by {vendor_name}" if vendor_name else ""
    user_message = (
        f"Product: {product_name}{vendor_info}\n\n"
        f"The following 4 parallel research agents each searched a different source "
        f"and returned these customer candidates:\n\n"
        + "\n\n".join(sections)
        + "\n\nSynthesize these into a single deduplicated customer list. "
        "Apply all classification rules from your instructions. "
        "Output valid JSON only — no text outside the JSON."
    )

    response = await llm.ainvoke(
        [
            SystemMessage(content=synth_prompt),
            HumanMessage(content=user_message),
        ]
    )
    result = _parse_output(response.content)
    names = [c.name for c in result.customers]
    print(f"\n[SYNTHESIZER] {len(result.customers)} customers after synthesis: {names}")
    return result


async def _run_sub_agent(
    llm: ChatOpenAI,
    exa_client: Exa,
    num_results: int,
    max_searches: int,
    product_url: str,
    product_name: str,
    vendor_name: str | None,
    label: str,
    focus: str,
    system_prompt: str,
) -> CustomerAnalysis:
    vendor_info = f" sold by {vendor_name}" if vendor_name else ""
    user_message = (
        f"Find customers for: {product_name}{vendor_info}\n"
        f"Product website: {product_url}\n\n"
        f"YOU ARE ONE OF 4 PARALLEL AGENTS each covering a different search source. "
        f"Your assigned source:\n\n{focus}\n\n"
        f"Do NOT search outside your assigned source — other agents cover the rest. "
        f"Use at most {max_searches} searches total. "
        f"Apply all classification rules from your instructions. "
        f"After your final search, output your findings as valid JSON only — "
        f"no text outside the JSON."
    )

    exa_tool = _make_exa_tool(exa_client, num_results)
    agent = create_react_agent(model=llm, tools=[exa_tool], prompt=system_prompt)

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": user_message}]},
        config={"recursion_limit": 100},
    )

    final_message = result["messages"][-1].content
    parsed = _parse_output(final_message)
    names = [c.name for c in parsed.customers]
    print(f"\n[{label.upper()}] {len(parsed.customers)} customers: {names}")
    return parsed


class ReactExaMultiAgentCustomerAgent:
    """Orchestrator that runs 4 parallel ReAct+Exa sub-agents, each with fresh context.

    Each sub-agent covers one slice of the v3 search strategy so no single agent
    accumulates enough search results to overflow GPT-5's output token limit.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        settings: Settings | None = None,
        num_results: int = DEFAULT_NUM_RESULTS,
        max_searches_per_agent: int = DEFAULT_MAX_SEARCHES_PER_AGENT,
    ):
        self.model = model
        self.settings = settings or Settings()
        self.num_results = num_results
        self.max_searches_per_agent = max_searches_per_agent
        self._exa_client = Exa(api_key=self.settings.exa_api_key)
        self._llm = ChatOpenAI(
            model=model,
            api_key=self.settings.openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
            timeout=600,
        )
        api_key = self.settings.braintrust_api_key
        self._sub_agent_prompt = (
            load_prompt(
                project=BRAINTRUST_PROJECT,
                slug=SUB_AGENT_PROMPT_SLUG,
                api_key=api_key,
            )
            .prompt_data.prompt.messages[0]
            .content
        )
        self._synth_prompt = (
            load_prompt(
                project=BRAINTRUST_PROJECT,
                slug=SYNTH_PROMPT_SLUG,
                api_key=api_key,
            )
            .prompt_data.prompt.messages[0]
            .content
        )

    def analyze(
        self,
        product_url: str,
        product_name: str,
        vendor_name: str | None = None,
    ) -> CustomerAnalysis:
        metadata = {
            "agent": "ReactExaMultiAgentCustomerAgent",
            "product_url": product_url,
            "product_name": product_name,
            "vendor_name": vendor_name,
            "model": self.model,
        }
        with braintrust.start_span(
            name="ReactExaMultiAgentCustomerAgent", metadata=metadata
        ) as span:
            result = asyncio.run(self._analyze_async(product_url, product_name, vendor_name))
            log_customer_root_span(
                span,
                input={
                    "product_name": product_name,
                    "product_url": product_url,
                    "vendor_name": vendor_name,
                },  # noqa: E501
                metadata=metadata,
                output=result.model_dump(),
            )
            return result

    async def _analyze_async(
        self,
        product_url: str,
        product_name: str,
        vendor_name: str | None,
    ) -> CustomerAnalysis:
        tasks = [
            _run_sub_agent(
                self._llm,
                self._exa_client,
                self.num_results,
                self.max_searches_per_agent,
                product_url,
                product_name,
                vendor_name,
                label,
                focus,
                self._sub_agent_prompt,
            )
            for label, focus in _SUB_AGENT_TASKS
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid = [r for r in results if isinstance(r, CustomerAnalysis)]
        labels = [
            label
            for (label, _), r in zip(_SUB_AGENT_TASKS, results)
            if isinstance(r, CustomerAnalysis)
        ]  # noqa: E501
        return await _synthesize(
            self._llm, valid, labels, product_name, vendor_name, self._synth_prompt
        )  # noqa: E501
