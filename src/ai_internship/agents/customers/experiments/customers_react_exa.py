"""ReAct CustomerAgent using LangGraph + Exa search.

Architecture:
  LangGraph prebuilt ReAct agent (create_react_agent)
  Model:  GPT-5 via OpenAI (langchain-openai) — no Anthropic
  Search: Exa neural search

Why this is different from the previous Exa attempt (ELI-884):
  Before — CustomerAgent used Exa in a single pass: one call to search(),
  fixed number of results, no reasoning between searches. The agent had no
  way to decide what to look for next based on what it already found.

  Now — ReAct interleaves reasoning and acting. After each Exa search the
  model explicitly thinks about what is still missing and decides whether to
  search again, search differently, or stop. This targets the premature
  stopping failure mode identified in ELI-763.

ReAct loop (LangGraph handles this automatically):
  think → search → observe → think → search → ... → final answer
"""

import json
import re

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
DEFAULT_MAX_SEARCHES = 25

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


def _make_exa_tool(exa_client: Exa, num_results: int) -> object:
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
    # customers-v3's own final instruction says "JSON array" (not
    # {"customers": [...]}), so the model may legitimately return a bare
    # list. Wrap it before validating in every branch below — mirrors the
    # fix already present in customers_react_exa_multiagent.py, which this
    # file was missing (a bare-array response silently produced an empty
    # CustomerAnalysis with no error).
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
    # Fallback: model may return a bare JSON array.
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


class ReactExaCustomerAgent:
    """LangGraph ReAct agent: GPT-5 + Exa search, reasoning between each search call.

    The agent decides what to search for, evaluates results, and continues
    searching until it is confident it has found all available customers.
    This is a fundamentally different architecture from the single-pass Exa
    attempt in ELI-884.
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
            "agent": "ReactExaCustomerAgent",
            "product_url": product_url,
            "product_name": product_name,
            "vendor_name": vendor_name,
            "model": self.model,
        }
        cost_tracker = CostTrackingBraintrustCallbackHandler(logger=None)
        with braintrust.start_span(name="ReactExaCustomerAgent", metadata=metadata) as span:
            vendor_info = f" sold by {vendor_name}" if vendor_name else ""
            user_message = (
                f"Find all customers for this product: {product_name}{vendor_info}\n"
                f"Product website: {product_url}\n\n"
                f"Search thoroughly using multiple queries. After each search, reason "
                f"about what you found and what types of customers might still be missing. "
                f"After at most {self.max_searches} tool calls total, STOP and output your "
                f"final answer as valid JSON only — no explanatory text outside the JSON."
            )

            exa_tool = _make_exa_tool(self._exa_client, self.num_results)
            agent = create_react_agent(
                model=self._llm, tools=[exa_tool], prompt=self._system_prompt
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
