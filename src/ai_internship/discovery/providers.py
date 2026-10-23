"""Small provider interfaces that keep Firecrawl SDK types out of the graphs."""

from __future__ import annotations

import inspect
from html.parser import HTMLParser
from typing import Protocol

from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    url: str
    title: str | None = None
    snippet: str | None = None


class FetchedPage(BaseModel):
    url: str
    content: str
    raw_content: str | None = None
    title: str | None = None
    metadata: dict[str, object] = {}


class InteractiveEvidence(BaseModel):
    control_type: str
    control_label: str
    state: int
    content: str
    attribution: str = ""
    context: str = ""


class LogoEvidence(BaseModel):
    alt_text: str
    image_url: str
    context: str = ""


class InteractiveEvidenceResult(BaseModel):
    items: list[InteractiveEvidence] = Field(default_factory=list)
    logos: list[LogoEvidence] = Field(default_factory=list)


class SearchProvider(Protocol):
    def search(self, query: str, limit: int) -> list[SearchHit]: ...


class ScrapeProvider(Protocol):
    def scrape(self, url: str, *, wait_for_ms: int = 0) -> FetchedPage: ...


class SiteMapper(Protocol):
    def map_site(self, url: str, limit: int) -> list[str]: ...


class SiteCrawler(Protocol):
    def crawl(self, url: str, max_depth: int, limit: int) -> list[FetchedPage]: ...


_EVIDENCE_CUES = (
    "customer",
    "testimonial",
    "partner",
    "integration",
    "marketplace",
    "case study",
    "case-study",
    "case studies",
)


class _LogoParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []
        self._context_stack: list[tuple[str, str]] = []
        self.context = ""
        self.logos: list[LogoEvidence] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in {"section", "article", "footer", "header", "main"}:
            self._context_stack.append((tag, self.context))
            self.context = ""
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_tag = tag
            self._heading_parts = []
        alt_text = (attributes.get("alt") or "").strip()
        image_url = (attributes.get("src") or "").strip()
        if tag == "img" and alt_text and image_url:
            self.logos.append(
                LogoEvidence(
                    alt_text=alt_text,
                    image_url=image_url,
                    context=self.context,
                )
            )

    def handle_data(self, data):
        if self._heading_tag:
            self._heading_parts.append(data)

    def handle_endtag(self, tag):
        if tag == self._heading_tag:
            self.context = " ".join("".join(self._heading_parts).split())
            self._heading_tag = None
            self._heading_parts = []
        if self._context_stack and tag == self._context_stack[-1][0]:
            _, self.context = self._context_stack.pop()


def _logo_evidence(raw_html: str, limit: int) -> list[LogoEvidence]:
    parser = _LogoParser()
    parser.feed(raw_html)
    unique: list[LogoEvidence] = []
    seen: set[tuple[str, str]] = set()
    for logo in parser.logos:
        key = (logo.alt_text.casefold(), logo.context.casefold())
        if key in seen or not any(cue in logo.context.lower() for cue in _EVIDENCE_CUES):
            continue
        seen.add(key)
        unique.append(logo)
        if len(unique) == limit:
            break
    return unique


