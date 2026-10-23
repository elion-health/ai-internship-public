"""ELI-1432 step 3: score integrations at the *candidate* level, not just the
edge level.

Background (see ELI-1432 handoff): `_CREATABLE_LABELS = {"HealthSystem"}` in
graphiti_resolve.py means an `Integrates` assertion whose counterpart Product
was not pre-seeded with a marketplace_id gets rejected during grounding with
`GroundingRejection(code="unresolved_endpoint", ...)` -- the pipeline found
and correctly identified the integration partner from evidence text, but
couldn't create a real graph edge for it because that Product node didn't
exist yet. ELI-1427 (PR #67) expanded the seed catalog 75 -> 2,620 products,
but the original run's extraction already completed *before* that seeding,
so those specific candidates were already marked terminal and never retried
(confirmed live: `discovery/store.py`'s `plan_extraction` skips any
(source, profile) pair with a terminal outcome unless `force=True`, and
re-running `--score-only` after the seed import produced byte-identical
scores to before it -- see ELI-1432 handoff step 2).

This script recovers those "would-be-accepted" candidates directly from
`ExtractionRun.grounding_rejections` (a JSON blob, per ELI-1143) without
re-running extraction, unions them with the real accepted edges already in
Neo4j, and scores that combined set against the same Braintrust golden
integrations dataset -- reporting both numbers side by side.

Only `unresolved_endpoint` rejections on `Integrates` assertions are
recovered. Every other rejection code (incomplete_relationship, or any
content/grounding-quality rejection from `full_rejection_reason`) means the
pipeline rejected the candidate for a real reason unrelated to seeding, and
recovering those would overstate what the pipeline actually found -- see
ELI-1432 handoff step 4's warning not to conflate the two in the FN audit.
"""

from __future__ import annotations

import argparse
import asyncio
import json

# Reuse the exact edge-matching query the main eval script uses, rather than
# re-deriving it -- scripts/ is on sys.path[0] when this file is run
# directly, so this is a plain same-directory import. TARGETS is NOT taken
# from this import, though -- see _load_targets below.
from eval_stateful_research import (
    BRAINTRUST_PROJECT,
    INTEGRATIONS_DATASET_NAME,
    fetch_relationship_names,
)
from loguru import logger
from neo4j import AsyncGraphDatabase

from ai_internship.config import Settings
from ai_internship.evaluation.scorers import (
    _normalize,
    integrations_f1,
    integrations_precision,
    integrations_recall,
)
from ai_internship.public_assets import init_dataset, research_targets


def _load_targets() -> dict:
    return research_targets()


TARGETS = _load_targets()


async def resolve_target_id(driver, name: str) -> str | None:
    """ResearchTarget.target_id is sha256(canonicalize_url(url)) (research/contracts.py)
    -- rather than reimplement that canonicalization here and risk drifting from
    it, look the node up by the name run_target() actually stamped on it (the
    same TARGETS key), matched the same case-insensitive/trimmed way
    fetch_relationship_names matches everything else in this project.

    Picking `records[0]` with no ordering was nondeterministic whenever more
    than one ResearchTarget node shares this normalized name -- the project
    already documents this happening for real (vendor_fetch_baseline.py's own
    comment on same-vendor duplicate targets differing only by casing), and a
    stale duplicate winning silently would make the recovery below query
    against the wrong node's ExtractionRuns. Break the tie deterministically:
    prefer whichever candidate target_id actually has ExtractionRun data to
    recover from -- a stale/empty duplicate has none."""
    records, _, _ = await driver.execute_query(
        "MATCH (t:ResearchTarget) WHERE toLower(trim(t.name)) = toLower(trim($name)) "
        "RETURN t.target_id AS target_id",
        name=name,
    )
    if not records:
        return None
    target_ids = [r["target_id"] for r in records]
    if len(target_ids) == 1:
        return target_ids[0]
    counts, _, _ = await driver.execute_query(
        "UNWIND $target_ids AS tid MATCH (e:ExtractionRun {target_id: tid}) "
        "RETURN tid, count(e) AS n",
        target_ids=target_ids,
    )
    counts_by_id = {r["tid"]: r["n"] for r in counts}
    best = max(target_ids, key=lambda tid: counts_by_id.get(tid, 0))
    logger.warning(
        f"'{name}' matches {len(target_ids)} ResearchTarget nodes ({target_ids}) -- "
        f"picking {best!r} (most ExtractionRuns: {counts_by_id.get(best, 0)}), not records[0]"
    )
    return best


