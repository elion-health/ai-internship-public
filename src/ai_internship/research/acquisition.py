"""Deterministic target-scoped inventory and bounded source acquisition."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

import braintrust

from ai_internship.discovery.contracts import (
    FrontierRecord,
    FrontierStatus,
    RetainedSource,
    canonicalize_url,
    technical_exclusion_reason,
)
from ai_internship.discovery.providers import InteractiveEvidenceResult
from ai_internship.research.content_quality import assess_content, assess_page_relevance


@dataclass
class AcquisitionResult:
    source_ids: list[str] = field(default_factory=list)
    discovered_count: int = 0
    retained_count: int = 0
    excluded_count: int = 0
    failure_count: int = 0
    provider_calls: int = 0
    elapsed_seconds: float = 0.0
    warnings: list[dict[str, int | str]] = field(default_factory=list)


def _enrich_content(content: str, url: str, evidence: InteractiveEvidenceResult) -> str:
    sections: list[str] = []
    seen_content: set[str] = set()
    items = []
    for item in evidence.items:
        if item.content in seen_content:
            continue
        seen_content.add(item.content)
        details = [
            f"### {item.control_type}: {item.control_label} (state {item.state})",
            f"Source interaction: {url}",
        ]
        if item.context:
            details.append(f"Context: {item.context}")
        if item.attribution:
            details.append(f"Attribution: {item.attribution}")
        details.extend(["", item.content])
        items.append("\n".join(details))
    if items:
        sections.append("## Interactive content\n\n" + "\n\n".join(items))

    seen_logos: set[str] = set()
    logos = []
    for logo in evidence.logos:
        key = logo.alt_text.casefold()
        if key in seen_logos:
            continue
        seen_logos.add(key)
        details = [
            f"### Logo: {logo.alt_text}",
            f"Source image: {logo.image_url}",
            f"Alt text: {logo.alt_text}",
        ]
        if logo.context:
            details.append(f"Context: {logo.context}")
        logos.append("\n".join(details))
    if logos:
        sections.append("## Supporting logo evidence\n\n" + "\n\n".join(logos))
    return content if not sections else f"{content}\n\n" + "\n\n".join(sections)


async def _provider_call(
    store,
    *,
    run_id: str,
    stage: str,
    url: str,
    timeout_seconds: float,
    function,
    args: tuple[object, ...] = (),
    kwargs: dict[str, object] | None = None,
):
    call_id = str(uuid4())
    started = monotonic()
    await store.start_provider_call(call_id=call_id, run_id=run_id, stage=stage, url=url)
    with braintrust.start_span(
        name="research_provider_call",
        type="tool",
        input={"stage": stage, "url": url, "timeout_seconds": timeout_seconds},
    ) as span:
        try:
            span.log(output={"status": "running"})
            await asyncio.to_thread(braintrust.flush)

            async def invoke():
                result = (
                    function(*args, **(kwargs or {}))
                    if inspect.iscoroutinefunction(function)
                    else await asyncio.to_thread(function, *args, **(kwargs or {}))
                )
                return await result if inspect.isawaitable(result) else result

            result = await asyncio.wait_for(invoke(), timeout=timeout_seconds)
        except Exception as exc:
            elapsed_seconds = monotonic() - started
            await store.finish_provider_call(
                call_id,
                status="failed",
                elapsed_seconds=elapsed_seconds,
                error=str(exc) or type(exc).__name__,
            )
            span.log(
                output={"status": "failed", "error": str(exc) or type(exc).__name__},
                metrics={"elapsed_seconds": elapsed_seconds},
            )
            raise
        elapsed_seconds = monotonic() - started
        await store.finish_provider_call(
            call_id,
            status="succeeded",
            elapsed_seconds=elapsed_seconds,
        )
        span.log(output={"status": "succeeded"}, metrics={"elapsed_seconds": elapsed_seconds})
        return result


def _product_section_first(urls: list[str], canonical_url: str) -> list[str]:
    """`urls` reordered so the ones sharing the canonical URL's path section lead.
    Relative order within each group is the provider's."""
    section = urlsplit(canonical_url).path.rsplit("/", 1)[0]
    if not section.strip("/"):
        return list(urls)

    def inside_section(url: str) -> bool:
        """A path match, not a string prefix. /products/thing-other is a
        different product, and as a prefix it would lead the ordering and spend
        the page limit before the one actually being researched."""
        path = urlsplit(url).path
        return path == section or path.startswith(f"{section}/")

    inside = [url for url in urls if inside_section(url)]
    outside = [url for url in urls if not inside_section(url)]
    return inside + outside


