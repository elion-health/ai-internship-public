"""
ELI-1170 — Step 1 of 2: build the "frozen" extraction-miss case set.

WHAT THIS SCRIPT DOES (plain language):
We already ran the research graph on 9 vendors (that was ELI-1135/1136).
For each vendor, we know two things:
  1. Which customers we SHOULD have found (the "golden"/expected answers,
     stored in Braintrust).
  2. Which customers we ACTUALLY found (stored in Neo4j, from the real run).

Some expected customers are missing. For each missing customer, we check:
  "Is this customer's name actually sitting in the text of a page we already
  scraped?"

If YES -> the fetch stage did its job (we have the evidence), but extraction
dropped it. That's a genuine LLM/extraction-stage miss, not a fetch problem.

This script finds all of those "yes" cases and writes them to a JSON file.
That JSON file is the frozen input for build_frozen_case_set.py's sibling
script, model_comparison_harness.py, which re-runs ONLY extraction+grounding
(no re-scraping) with different LLM models to see if a stronger model catches
what gpt-5.4-mini missed.

IMPORTANT: this script is 100% READ-ONLY.
  - No LLM calls (no cost, no API calls to OpenAI/OpenRouter).
  - No writes to Neo4j.
  - Just reads: Braintrust (golden answers) + Neo4j (what we extracted +
    what we scraped).

HOW TO RUN:
    uv run python scripts/build_frozen_case_set.py

OUTPUT:
    frozen_extraction_misses.json in the current directory -- a flat list of
    {vendor, customer, source_id, url} dicts. Multiple customers can point at
    the same source_id (same page can mention several missed customers).
"""

import asyncio
import json
import re

from neo4j import AsyncGraphDatabase

from ai_internship.config import Settings
from ai_internship.evaluation.scorers import _normalize
from ai_internship.public_assets import init_dataset, research_targets

# The 9 vendors ELI-1135/1136 profiled. These are the ONLY vendors this
# script looks at -- not the full 25-product Braintrust dataset.
VENDORS = {name: target["url"] for name, target in research_targets().items()}

# Braintrust's "product_id" for each vendor. We need this to pull the right
# expected-customers row out of the golden dataset, and to match the right
# Product node in Neo4j (some vendors have a same-named Vendor node too, so
# we can't just match on name alone).
MARKETPLACE_IDS = {name: target["marketplace_id"] for name, target in research_targets().items()}

OUTPUT_PATH = "frozen_extraction_misses.json"


async def get_extracted(driver, marketplace_id: str) -> set[str]:
    """What customers did we ACTUALLY extract for this vendor?

    Reads the (HealthSystem)-[Adopts]->(Product) edges from Neo4j -- these
    are the "we found this customer" edges the real pipeline run created.
    """
    records, _, _ = await driver.execute_query(
        "MATCH (a:HealthSystem)-[r:RELATES_TO {name: 'Adopts', active: true}]->"
        "(b:Product {marketplace_id: $mid}) RETURN DISTINCT a.name AS name",
        mid=marketplace_id,
    )
    return {rec["name"] for rec in records}


# URLs that are never real prose -- a customer name matching one of these is
# just a substring hit inside a raw URL slug or machine-readable file, not
# actual evidence. Confirmed live: Amwell's only "hit" was the word sitting
# inside a URL string in suki.ai/sitemap.xml, not a real sentence anywhere.
NON_PROSE_URL_SUFFIXES = (".xml", "/sitemap.xml", "/robots.txt", ".json", ".rss")


def _looks_like_prose_page(url: str) -> bool:
    lowered = url.lower()
    return not any(lowered.endswith(suffix) for suffix in NON_PROSE_URL_SUFFIXES)


async def get_fetched_sources(driver, vendor_name: str) -> list[tuple[str, str, str]]:
    """Every page we successfully scraped for this vendor, as
    (source_id, url, page_text) tuples.

    "first_party" = pages on the vendor's own site (not random 3rd-party
    mentions). "fetched" = the scrape actually succeeded (not a 404/blocked
    page). This is the same page set extraction was originally run against.

    Sitemaps/robots.txt/etc. get filtered out here -- their raw content is
    just a list of URLs, and a customer name matching a substring of some
    other page's URL slug isn't real evidence of anything.
    """
    records, _, _ = await driver.execute_query(
        "MATCH (t:ResearchTarget {name: $vendor})-[:TRACKS]->(f:FrontierUrl) "
        "WHERE f.origin = 'first_party' AND f.status = 'fetched' "
        "MATCH (s:RetainedSource {source_id: f.latest_source_id}) "
        "RETURN s.source_id AS source_id, "
        "coalesce(s.final_url, s.requested_url) AS url, s.content AS content",
        vendor=vendor_name,
    )
    return [
        (rec["source_id"], rec["url"], rec["content"] or "")
        for rec in records
        if _looks_like_prose_page(rec["url"])
    ]


