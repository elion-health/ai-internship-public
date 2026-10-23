"""Cache each vendor's own site. Nothing else.

This maps a vendor's site, fetches its pages and stores them as RetainedSource
nodes, so later work can replay extraction over a fixed corpus without paying to
fetch the web again.

It does not extract. There is no model in this script and no ingestion graph, so
extraction cannot happen by accident: the only cost is Firecrawl credits. That
matters because extraction output is only worth keeping once the prompts behind
it are settled, and re-running it over cached pages later is nearly free.

It is safe to re-run. A vendor whose inventory is already complete at this page
limit is skipped, and only frontier URLs still pending or failed are fetched, so
an interrupted run resumes where it stopped rather than refetching.

    uv run python scripts/cache_first_party_pages.py
    uv run python scripts/cache_first_party_pages.py --product "Suki" --product "FirstHx"
    uv run python scripts/cache_first_party_pages.py --only-missing --page-limit 300
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger
from neo4j import AsyncGraphDatabase

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_internship.config import Settings  # noqa: E402
from ai_internship.discovery.contracts import DiscoveryRun  # noqa: E402
from ai_internship.discovery.providers import FirecrawlDiscoveryProvider  # noqa: E402
from ai_internship.discovery.store import DiscoveryStore  # noqa: E402
from ai_internship.public_assets import research_targets  # noqa: E402
from ai_internship.research.acquisition import FirstPartyAcquisition  # noqa: E402
from ai_internship.research.contracts import ResearchTarget  # noqa: E402

TARGETS = research_targets()


async def cached_page_counts(driver) -> dict[str, int]:
    """First-party pages already held, per target name."""
    result = await driver.execute_query(
        "MATCH (target:ResearchTarget)-[:HAS_SOURCE]->(source:RetainedSource) "
        "WHERE source.entrypoint = 'first_party_acquisition' "
        "RETURN target.name AS name, count(*) AS pages"
    )
    records = result[0] if isinstance(result, tuple) else result
    return {record["name"]: record["pages"] for record in records}


async def cache_one(acquisition, store, name: str, url: str) -> dict[str, int]:
    target = await store.load_or_create_target(ResearchTarget.build(name, url))
    run = DiscoveryRun(
        entrypoint="first_party_cache",
        started_at=datetime.now(UTC),
        config={"target_id": target.target_id, "extraction": "none"},
    )
    await store.start_run(run)
    try:
        inventory = await acquisition.map_first_party(
            target=target, run_id=run.run_id, mode="initial"
        )
        fetched = await acquisition.fetch_first_party(target=target, run_id=run.run_id)
    except Exception as error:  # noqa: BLE001
        # One vendor's site must not end the run -- an unreachable host or a
        # provider error here leaves every later vendor uncached.
        logger.error("[{}] failed: {}", name, repr(error))
        await store.finish_run(run.run_id, status="failed", metrics={})
        return {
            "discovered": 0,
            "retained": 0,
            "failed": 0,
            "provider_calls": 0,
            "failed_targets": 1,
        }
    metrics = {
        "discovered": inventory.discovered_count,
        "retained": fetched.retained_count,
        "failed": fetched.failure_count,
        "provider_calls": inventory.provider_calls + fetched.provider_calls,
        "failed_targets": 0,
    }
    await store.finish_run(run.run_id, status="completed", metrics=metrics)
    for warning in list(inventory.warnings) + list(fetched.warnings):
        logger.warning("[{}] {}", name, warning)
    logger.info(
        "[{}] mapped {}, cached {}, failed {}, {} provider calls",
        name,
        metrics["discovered"],
        metrics["retained"],
        metrics["failed"],
        metrics["provider_calls"],
    )
    return metrics


async def main_async(args) -> int:
    settings = Settings()
    page_limit = args.page_limit or settings.research_first_party_page_limit
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    provider = FirecrawlDiscoveryProvider(
        api_key=settings.firecrawl_api_key,
        timeout=settings.research_provider_timeout_seconds,
        crawl_timeout=settings.research_crawl_timeout_seconds,
    )
    store = DiscoveryStore(driver)
    acquisition = FirstPartyAcquisition(
        store,
        provider,
        provider,
        first_party_page_limit=page_limit,
        external_fetch_limit=settings.research_external_fetch_limit,
        provider_timeout_seconds=settings.research_provider_timeout_seconds,
        min_content_characters=settings.research_min_content_characters,
        render_retry_wait_ms=settings.research_render_retry_wait_ms,
        interactive_evidence_limit=settings.research_interactive_evidence_limit,
        fetch_concurrency=settings.research_fetch_concurrency,
    )

    try:
        names = args.product or list(TARGETS)
        if args.only_missing:
            held = await cached_page_counts(driver)
            names = [name for name in names if held.get(name, 0) < args.missing_below]
        logger.info("caching {} vendor site(s) at up to {} pages each", len(names), page_limit)

        totals = {
            "discovered": 0,
            "retained": 0,
            "failed": 0,
            "provider_calls": 0,
            "failed_targets": 0,
        }
        for position, name in enumerate(names, start=1):
            logger.info("({}/{}) {}", position, len(names), name)
            metrics = await cache_one(acquisition, store, name, TARGETS[name]["url"])
            for key, value in metrics.items():
                totals[key] += value
        logger.info(
            "done: {} pages cached, {} pages failed, {} provider calls across {} vendor(s); "
            "{} target failures",
            totals["retained"],
            totals["failed"],
            totals["provider_calls"],
            len(names),
            totals["failed_targets"],
        )
        return 1 if totals["failed_targets"] or totals["failed"] else 0
    finally:
        await driver.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cache vendors' own sites as RetainedSource pages. No extraction, no model."
    )
    parser.add_argument(
        "--product",
        action="append",
        choices=list(TARGETS),
        help="Vendor(s) to cache (default: every vendor in TARGETS).",
    )
    parser.add_argument(
        "--page-limit",
        type=int,
        default=None,
        help="Pages per vendor (default: RESEARCH_FIRST_PARTY_PAGE_LIMIT).",
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Skip vendors that already hold enough cached pages.",
    )
    parser.add_argument(
        "--missing-below",
        type=int,
        default=20,
        help="With --only-missing, the page count under which a vendor counts as missing "
        "(default: 20). A vendor holding fewer than this was a bad map, not a small site.",
    )
    raise SystemExit(asyncio.run(main_async(parser.parse_args())))


if __name__ == "__main__":
    main()
