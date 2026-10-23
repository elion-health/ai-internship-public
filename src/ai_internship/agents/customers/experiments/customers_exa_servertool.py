"""CustomerAgent using LangGraph + Exa via OpenRouter server tool.

Architecture:
  LangGraph prebuilt ReAct agent (create_react_agent)
  Model:  GPT-5 via OpenRouter, with openrouter:web_search bound natively
  Search: Exa via OpenRouter server tool — executes server-side within the
          model's own reasoning call, not in a separate LLM call.

Why this is better than customers_react_exa_openrouter.py:
  Old approach: exa_search tool wraps a second search_llm.invoke() call.
    15 searches = 15 reasoning calls + 15 search calls = 30 LLM calls total.

  This approach: openrouter:web_search is bound to the reasoning LLM itself.
    OpenRouter executes the search server-side within the same request.
    15 searches ≈ 15 reasoning calls (no extra calls). Search is free compute.

ELI-951 E0 fix (2026-07-14): the extra_body previously sent engine/
max_total_results as siblings of "type", which OpenRouter silently ignores —
they must be nested under a "parameters" key per OpenRouter's current
openrouter:web_search server-tool docs (the flat/plugins shapes are
deprecated). Unfixed, the model fell back to its own native web search
(OpenAI's "web.run") instead of Exa, and the "cap" never applied — verified
live: 27 uncapped native search requests, $0.31/product. Fixed and verified:
genuine Exa, cap respected, ~10x cheaper. See docs/e0-servertool-report.md.

max_searches / recursion_limit no longer bound anything meaningful here: with
tools=[] on the LangGraph agent, all searching happens inside one OpenRouter
completion invisible to LangGraph, which only ever executes a single model
node. The real "how many searches" knob is max_total_results.
"""

import json
import re

import braintrust
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from ai_internship.agents.customers.costs import CostTrackingBraintrustCallbackHandler
from ai_internship.agents.customers.tracing import log_customer_root_span
from ai_internship.config import Settings
from ai_internship.public_assets import load_prompt, project_name
from ai_internship.schemas import CustomerAnalysis

BRAINTRUST_PROJECT = project_name()
PROMPT_SLUG = "customers-v3"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

DEFAULT_MODEL = "openai/gpt-5"
DEFAULT_MAX_SEARCHES = 15
DEFAULT_MAX_RESULTS_PER_SEARCH = 10  # 1-25, per OpenRouter server-tool docs
DEFAULT_MAX_TOTAL_RESULTS = 20  # raised from 5 now that the cap actually applies
DEFAULT_SEARCH_CONTEXT_SIZE = "high"

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
    return CustomerAnalysis(customers=[])


class ExaServerToolCustomerAgent:
    """LangGraph ReAct agent: GPT-5 via OpenRouter with openrouter:web_search server tool.

    Search is bound directly to the reasoning LLM — no separate exa_search
    wrapper tool. OpenRouter executes Exa searches server-side within each
    reasoning call.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        settings: Settings | None = None,
        max_searches: int = DEFAULT_MAX_SEARCHES,
        max_results_per_search: int = DEFAULT_MAX_RESULTS_PER_SEARCH,
        max_total_results: int = DEFAULT_MAX_TOTAL_RESULTS,
        search_context_size: str = DEFAULT_SEARCH_CONTEXT_SIZE,
    ):
        self.model = model
        self.settings = settings or Settings()
        self.max_searches = max_searches
        self.max_results_per_search = max_results_per_search
        self.max_total_results = max_total_results
        self.search_context_size = search_context_size

        llm = ChatOpenAI(
            model=model,
            base_url=OPENROUTER_BASE_URL,
            api_key=self.settings.openrouter_api_key,
            timeout=600,
        )
        # Bind openrouter:web_search directly to the reasoning LLM so search
        # executes server-side within the model's own calls — no second LLM call.
        # engine/max_results/etc MUST be nested under "parameters" — as
        # siblings of "type" they're silently dropped and the model falls
        # back to its own native web search instead of Exa (ELI-951 E0).
        self._llm = llm.bind(
            extra_body={
                "tools": [
                    {
                        "type": "openrouter:web_search",
                        "parameters": {
                            "engine": "exa",
                            "max_results": self.max_results_per_search,
                            "max_total_results": self.max_total_results,
                            "search_context_size": self.search_context_size,
                        },
                    }
                ]
            }
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
            "agent": "ExaServerToolCustomerAgent",
            "product_url": product_url,
            "product_name": product_name,
            "vendor_name": vendor_name,
            "model": self.model,
        }
        cost_tracker = CostTrackingBraintrustCallbackHandler(logger=None)
        with braintrust.start_span(name="ExaServerToolCustomerAgent", metadata=metadata) as span:
            vendor_info = f" sold by {vendor_name}" if vendor_name else ""
            user_message = (
                f"Find all customers for this product: {product_name}{vendor_info}\n"
                f"Product website: {product_url}\n\n"
                f"Search thoroughly using multiple queries. After each search, reason "
                f"about what you found and what types of customers might still be missing. "
                f"Output your final answer as valid JSON only — no explanatory text outside "
                f"the JSON."
            )

            agent = create_react_agent(
                model=self._llm,
                tools=[],
                prompt=self._system_prompt,
            )

            result = agent.invoke(
                {"messages": [{"role": "user", "content": user_message}]},
                config={
                    "recursion_limit": self.max_searches * 3 + 10,
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
                },  # noqa: E501
                metadata=metadata,
                output=parsed.model_dump(),
                cost_tracker=cost_tracker,
            )
            return parsed