def _within_site(host: str | None, root_host: str) -> bool:
    """True if `host` is `root_host` itself or one of its subdomains."""
    return bool(host) and (host == root_host or host.endswith(f".{root_host}"))


def _inventory_host(urls: list[str], target_host: str) -> str:
    """The host a first-party inventory is filtered against.

    Normally the target's own. But a vendor whose site has moved answers its old
    address with a redirect, and the provider maps the destination rather than
    the address it was handed. Filtering only against the pre-redirect host
    can exclude every mapped URL.

    The move is honoured only when the map came back with nothing on the target's
    own host, so a vendor whose map already works is filtered exactly as before.
    """
    hosts = [urlsplit(url).hostname for url in urls if urlsplit(url).hostname]
    if not hosts or any(_within_site(host, target_host) for host in hosts):
        return target_host

    # The destination is a site, not one host, and its busiest host is not
    # necessarily its root: a map dominated by docs.moved.test must not
    # exclude moved.test. So score the mapped hosts by how many of the mapped
    # URLs each would cover, and prefer the shortest among those covering the
    # most.
    #
    # Only a host the map actually returned is eligible. Walking up to invented
    # ancestors cannot be made safe without a public-suffix list: vendor.co.uk
    # and unrelated.co.uk share co.uk, and treating that as the site would queue
    # another company's pages. Under-reaching a sibling subdomain is the better
    # failure of the two, and a site's map almost always contains its own root.
    hosts = [urlsplit(url).hostname for url in urls if urlsplit(url).hostname]
    if not hosts or any(_within_site(host, target_host) for host in hosts):
        return target_host
    return max(
        set(hosts),
        key=lambda candidate: (
            sum(_within_site(host, candidate) for host in hosts),
            -len(candidate),
        ),
    )