def get_expected(dataset, vendor_name: str) -> list[str]:
    """Pull the golden/expected customer list for this vendor out of the
    Braintrust dataset (this is the "answer key" humans curated).

    Only returns customers that have real `supporting_evidence` -- some
    golden entries are just a bare name + source="marketplace" with no
    citation at all (confirmed live: Amwell and Premier, Suki's two
    stubbornest "misses," are both in this bare-entry group). We don't
    actually know those are stated anywhere on the vendor's own site, so
    treating them as misses when the pipeline doesn't find them is testing
    against an unverified assumption, not a confirmed fact.
    """
    for sample in dataset:
        meta = sample.get("metadata", {})
        if meta.get("product_name", "") == vendor_name:
            expected = sample.get("expected", {})
            customers = (
                expected.get("customers", [])
                if isinstance(expected, dict)
                else getattr(expected, "customers", [])
            )
            # A bare string entry has no supporting_evidence field to check at
            # all, so it's exactly the unverified case this filter exists to
            # exclude -- skip it too, don't let it through by default
            # (PR #36 review, Macroscope).
            return [
                c.get("name", c)
                for c in customers
                if isinstance(c, dict) and c.get("supporting_evidence")
            ]
    return []


async def main():
    settings = Settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )

    # Load the golden dataset once up front -- it's small (25 rows), no
    # point re-fetching it per vendor.
    dataset = list(
        init_dataset(
            project="ai-internship",
            name="Marketplace Customers Dataset",
            api_key=settings.braintrust_api_key,
        )
    )

    # This is what we're building: one entry per (vendor, missed customer)
    # where we can PROVE the evidence was already scraped.
    frozen_cases = []

    try:
        for vendor in VENDORS:
            expected = get_expected(dataset, vendor)
            extracted = await get_extracted(driver, MARKETPLACE_IDS[vendor])

            # "Missed" = golden dataset says this customer should be there,
            # but the real run's Neo4j output doesn't have it. Compare
            # normalized forms (same normalize() the real scorer uses) --
            # a byte-for-byte check would wrongly call "Mayo Clinic" a miss
            # just because it got extracted as "Mayo clinic" (PR #36 review,
            # Macroscope).
            normalized_extracted = {_normalize(name) for name in extracted}
            missed = [c for c in expected if _normalize(c) not in normalized_extracted]
            if not missed:
                continue  # nothing missing for this vendor -- skip it

            sources = await get_fetched_sources(driver, vendor)
            if not sources:
                # With no fetched pages, misses cannot diagnose extraction.
                continue

            # For each missed customer, search every scraped page for this
            # vendor for the name as a whole word, not just any substring --
            # a plain "in" check would match "Acme" inside "acmeenter" or
            # "TK" inside random prose, producing fake extraction-stage
            # misses (PR #36 review, Macroscope). Word-boundary regex still
            # catches real mentions since customer names are proper nouns,
            # not sentence fragments.
            for customer in missed:
                needle_pattern = re.compile(rf"\b{re.escape(customer)}\b", re.IGNORECASE)
                for source_id, url, content in sources:
                    if needle_pattern.search(content):
                        frozen_cases.append(
                            {
                                "vendor": vendor,
                                "customer": customer,
                                "source_id": source_id,
                                "url": url,
                            }
                        )
                        break  # one matching page is enough, stop looking

            found_so_far = len([c for c in frozen_cases if c["vendor"] == vendor])
            print(f"[{vendor}] {found_so_far} frozen cases")

    finally:
        await driver.close()

    # Dump everything to disk so the comparison harness can just load this
    # file and not have to touch Neo4j/Braintrust logic again.
    with open(OUTPUT_PATH, "w") as f:
        json.dump(frozen_cases, f, indent=2)

    unique_sources = {c["source_id"] for c in frozen_cases}
    print(f"\nTotal frozen cases: {len(frozen_cases)} across {len(unique_sources)} unique pages")
    print(f"Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
