"""ReAct CustomerAgent using LangGraph + Exa via OpenRouter web plugin.

Architecture:
  LangGraph prebuilt ReAct agent (create_react_agent)
  Model:  GPT-5 via OpenRouter
  Search: Exa via OpenRouter web plugin

  The exa_search tool calls OpenRouter with {"plugins": [{"id": "web", "engine": "exa"}]},
  which runs the Exa search server-side and returns results to the ReAct loop.

Why not direct Exa?
  Direct Exa credits are exhausted. OpenRouter provides Exa access through its
  web plugin, billed to the OpenRouter account.
"""

import json
import re

import braintrust
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
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
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

DEFAULT_MODEL = "openai/gpt-5"
DEFAULT_NUM_RESULTS = 8
DEFAULT_MAX_SEARCHES = 15
DEFAULT_MAX_CHARACTERS = 800

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


def _make_exa_tool(search_llm: ChatOpenAI, num_results: int):
    extra_body = {
        "plugins": [
            {
                "id": "web",
                "engine": "exa",
                "max_results": num_results,
                "max_characters": DEFAULT_MAX_CHARACTERS,
            }
        ]
    }

    @tool
    def exa_search(query: str, config: RunnableConfig) -> str:
        """Search the web using Exa neural search. Use this to find customers,
        case studies, press releases, and news about a health IT product.
        Call this multiple times with different queries to be thorough."""
        response = search_llm.invoke(
            [
                SystemMessage(
                    content="You are a search assistant. Search for the given query and return "
                    "all results including URLs, titles, and text content. Be comprehensive."
                ),
                {"role": "user", "content": f"Search for: {query}"},
            ],
            config=config,
            extra_body=extra_body,
        )
        return response.content

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
    return CustomerAnalysis(customers=[])


class ExaOpenRouterCustomerAgent:
    """LangGraph ReAct agent: GPT-5 via OpenRouter + Exa via OpenRouter web plugin.

    The exa_search tool calls OpenRouter with the Exa plugin so no direct Exa
    API key is needed — searches are billed through the OpenRouter account.
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

        self._llm = ChatOpenAI(
            model=model,
            base_url=OPENROUTER_BASE_URL,
            api_key=self.settings.openrouter_api_key,
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
            "agent": "ExaOpenRouterCustomerAgent",
            "product_url": product_url,
            "product_name": product_name,
            "vendor_name": vendor_name,
            "model": self.model,
        }
        cost_tracker = CostTrackingBraintrustCallbackHandler(logger=None)
        with braintrust.start_span(name="ExaOpenRouterCustomerAgent", metadata=metadata) as span:
            vendor_info = f" sold by {vendor_name}" if vendor_name else ""
            user_message = (
                f"Find all customers for this product: {product_name}{vendor_info}\n"
                f"Product website: {product_url}\n\n"
                f"Search thoroughly using multiple queries. After each search, reason "
                f"about what you found and what types of customers might still be missing. "
                f"After at most {self.max_searches} tool calls total, STOP and output your "
                f"final answer as valid JSON only — no explanatory text outside the JSON."
            )

            exa_tool = _make_exa_tool(self._llm, self.num_results)
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