def _site_root(url: str) -> str:
    """The scheme and host of a target's canonical URL, with the path dropped."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/"


@dataclass
class _PageOutcome:
    """One page's result, so the fetch loop can run pages concurrently and still
    fold their counters in a deterministic order."""

    provider_calls: int = 0
    source: RetainedSource | None = None
    failed: bool = False


class FirstPartyAcquisition:
    def __init__(
        self,
        store,
        mapper,
        scraper,
        *,
        first_party_page_limit: int,
        external_fetch_limit: int,
        provider_timeout_seconds: float,
        min_content_characters: int,
        render_retry_wait_ms: int,
        interactive_evidence_limit: int = 0,
        fetch_concurrency: int = 1,
    ):
        self.store = store
        self.mapper = mapper
        self.scraper = scraper
        self.first_party_page_limit = first_party_page_limit
        self.external_fetch_limit = external_fetch_limit
        self.provider_timeout_seconds = provider_timeout_seconds
        self.min_content_characters = min_content_characters
        self.render_retry_wait_ms = render_retry_wait_ms
        self.interactive_evidence_limit = interactive_evidence_limit
        self.fetch_concurrency = max(1, fetch_concurrency)

    async def map_first_party(self, *, target, run_id: str, mode: str) -> AcquisitionResult:
        started = monotonic()
        result = AcquisitionResult()
        if mode != "initial" or await self.store.has_completed_inventory(
            target.target_id, map_limit=self.first_party_page_limit
        ):
            result.elapsed_seconds = monotonic() - started
            return result

        # Mapped from the site root, not from the target's canonical URL. A
        # canonical URL is usually a deep product page, and the provider's map of
        # a deep page can return only that page. The host filter below keeps
        # the result on the vendor's own domain.
        root_url = _site_root(target.canonical_url)
        result.provider_calls = 1
        urls = await _provider_call(
            self.store,
            run_id=run_id,
            stage="first_party_map",
            url=root_url,
            timeout_seconds=self.provider_timeout_seconds,
            function=self.mapper.map_site,
            args=(root_url, self.first_party_page_limit),
        )
        # The page limit is applied after ordering, not to the provider's own
        # order. Mapping the root is what makes a deep product URL yield anything
        # at all, but on a large corporate domain it returns the whole company:
        # unrelated consumer pages can consume the budget before useful pages.
        # Pages under the product's own section of
        # the site come first, so the limit is spent there before the rest of the
        # domain is considered.
        unique = {
            canonicalize_url(url): canonicalize_url(url)
            for url in _product_section_first(urls, target.canonical_url)[
                : self.first_party_page_limit
            ]
        }
        result.discovered_count = len(unique)
        inventory_may_be_truncated = len(urls) >= self.first_party_page_limit
        if inventory_may_be_truncated:
            result.warnings.append(
                {
                    "code": "first_party_map_limit_reached",
                    "mapped_count": result.discovered_count,
                    "limit": self.first_party_page_limit,
                }
            )
        target_host = _inventory_host(list(unique), urlsplit(target.canonical_url).hostname)
        for canonical, requested in sorted(unique.items()):
            reason = technical_exclusion_reason(canonical)
            candidate_host = urlsplit(canonical).hostname
            if not reason and not _within_site(candidate_host, target_host):
                reason = "external_host"
            await self.store.upsert_frontier(
                FrontierRecord(
                    target_id=target.target_id,
                    canonical_url=canonical,
                    requested_url=requested,
                    origin="first_party",
                    status=FrontierStatus.EXCLUDED if reason else FrontierStatus.PENDING,
                    discovery_method="map",
                    discovery_run_id=run_id,
                    policy_reason=reason,
                )
            )
            result.excluded_count += int(reason is not None)
        await self.store.mark_inventory_completed(
            target.target_id,
            run_id,
            map_limit=self.first_party_page_limit,
            mapped_count=result.discovered_count,
            inventory_may_be_truncated=inventory_may_be_truncated,
        )
        result.elapsed_seconds = monotonic() - started
        return result

    async def fetch_first_party(self, *, target, run_id: str) -> AcquisitionResult:
        return await self._fetch_pending(
            target,
            run_id,
            "first_party",
            max_calls=self.first_party_page_limit,
        )

    async def run_external(
        self,
        *,
        target,
        run_id: str,
        observed,
        selected,
        prefetched_pages=(),
    ) -> AcquisitionResult:
        selected_urls = {canonicalize_url(candidate.url) for candidate in selected}
        for candidate in observed:
            canonical = canonicalize_url(candidate.url)
            reason = technical_exclusion_reason(canonical)
            queries = list(getattr(candidate, "queries", []))
            methods = list(getattr(candidate, "discovery_methods", []))
            call_ids = list(getattr(candidate, "provider_call_ids", []))
            profiles = list(getattr(candidate, "profiles", []))
            await self.store.upsert_frontier(
                FrontierRecord(
                    target_id=target.target_id,
                    canonical_url=canonical,
                    requested_url=canonical,
                    origin="external",
                    status=(
                        FrontierStatus.EXCLUDED
                        if reason
                        else FrontierStatus.PENDING
                        if canonical in selected_urls
                        else FrontierStatus.DISCOVERED
                    ),
                    discovery_method="external_agent",
                    discovery_run_id=run_id,
                    policy_reason=reason,
                    query=(queries or [None])[0],
                    queries=queries,
                    discovery_methods=methods or ["external_agent"],
                    provider_call_ids=call_ids,
                    profiles=profiles,
                )
            )
        return await self._fetch_pending(
            target,
            run_id,
            "external",
            max_calls=self.external_fetch_limit,
            prefetched_pages={canonicalize_url(page.url): page for page in prefetched_pages},
        )

    async def _fetch_pending(
        self, target, run_id: str, origin: str, *, max_calls: int, prefetched_pages=None
    ) -> AcquisitionResult:
        started = monotonic()
        result = AcquisitionResult()
        seen_eligible_content_hashes: set[str] = set()
        prefetched_pages = prefetched_pages or {}
        pending = await self.store.list_pending_frontier(target.target_id, origin=origin)
        # Re-screen against the current exclusion policy before applying the fetch
        # limit, not after -- a PENDING record's status was only ever validated at
        # discovery time (map_first_party / run_external), which a resumed graph run
        # can skip entirely if its inventory stage was already checkpointed as
        # complete. Without this, a policy added after that checkpoint (e.g.
        # ELI-1318's developer.* exclusion) would never apply to already-queued
        # records -- and screening only the post-slice batch would let stale
        # excluded records consume fetch-limit slots that belong to real URLs.
        screened = []
        for record in pending:
            reason = technical_exclusion_reason(record.canonical_url)
            if reason:
                await self.store.upsert_frontier(
                    record.model_copy(
                        update={
                            "status": FrontierStatus.EXCLUDED,
                            "policy_reason": reason,
                            "discovery_run_id": run_id,
                        }
                    )
                )
                result.excluded_count += 1
                continue
            screened.append(record)
        eligible = [
            record
            for record in screened
            if origin != "external" or record.status is FrontierStatus.PENDING
        ][:max_calls]
        if origin == "first_party" and len(eligible) < len(screened):
            result.warnings.append(
                {
                    "code": "first_party_fetch_limit_reached",
                    "pending_count": len(screened),
                    "scheduled_count": len(eligible),
                    "limit": max_calls,
                }
            )
        # Pages are independent of each other, so they are fetched concurrently
        # with a bounded number in flight. Each page costs a scrape plus, on the
        # vendor's own site, an interactive-evidence expansion -- measured at
        # ~18.5s per page, which serialised puts a 500-page site at over two
        # hours on network wait alone.
        semaphore = asyncio.Semaphore(self.fetch_concurrency)

        async def fetch_guarded(record) -> _PageOutcome:
            async with semaphore:
                return await self._fetch_one(
                    record,
                    target=target,
                    run_id=run_id,
                    origin=origin,
                    prefetched_pages=prefetched_pages,
                )

        outcomes = await asyncio.gather(*(fetch_guarded(record) for record in eligible))
        # Folded back in the order the records were scheduled, not the order they
        # happened to finish, so the content-hash dedupe below keeps whichever
        # page the serial version would have kept.
        for outcome in outcomes:
            result.provider_calls += outcome.provider_calls
            if outcome.failed:
                result.failure_count += 1
                continue
            source = outcome.source
            result.retained_count += 1
            if source.quality_status == "eligible":
                if source.content_hash not in seen_eligible_content_hashes:
                    result.source_ids.append(source.source_id)
                    seen_eligible_content_hashes.add(source.content_hash)
            else:
                result.excluded_count += 1
        result.elapsed_seconds = monotonic() - started
        return result

    async def _fetch_one(
        self, record, *, target, run_id: str, origin: str, prefetched_pages
    ) -> _PageOutcome:
        outcome = _PageOutcome()
        try:
            page = prefetched_pages.get(record.canonical_url)
            if page is None:
                outcome.provider_calls += 1
                page = await _provider_call(
                    self.store,
                    run_id=run_id,
                    stage=f"{origin}_fetch",
                    url=record.requested_url,
                    timeout_seconds=self.provider_timeout_seconds,
                    function=self.scraper.scrape,
                    args=(record.requested_url,),
                    kwargs={"wait_for_ms": 0},
                )
            assessment = assess_content(
                page.content, minimum_characters=self.min_content_characters
            )
            if assessment.retryable:
                outcome.provider_calls += 1
                page = await _provider_call(
                    self.store,
                    run_id=run_id,
                    stage=f"{origin}_fetch",
                    url=record.requested_url,
                    timeout_seconds=self.provider_timeout_seconds,
                    function=self.scraper.scrape,
                    args=(record.requested_url,),
                    kwargs={"wait_for_ms": self.render_retry_wait_ms},
                )
                assessment = assess_content(
                    page.content, minimum_characters=self.min_content_characters
                )
            if (
                origin == "first_party"
                and self.interactive_evidence_limit > 0
                and hasattr(self.scraper, "expand_interactive_evidence")
            ):
                try:
                    outcome.provider_calls += 1
                    evidence = await _provider_call(
                        self.store,
                        run_id=run_id,
                        stage="first_party_interactive_evidence",
                        url=record.requested_url,
                        timeout_seconds=self.provider_timeout_seconds,
                        function=self.scraper.expand_interactive_evidence,
                        args=(record.requested_url, page.raw_content),
                        kwargs={"limit": self.interactive_evidence_limit},
                    )
                    page = page.model_copy(
                        update={
                            "content": _enrich_content(page.content, record.requested_url, evidence)
                        }
                    )
                except Exception as exc:
                    page = page.model_copy(
                        update={
                            "metadata": {
                                **page.metadata,
                                "interactive_evidence_error": str(exc) or type(exc).__name__,
                            }
                        }
                    )
            assessment = assess_content(
                page.content, minimum_characters=self.min_content_characters
            )
            relevance = assess_page_relevance(page.content, target.name)
            source = RetainedSource.build(
                requested_url=record.requested_url,
                final_url=page.url,
                title=page.title,
                retrieved_at=datetime.now(UTC),
                discovery_run_id=run_id,
                entrypoint=f"{origin}_acquisition",
                content=page.content,
                raw_content=page.raw_content,
                provider_metadata=page.metadata,
                quality_status="eligible"
                if assessment.eligible and relevance.eligible
                else "excluded",
                quality_reason=assessment.reason or relevance.reason,
                parent_url=record.parent_url,
                depth=record.depth,
            )
            await self.store.retain_for_target(target.target_id, source)
            outcome.source = source
        except Exception as exc:
            await self.store.mark_frontier_failed(record, str(exc))
            outcome.failed = True
        return outcome
