"""Run + score the stateful research graph against the Braintrust golden
dataset, for the ELI-1010 marketplace-customer-integration targets."""

from __future__ import annotations

import argparse
import asyncio
from uuid import uuid4

from braintrust import EvalAsync, EvalCase
from loguru import logger

from ai_internship.config import Settings
from ai_internship.evaluation.scorers import (
    customers_f1,
    customers_precision,
    customers_recall,
    integrations_f1,
    integrations_precision,
    integrations_recall,
)
from ai_internship.public_assets import init_dataset, project_name, research_targets
from ai_internship.research.contracts import ResearchRequest
from ai_internship.research.runtime import build_stateful_research_graph, setup_braintrust_tracing

BRAINTRUST_PROJECT = project_name()
CUSTOMER_DATASET_NAME = "Marketplace Customers Dataset"
# Separate dataset from CUSTOMER_DATASET_NAME above -- different expected-output shape
# (integrations, not customers), so scored as its own EvalAsync pass below,
# not merged into the customers one. As of 2026-08-11 both datasets actually
# cover the same 41 products (confirmed live via braintrust.init_dataset), but
# TARGETS below is a curated 38-product subset of that (25 original + 13 added
# by ELI-1289) -- see the filter in the "integrations" pass below, which only
# scores the intersection of the two.
INTEGRATIONS_DATASET_NAME = "Marketplace Integrations Dataset"

# Targets sourced directly from the "Marketplace Customers Dataset" in Braintrust
# (queried live 2026-08-05 via braintrust.init_dataset -- product_name/url/
# product_id per row). marketplace_id == Braintrust's metadata.product_id.
# fetch_relationship_names below matches a Neo4j node by name OR marketplace_id
# (either is sufficient) -- name-only matching turned out to break vendors
# whose canonical graph entity name diverges from TARGETS (e.g. entity
# resolution preserving "OncoEMR®" over "Flatiron OncoEMR"; Codex review,
# PR #49), while marketplace_id-only matching turned out unreliable for
# vendors whose node never had it set or had it set to a different value
# (confirmed live 2026-08-20, the 13 ELI-1289 vendors). Ordered by dataset row order
# (insertion order), not alphabetically: rows 1-13 are
# earlier/unrelated dataset entries, rows 14-22 are ELI-1115's marketplace-
# expansion batch (10 added, Carequality later dropped -> 9 -- this is the
# exact set ELI-1135/1136 profiled), rows 23-25 are the original ELI-1010
# targets, added last.


TARGETS = research_targets()


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Run and score the stateful research graph against the marketplace "
        "customers golden dataset."
    )
    parser.add_argument(
        "--product",
        action="append",
        choices=list(TARGETS),
        default=None,
        help="Target product(s) to evaluate (default: all products in TARGETS).",
    )
    parser.add_argument(
        "--profile",
        action="append",
        default=None,
        help="Profiles to extract (default: customers only -- no golden data for "
        "integrations yet in this project's dataset).",
    )
    parser.add_argument("--mode", choices=("initial", "resume"), default="initial")
    parser.add_argument(
        "--skip-external",
        action="store_true",
        help="Run first-party acquisition and extraction without external discovery.",
    )
    parser.add_argument("--force-profile", action="append", default=[])
    parser.add_argument(
        "--score-only",
        action="store_true",
        help="Skip running the graph; only query Neo4j for existing extraction "
        "output and score it.",
    )
    return parser.parse_args(argv)


async def run_target(
    graph,
    name: str,
    url: str,
    profiles: list[str],
    mode: str,
    force_profiles: list[str],
    marketplace_id: str | None = None,
    skip_external: bool = False,
) -> None:
    request = ResearchRequest(
        name=name,
        url=url,
        profiles=profiles,
        mode=mode,
        force_profiles=force_profiles,
        marketplace_id=marketplace_id,
        skip_external=skip_external,
    )
    thread_id = str(uuid4())
    await graph.ainvoke(
        {"request": request},
        config={"configurable": {"thread_id": thread_id}, "metadata": {"thread_id": thread_id}},
    )