async def recover_unresolved_integrations_candidates(
    driver, target_id: str, target_name: str
) -> set[str]:
    """Pull every Integrates assertion rejected purely for `unresolved_endpoint`
    across every ExtractionRun for this target's integrations-profile sources,
    and return the counterpart product name for each -- whichever side of
    subject/object isn't this target itself.

    Target-side matching uses `_normalize()` (scorers.py), not plain
    `.strip().lower()` -- the same space/hyphen/diacritic/curly-apostrophe
    variants that function exists to handle everywhere else in this project
    (e.g. "eVisitNB" vs "eVisit NB") apply just as much to an assertion's
    extracted subject/object text as to a scored prediction; a plain
    lower/strip comparison silently missed those and dropped real recoverable
    candidates into skipped_neither_side instead."""
    records, _, _ = await driver.execute_query(
        "MATCH (e:ExtractionRun) WHERE e.target_id = $target_id "
        "AND e.profile = 'integrations' AND e.grounding_rejections IS NOT NULL "
        "RETURN e.grounding_rejections AS grounding_rejections",
        target_id=target_id,
    )
    target_norm = _normalize(target_name)
    candidates: set[str] = set()
    skipped_neither_side = 0
    for record in records:
        rejections = json.loads(record["grounding_rejections"])
        for rejection in rejections:
            if rejection.get("code") != "unresolved_endpoint":
                continue
            assertion = rejection.get("assertion") or {}
            if assertion.get("relation") != "Integrates":
                continue
            subject = (assertion.get("subject") or "").strip()
            obj = (assertion.get("object") or "").strip()
            if _normalize(subject) == target_norm:
                candidates.add(obj)
            elif _normalize(obj) == target_norm:
                candidates.add(subject)
            else:
                # Neither side matched the target's own name -- can't tell
                # which side is the recovered partner, so this one is
                # skipped rather than guessed. Logged for visibility, not
                # silently dropped.
                skipped_neither_side += 1
    if skipped_neither_side:
        logger.warning(
            f"[{target_name}] {skipped_neither_side} unresolved_endpoint Integrates "
            "rejection(s) had neither side matching the target's own name -- skipped"
        )
    candidates.discard("")
    return candidates


async def main_async(args) -> int:
    settings = Settings()
    products = args.product or list(TARGETS)
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )

    edge_only: dict[str, set[str]] = {}
    candidate_level: dict[str, set[str]] = {}
    recovered_counts: dict[str, int] = {}
    # Bounded concurrency, not a plain sequential loop -- each vendor is 2-3
    # independent Neo4j round trips with nothing shared between vendors (PR
    # review finding), same pattern eval_stateful_research.py already uses.
    # A read-only audit script meant to be re-run for future audits shouldn't
    # take ~61x one vendor's latency by construction.
    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    async def process(name: str) -> None:
        async with semaphore:
            target = TARGETS[name]
            edges = set(
                await fetch_relationship_names(
                    driver, name, target["marketplace_id"], "Integrates", "Product", "Product"
                )
            )
            target_id = await resolve_target_id(driver, name)
            if target_id is None:
                logger.warning(f"[{name}] no ResearchTarget node found -- 0 recovered candidates")
                recovered = set()
            else:
                recovered = await recover_unresolved_integrations_candidates(
                    driver, target_id, name
                )
            edge_only[name] = edges
            candidate_level[name] = edges | recovered
            recovered_counts[name] = len(recovered - edges)
            logger.info(
                f"[{name}] edges: {sorted(edges)} | +{recovered_counts[name]} recovered "
                f"from grounding_rejections: {sorted(recovered - edges)}"
            )

    try:
        results = await asyncio.gather(
            *(process(name) for name in products), return_exceptions=True
        )
        failures = [
            (name, exc)
            for name, exc in zip(products, results, strict=True)
            if isinstance(exc, BaseException)
        ]
        for name, exc in failures:
            logger.error(f"[{name}] failed: {exc!r}")
        if failures:
            logger.warning(
                f"{len(failures)}/{len(products)} product(s) failed: "
                f"{[name for name, _ in failures]} -- continuing with the rest"
            )
    finally:
        await driver.close()

    total_recovered = sum(recovered_counts.values())
    logger.info(
        f"Total recovered candidates across {len(products)} product(s): {total_recovered} "
        f"(products with >=1 recovered: {sum(1 for c in recovered_counts.values() if c)})"
    )

    logger.info(f"Loading dataset '{INTEGRATIONS_DATASET_NAME}' from Braintrust...")
    dataset = init_dataset(
        project=BRAINTRUST_PROJECT,
        name=INTEGRATIONS_DATASET_NAME,
        api_key=settings.braintrust_api_key,
    )
    wanted = {n.lower() for n in products}

    def build_eval_cases(predictions: dict[str, set[str]]):
        cases = []
        for sample in dataset:
            meta = sample.get("metadata", {})
            product_name = meta.get("product_name", "")
            if product_name.lower() not in wanted or product_name not in TARGETS:
                continue
            cases.append((product_name, sample["expected"], predictions.get(product_name, set())))
        return cases

    for label, predictions in (("edge-only", edge_only), ("candidate-level", candidate_level)):
        cases = build_eval_cases(predictions)
        if not cases:
            logger.warning(f"[{label}] no matching dataset rows -- skipping score")
            continue
        recalls, precisions, f1s = [], [], []
        for _product_name, expected, names in cases:
            output = {"integrations": [{"name": n} for n in names]}
            recalls.append(integrations_recall(None, output, expected))
            precisions.append(integrations_precision(None, output, expected))
            f1s.append(integrations_f1(None, output, expected))
        n = len(cases)
        logger.info(
            f"[{label}] integrations over {n} product(s): "
            f"recall={100 * sum(recalls) / n:.2f}% "
            f"precision={100 * sum(precisions) / n:.2f}% "
            f"f1={100 * sum(f1s) / n:.2f}%"
        )

    return 0


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Score integrations at candidate level (recovering unresolved_endpoint "
        "rejections) alongside the existing edge-only score, without re-running extraction."
    )
    parser.add_argument("--product", action="append", choices=list(TARGETS), default=None)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Max products to process concurrently (default: 8). All work here is "
        "read-only Neo4j queries, so this is safe to raise; kept modest by default "
        "to not hammer the driver's connection pool.",
    )
    return parser.parse_args(argv)


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
