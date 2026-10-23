"""ReAct CustomerAgent using LangGraph + Firecrawl v2.

Active experiment while Exa credits are being topped up. Uses the same
GPT-5 + LangGraph ReAct pattern as the Exa agent but replaces Exa with
Firecrawl v2, which adds map_vendor_site (fast discovery of proof pages
on the vendor's own domain) and full-page scraping via scrape_page.

Architecture:
  LangGraph prebuilt ReAct agent (create_react_agent)
  Model:  GPT-5 via OpenAI (langchain-openai)
  Search: Firecrawl v2 (Firecrawl client, not FirecrawlApp)
  Prompt: customers-v3 from Braintrust + confidence schema appended
"""

import json
import logging
import re

import braintrust
from firecrawl import Firecrawl
from firecrawl.types import ScrapeOptions
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from ai_internship.agents.customers.costs import CostTrackingBraintrustCallbackHandler
from ai_internship.agents.customers.tracing import log_customer_root_span
from ai_internship.config import Settings
from ai_internship.public_assets import load_prompt, project_name
from ai_internship.schemas import CustomerAnalysis

logger = logging.getLogger(__name__)

BRAINTRUST_PROJECT = project_name()
PROMPT_SLUG = "customers-v3"

DEFAULT_MODEL = "openai/gpt-5"
DEFAULT_NUM_RESULTS = 5
DEFAULT_MAX_SEARCHES = 15

# Appended to the Braintrust system prompt so GPT-5 knows the required schema.
# customers-v3 does not include `confidence`, so we add it here rather than
# creating a separate Braintrust slug just for one missing field.
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


def _make_firecrawl_tools(client: Firecrawl, num_results: int) -> list:
    @tool
    def search_web(query: str) -> str:
        """Search the web using Firecrawl. Returns full page content for each result.
        Use this to find customers, case studies, press releases, and news about a
        health IT product. Call multiple times with different queries to be thorough."""
        try:
            response = client.search(
                query,
                limit=num_results,
                scrape_options=ScrapeOptions(
                    formats=["markdown"],
                    only_main_content=False,
                ),
            )
            results = response.web or []
            if not results:
                return "No results found."
            parts = []
            for r in results:
                url = getattr(r, "url", "") or ""
                title = getattr(r, "title", "") or ""
                markdown = getattr(r, "markdown", "") or ""
                description = getattr(r, "description", "") or ""
                content = markdown[:1500] if markdown else description
                parts.append(f"URL: {url}\nTitle: {title}\n{content}")
            return "\n\n---\n\n".join(parts)
        except Exception as e:
            return f"Search failed: {e}"

    @tool
    def scrape_page(url: str) -> str:
        """Fetch the full content of a specific web page. Use this to deep-read a
        promising page — a vendor customer list, case study, press release, or logo
        wall. Always use this on the vendor's /customers or /case-studies page."""
        try:
            result = client.scrape(
                url,
                formats=["markdown"],
                only_main_content=False,
                wait_for=1500,
            )
            content = result.markdown or ""
            return content[:5000] if content else "No content found."
        except Exception as e:
            return f"Could not scrape page: {e}"

    @tool
    def map_vendor_site(vendor_url: str) -> str:
        """Discover customer-proof pages on a vendor's own website (case studies,
        customer lists, testimonials, press releases). Pass the vendor's base domain
        URL. Returns a list of relevant page URLs to scrape. Use this early — it finds
        pages that search engines may not surface."""
        try:
            resp = client.map(
                vendor_url,
                search="customer case study success testimonial trusted",
                include_subdomains=True,
                limit=30,
            )
            links = resp.links or []
            if not links:
                return "No pages found."
            urls = [lnk.url for lnk in links if getattr(lnk, "url", None)]
            return "\n".join(urls[:30])
        except Exception as e:
            return f"Could not map site: {e}"

    return [map_vendor_site, search_web, scrape_page]


def _parse_output(text: str) -> CustomerAnalysis:
    for pattern in [r"```json\s*([\s\S]*?)```", r"```\s*([\s\S]*?)```"]:
        match = re.search(pattern, text)
        if match:
            try:
                return CustomerAnalysis.model_validate(json.loads(match.group(1).strip()))
            except Exception as e:
                logger.warning("Code block parse failed: %s", e)

    try:
        return CustomerAnalysis.model_validate(json.loads(text.strip()))
    except Exception as e:
        logger.warning("Direct JSON parse failed: %s", e)

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
                    except Exception as e:
                        logger.warning("Brace-scan parse failed: %s", e)
                        break

    logger.error("All parse attempts failed. Raw output (first 500 chars): %s", text[:500])
    return CustomerAnalysis(customers=[])


class ReactFirecrawlCustomerAgent:
    """LangGraph ReAct agent: GPT-5 + Firecrawl v2.

    Three tools: map_vendor_site (discover proof pages on vendor domain),
    search_web (full page content via Firecrawl), scrape_page (deep-read).
    System prompt: customers-v3 from Braintrust + confidence schema addendum.
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

        firecrawl_client = Firecrawl(api_key=self.settings.firecrawl_api_key)
        tools = _make_firecrawl_tools(firecrawl_client, num_results)

        llm = ChatOpenAI(
            model=model,
            api_key=self.settings.openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
            timeout=600,
        )

        base_prompt = (
            load_prompt(
                project=BRAINTRUST_PROJECT,
                slug=PROMPT_SLUG,
                api_key=self.settings.braintrust_api_key,
            )
            .prompt_data.prompt.messages[0]
            .content
        )

        self._agent = create_react_agent(
            model=llm,
            tools=tools,
            prompt=base_prompt + _SCHEMA_ADDENDUM,
        )

    def analyze(
        self,
        product_url: str,
        product_name: str,
        vendor_name: str | None = None,
    ) -> CustomerAnalysis:
        metadata = {
            "agent": "ReactFirecrawlCustomerAgent",
            "product_url": product_url,
            "product_name": product_name,
            "vendor_name": vendor_name,
            "model": self.model,
        }
        cost_tracker = CostTrackingBraintrustCallbackHandler(logger=None)
        with braintrust.start_span(name="ReactFirecrawlCustomerAgent", metadata=metadata) as span:
            vendor_info = f" sold by {vendor_name}" if vendor_name else ""
            user_message = (
                f"Find all customers for this product: {product_name}{vendor_info}\n"
                f"Product website: {product_url}\n\n"
                f"Use map_vendor_site first, then search_web and scrape_page. "
                f"After at most {self.max_searches} tool calls total, STOP and output "
                f"your final answer as valid JSON only — no text outside the JSON."
            )

            result = self._agent.invoke(
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