async def fetch_relationship_names(
    driver, name: str, marketplace_id: str | None, relation: str, from_label: str, to_label: str
) -> list[str]:
    """Read active RELATES_TO edges of `relation` connected to the target node,
    identified by name (case-insensitive, trimmed) OR marketplace_id -- either
    is sufficient, neither is required to be reliable alone.

    marketplace_id-only matching turned out unreliable: TARGETS' marketplace_id
    values don't consistently match what's actually stamped on the
    corresponding Neo4j node (confirmed live 2026-08-20 -- for the 13
    ELI-1289 vendors, none of the TARGETS marketplace_ids existed anywhere in
    the graph, either because the node never had marketplace_id set, or
    because it was set to a different value than TARGETS records). Switching
    to name-only matching then broke the *other* direction (Codex review,
    PR #49): entity resolution can canonicalize a node to a display name that
    differs from what's in TARGETS (e.g. "Flatiron OncoEMR" in TARGETS vs. the
    resolver deliberately preserving "OncoEMR®" as the real node's name --
    tests/test_graphiti_resolve.py:95-121 -- to avoid a fuzzy-matched
    extraction variant silently renaming an established entity). Matching on
    *either* field covers both failure modes: a vendor whose marketplace_id
    is missing/wrong still resolves by name, and a vendor whose canonical name
    diverges from TARGETS still resolves by marketplace_id (when that field
    happens to be reliable for it). `marketplace_id=None` is a normal, safe
    input -- Cypher's `x = null` evaluates to null (never true), so that half
    of the OR simply never matches and name-only behavior is preserved.

    Restricting the query to `:Product`/`:HealthSystem` labels via
    from_label/to_label guards against the same-named-node collision this
    function used to guard against (e.g. a `:Vendor` and a `:Product` both
    named "FirstHx") -- a `:Vendor`-labeled node never matches a query pattern
    restricted to `:Product`. Matches are aggregated (OR'd) across every node
    sharing that name/marketplace_id+label, not just one, so multiple
    same-named or same-id Product nodes (if any) all contribute their edges.

    Either-side name matching is only safe when `from_label == to_label`.
    For Adopts, match the Product side by name and return the HealthSystem
    counterparty. Matching the opposite side could import unrelated edges
    from a same-named organization.

    When `from_label == to_label` ("Integrates", both sides `:Product`),
    either-side matching is still necessary: the model decides per-assertion
    which product is "subject" (item.product) vs "object"
    (item.integrated_product). Matching only one side would drop the other
    direction.

    Self-loops are excluded in both cases by node identity (`elementId(a) <>
    elementId(b)`), not by comparing names: distinct nodes can share a display
    name across labels. Identity checks retain legitimate edges between them
    while excluding actual same-node relationships."""
    match_clause = (
        f"MATCH (a:{from_label})-[r:RELATES_TO {{name: $relation, active: true}}]-(b:{to_label}) "
    )
    if from_label == to_label:
        records, _, _ = await driver.execute_query(
            match_clause + "WHERE (toLower(trim(a.name)) = toLower(trim($name)) "
            "OR a.marketplace_id = $mid "
            "OR toLower(trim(b.name)) = toLower(trim($name)) "
            "OR b.marketplace_id = $mid) "
            "AND elementId(a) <> elementId(b) "
            "RETURN DISTINCT CASE WHEN toLower(trim(a.name)) = toLower(trim($name)) "
            "OR a.marketplace_id = $mid "
            "THEN b.name ELSE a.name END AS name",
            relation=relation,
            name=name,
            mid=marketplace_id,
        )
    else:
        records, _, _ = await driver.execute_query(
            match_clause + "WHERE (toLower(trim(b.name)) = toLower(trim($name)) "
            "OR b.marketplace_id = $mid) "
            "AND elementId(a) <> elementId(b) "
            "RETURN DISTINCT a.name AS name",
            relation=relation,
            name=name,
            mid=marketplace_id,
        )
    return [record["name"] for record in records]