class FirecrawlDiscoveryProvider:
    """Adapter that presents Firecrawl results through the local discovery contracts."""

    def __init__(
        self,
        api_key: str | None = None,
        client=None,
        timeout: float | None = None,
        crawl_timeout: float | None = None,
    ):
        if client is None:
            if not api_key:
                raise ValueError("FIRECRAWL_API_KEY is required for provider-backed discovery")
            from firecrawl import FirecrawlApp

            client = FirecrawlApp(api_key=api_key, timeout=timeout)
        self._client = client
        # Separate from `timeout` above: that one bounds each individual HTTP
        # request the client makes; this bounds the crawl job as a whole
        # (Firecrawl's crawl() polls until the job finishes or this elapses).
        self._crawl_timeout = crawl_timeout

    def search(self, query: str, limit: int) -> list[SearchHit]:
        response = self._client.search(query, limit=limit)
        return [
            SearchHit(url=item.url, title=item.title, snippet=item.description)
            for item in response.web or []
            if item.url
        ]

    def scrape(self, url: str, *, wait_for_ms: int = 0) -> FetchedPage:
        scrape_kwargs = {"formats": ["markdown", "rawHtml"], "only_main_content": True}
        parameters = inspect.signature(self._client.scrape).parameters.values()
        if any(
            parameter.name == "only_clean_content"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        ):
            scrape_kwargs["only_clean_content"] = True
        if wait_for_ms:
            scrape_kwargs["wait_for"] = wait_for_ms
        response = self._client.scrape(url, **scrape_kwargs)
        metadata = getattr(response, "metadata", {}) or {}
        if hasattr(metadata, "model_dump"):
            metadata = metadata.model_dump()
        return FetchedPage(
            url=url,
            content=getattr(response, "markdown", "") or "",
            raw_content=getattr(response, "raw_html", None) or getattr(response, "rawHtml", None),
            title=metadata.get("title"),
            metadata=metadata,
        )

    def map_site(self, url: str, limit: int) -> list[str]:
        response = self._client.map(url, limit=limit)
        return [
            link if isinstance(link, str) else link.url
            for link in getattr(response, "links", []) or []
            if isinstance(link, str) or getattr(link, "url", None)
        ]

    def crawl(self, url: str, max_depth: int, limit: int) -> list[FetchedPage]:
        # Found 2026-09-16: this method didn't exist at all, so
        # DiscoverySession's crawl_site tool was never wired to a real
        # provider anywhere -- the external-discovery agent could only ever
        # read the single page a search hit pointed to, never explore
        # further pages on that same external site.
        #
        # PR #73 review (Macroscope, Medium), verified against the Firecrawl
        # SDK's request validation: a non-positive limit raises instead of
        # being a no-op. DiscoverySession.crawl_site passes exactly that
        # (min(limit, remaining_page_budget)) once crawl_page_cap is already
        # reached, so this must return no pages rather than error.
        if limit <= 0:
            return []
        response = self._client.crawl(
            url,
            max_discovery_depth=max_depth,
            limit=limit,
            formats=["markdown", "rawHtml"],
            only_main_content=True,
            timeout=self._crawl_timeout,
        )
        pages = []
        for document in getattr(response, "data", []) or []:
            metadata = getattr(document, "metadata", {}) or {}
            if hasattr(metadata, "model_dump"):
                metadata = metadata.model_dump()
            page_url = metadata.get("source_url") or metadata.get("url")
            if not page_url:
                continue
            pages.append(
                FetchedPage(
                    url=page_url,
                    content=getattr(document, "markdown", "") or "",
                    raw_content=getattr(document, "raw_html", None)
                    or getattr(document, "rawHtml", None),
                    title=metadata.get("title"),
                    metadata=metadata,
                )
            )
        return pages

    def expand_interactive_evidence(
        self, url: str, raw_html: str | None, *, limit: int
    ) -> InteractiveEvidenceResult:
        if limit <= 0 or not raw_html:
            return InteractiveEvidenceResult()
        logos = _logo_evidence(raw_html, limit)
        items: list[InteractiveEvidence] = []
        seen: set[str] = set()
        for state, item in enumerate(self._interactive_states(url, limit)):
            content = item["content"].strip()
            if not content or content in seen:
                continue
            seen.add(content)
            items.append(
                InteractiveEvidence(
                    control_type=item["control_type"],
                    control_label=item["control_label"],
                    state=state,
                    content=content,
                    attribution=item["attribution"],
                    context=item["context"],
                )
            )
        return InteractiveEvidenceResult(items=items, logos=logos)

    def _interactive_states(self, url: str, limit: int) -> list[dict[str, str]]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="networkidle")
                return page.evaluate(
                    """async ({ cues, limit }) => {
const label = element => `${element?.getAttribute("aria-label") || ""} ${
  element?.textContent || ""}`.toLowerCase();
const revealsMore = element =>
  /load more|show more|view more|see more|more results/.test(label(element));
const hasCue = element => cues.some(cue => label(element).includes(cue));
if (!hasCue(document.body)) return [];
const cueBearingAncestor = element => {
  for (let ancestor = element;
    ancestor && !ancestor.matches("main, body, html");
    ancestor = ancestor.parentElement) {
    if (hasCue(ancestor)) return ancestor;
  }
  return null;
};
const evidenceRoot = control => {
  const panelId = control.getAttribute("aria-controls");
  const panel = panelId && document.getElementById(panelId);
  return cueBearingAncestor(panel) || cueBearingAncestor(control.parentElement) || panel
    || control.closest("section, article, [role=tabpanel]")
    || control.parentElement;
};
const isEvidenceControl = element => {
  const root = evidenceRoot(element);
  const inEvidence = hasCue(root) || hasCue(element);
  const filterNav = label(element.closest("nav, [role=navigation]")).includes("filter");
  return inEvidence && (revealsMore(element) || filterNav
    || element.matches("[role=tab], [aria-pressed], [aria-selected]")
    || hasCue(element));
};
const controls = () => [...document.querySelectorAll('button, [role="tab"], [role="button"]')]
  .filter(isEvidenceControl)
  .sort((left, right) => Number(revealsMore(right)) - Number(revealsMore(left)));
const descriptor = control => {
  const matching = controls().filter(candidate => label(candidate) === label(control));
  return {
    label: label(control),
    labelIndex: matching.indexOf(control),
    panelId: control.getAttribute("aria-controls"),
    repeat: revealsMore(control),
  };
};
const resolveControl = item =>
  controls().filter(control => label(control) === item.label)[item.labelIndex];
const resolveRoot = (control, item) => {
  const panel = item.panelId && document.getElementById(item.panelId);
  return control ? evidenceRoot(control) : cueBearingAncestor(panel) || panel;
};
const states = [];
let actions = 0;
for (const item of controls().slice(0, limit).map(descriptor)) {
  do {
    if (actions === limit) break;
    const control = resolveControl(item);
    if (!control) break;
    const before = resolveRoot(control, item)?.innerText.trim() || "";
    control.click();
    actions += 1;
    await new Promise(resolve => {
      const observer = new MutationObserver(() => {
        const updated = resolveRoot(resolveControl(item), item)?.innerText.trim() || "";
        if (updated !== before) {
          observer.disconnect();
          resolve();
        }
      });
      observer.observe(document.body, {childList: true, subtree: true, characterData: true});
      setTimeout(() => {
        observer.disconnect();
        resolve();
      }, 3000);
    });
    const postClickControl = resolveControl(item);
    const root = resolveRoot(postClickControl, item);
    const content = root?.innerText.trim() || "";
    if (!content || content === before || states.some(state => state.content === content)) break;
    const heading = root?.querySelector("h1, h2, h3, h4, h5, h6");
    const attribution = [...(
      root?.querySelectorAll("strong, [class*=author], [class*=testimonial]") || []
    )]
      .map(element => element.innerText.trim())
      .filter(Boolean)
      .join(" ");
    states.push({
      control_type: postClickControl?.getAttribute("role") || control.getAttribute("role")
        || control.tagName.toLowerCase(),
      control_label: postClickControl?.getAttribute("aria-label")
        || postClickControl?.innerText.trim()
        || control.getAttribute("aria-label") || control.innerText.trim(),
      content,
      attribution,
      context: heading ? heading.innerText.trim() : "",
    });
  } while (item.repeat && actions < limit);
  if (actions === limit) break;
}
return states;
}""",
                    {"cues": _EVIDENCE_CUES, "limit": limit},
                )
            finally:
                browser.close()


class MergedSearchProvider:
    """Combines results from multiple search providers, deduped by URL.

    Generic over whatever providers it's given -- has no knowledge of which
    specific search engines they wrap.
    """

    def __init__(self, *providers: SearchProvider):
        self._providers = providers

    def search(self, query: str, limit: int) -> list[SearchHit]:
        seen: set[str] = set()
        merged: list[SearchHit] = []
        for provider in self._providers:
            for hit in provider.search(query, limit):
                if hit.url in seen:
                    continue
                seen.add(hit.url)
                merged.append(hit)
        return merged
