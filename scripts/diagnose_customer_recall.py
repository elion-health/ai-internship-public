"""Read-only diagnostic: for each golden-dataset customer, trace exactly where
the pipeline stopped -- not in the crawled corpus, excluded by the relevance
filter, no candidate proposed, rejected by grounding (with which code), or
persisted. Doesn't run the graph or write anything; only reads whatever is
already in Neo4j from a prior `eval_stateful_research.py` run."""

from __future__ import annotations

import argparse
import asyncio
import json

from neo4j import AsyncGraphDatabase

from ai_internship.config import Settings
from ai_internship.enrichment.grounding import normalize_text
from ai_internship.public_assets import init_dataset, project_name, research_targets

BRAINTRUST_PROJECT = project_name()
DATASET_NAME = "Marketplace Customers Dataset"

# Kept in sync with scripts/eval_stateful_research.py's TARGETS.
MARKETPLACE_IDS = {name: target["marketplace_id"] for name, target in research_targets().items()}


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Trace why each golden-dataset customer was or wasn't found for a product."
    )
    parser.add_argument("--product", required=True, choices=list(MARKETPLACE_IDS))
    return parser.parse_args(argv)


def _name_mentioned(customer_name: str, content: str) -> bool:
    """Unlike the target-product matching in enrichment/grounding.py (which is
    deliberately loose -- vendors describe their own product with varying
    marketing phrasing), customer names are proper nouns that appear literally
    when genuinely mentioned. Matching on independent words (e.g. "cancer",
    "center") instead of the full phrase produces false positives on almost
    any oncology-industry page, so this requires the full normalized name as
    one contiguous substring."""
    normalized_name = normalize_text(customer_name).lower()
    return bool(normalized_name) and normalized_name in normalize_text(content).lower()


def _load_golden_customers(product: str, settings: Settings) -> list[str]:
    dataset = init_dataset(
        project=BRAINTRUST_PROJECT, name=DATASET_NAME, api_key=settings.braintrust_api_key
    )
    for sample in dataset:
        if sample.get("metadata", {}).get("product_name") == product:
            return [c["name"] for c in sample["expected"]["customers"]]
    raise SystemExit(f"No golden-dataset row found for product {product!r}")


async def diagnose(product: str) -> None:
    settings = Settings()
    golden_customers = _load_golden_customers(product, settings)

    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    try:
        records, _, _ = await driver.execute_query(
            "MATCH (t:ResearchTarget {name: $product})-[:HAS_SOURCE]->(s:RetainedSource) "
            "RETURN s.source_id AS source_id, s.final_url AS url, s.content AS content, "
            "s.quality_status AS status",
            product=product,
        )
        sources = [r.data() for r in records]
        print(f"Corpus for {product!r}: {len(sources)} retained sources.\n")

        for name in golden_customers:
            matches = [s for s in sources if _name_mentioned(name, s["content"])]
            if not matches:
                print(f"[{name}] NOT FOUND in any retained source content")
                continue
            for source in matches:
                prefix = f"[{name}] {source['url']}"
                if source["status"] != "eligible":
                    print(f"{prefix} -> EXCLUDED before extraction (status={source['status']})")
                    continue
                er_records, _, _ = await driver.execute_query(
                    "MATCH (er:ExtractionRun {source_id: $source_id, profile: 'customers'}) "
                    "RETURN er.outcome AS outcome, er.grounding_rejections AS rejections "
                    "ORDER BY er.completed_at DESC LIMIT 1",
                    source_id=source["source_id"],
                )
                if not er_records:
                    print(f"{prefix} -> eligible, but no extraction attempted yet")
                    continue
                outcome = er_records[0]["outcome"]
                print(f"{prefix} -> extraction outcome: {outcome}")
                rejections = er_records[0]["rejections"]
                if rejections and rejections != "[]":
                    for rejection in json.loads(rejections):
                        assertion = rejection.get("assertion", {})
                        if (
                            normalize_text(assertion.get("subject", "")).lower()
                            != normalize_text(name).lower()
                        ):
                            continue
                        print(
                            f"    rejected ({rejection.get('code')}): "
                            f"{assertion.get('subject')} -> {assertion.get('object')}"
                        )
            print()

    finally:
        await driver.close()


def main() -> int:
    args = parse_args()
    asyncio.run(diagnose(args.product))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