async def main_async(args) -> int:
    settings = Settings()
    setup_braintrust_tracing(settings)

    products = args.product or list(TARGETS)
    profiles = args.profile or ["customers"]

    graph, driver, graphiti_client = build_stateful_research_graph(settings)
    outputs: dict[str, dict] = {}
    try:
        for name in products:
            target = TARGETS[name]
            if not args.score_only:
                logger.info(f"Running stateful research graph for {name}...")
                await run_target(
                    graph,
                    name,
                    target["url"],
                    profiles,
                    args.mode,
                    args.force_profile,
                    marketplace_id=target["marketplace_id"],
                    skip_external=args.skip_external,
                )

            customers = await fetch_relationship_names(
                driver, name, target["marketplace_id"], "Adopts", "HealthSystem", "Product"
            )
            logger.info(f"[{name}] customers extracted: {customers}")
            outputs[name] = {"customers": [{"name": c} for c in customers]}

            if "integrations" in profiles:
                integrations = await fetch_relationship_names(
                    driver, name, target["marketplace_id"], "Integrates", "Product", "Product"
                )
                logger.info(f"[{name}] integrations extracted: {integrations}")
                outputs[name]["integrations"] = [{"name": i} for i in integrations]
    finally:
        await driver.close()
        if graphiti_client is not None:
            await graphiti_client.close()

    logger.info(
        f"Loading dataset '{CUSTOMER_DATASET_NAME}' from Braintrust "
        f"project '{BRAINTRUST_PROJECT}'..."
    )
    dataset = init_dataset(
        project=BRAINTRUST_PROJECT, name=CUSTOMER_DATASET_NAME, api_key=settings.braintrust_api_key
    )
    wanted = {n.lower() for n in products}
    eval_cases = []
    for sample in dataset:
        meta = sample.get("metadata", {})
        product_name = meta.get("product_name", "")
        if product_name.lower() not in wanted or product_name not in TARGETS:
            continue
        eval_cases.append(
            EvalCase(
                input={"product_name": product_name, "url": TARGETS[product_name]["url"]},
                expected=sample["expected"],
                metadata={"product_id": meta.get("product_id", "")},
            )
        )

    if not eval_cases:
        raise ValueError("No dataset rows matched the requested products -- nothing to score")

    def task_fn(inp) -> dict:
        return outputs.get(inp["product_name"], {"customers": []})

    experiment_name = f"stateful-graph__{args.mode}__{'-'.join(sorted(products))}"
    logger.info(f"Running Braintrust eval as experiment '{experiment_name}'...")
    result = await EvalAsync(
        BRAINTRUST_PROJECT,
        data=lambda: eval_cases,
        task=task_fn,
        scores=[customers_recall, customers_precision, customers_f1],
        experiment_name=experiment_name,
        metadata={"mode": args.mode, "profiles": profiles},
    )
    logger.info(f"Eval complete: {result.summary}")

    # Integrations dataset is separate from CUSTOMER_DATASET_NAME above (different rows,
    # different product set) -- scored as its own pass rather than merged into
    # the customers eval_cases/task_fn above, so a product with no Integrations-
    # dataset row (or not requested) doesn't affect the customers score at all.
    if "integrations" in profiles:
        logger.info(
            f"Loading dataset '{INTEGRATIONS_DATASET_NAME}' from Braintrust "
            f"project '{BRAINTRUST_PROJECT}'..."
        )
        integrations_dataset = init_dataset(
            project=BRAINTRUST_PROJECT,
            name=INTEGRATIONS_DATASET_NAME,
            api_key=settings.braintrust_api_key,
        )
        integrations_eval_cases = []
        skipped_outside_targets = []
        for sample in integrations_dataset:
            meta = sample.get("metadata", {})
            product_name = meta.get("product_name", "")
            if product_name.lower() not in wanted:
                continue
            if product_name not in TARGETS:
                # Macroscope (PR #38 review): don't drop this silently -- it
                # was requested (in `wanted`) but has no TARGETS entry, so
                # there's no url/marketplace_id to run or score it against.
                skipped_outside_targets.append(product_name)
                continue
            integrations_eval_cases.append(
                EvalCase(
                    input={"product_name": product_name, "url": TARGETS[product_name]["url"]},
                    expected=sample["expected"],
                    metadata={"product_id": meta.get("product_id", "")},
                )
            )
        if skipped_outside_targets:
            logger.info(
                f"Skipping {len(skipped_outside_targets)} requested Integrations-dataset "
                f"row(s) with no TARGETS entry (no url/marketplace_id to run against): "
                f"{skipped_outside_targets}"
            )

        if not integrations_eval_cases:
            logger.info(
                "No Integrations-dataset rows matched the requested products -- "
                "skipping the integrations eval pass."
            )
        else:

            def integrations_task_fn(inp) -> dict:
                return outputs.get(inp["product_name"], {"integrations": []})

            integrations_experiment_name = (
                f"stateful-graph-integrations__{args.mode}__{'-'.join(sorted(products))}"
            )
            logger.info(
                f"Running Braintrust eval as experiment '{integrations_experiment_name}'..."
            )
            integrations_result = await EvalAsync(
                BRAINTRUST_PROJECT,
                data=lambda: integrations_eval_cases,
                task=integrations_task_fn,
                scores=[integrations_recall, integrations_precision, integrations_f1],
                experiment_name=integrations_experiment_name,
                metadata={"mode": args.mode, "profiles": profiles},
            )
            logger.info(f"Integrations eval complete: {integrations_result.summary}")

    return 0


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
