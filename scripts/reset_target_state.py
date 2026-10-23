"""Wipe one ResearchTarget's own subgraph, to force a genuine from-scratch run.

Scope: everything this target owns exclusively -- ResearchTarget, its FrontierUrl
nodes, and (for sources it doesn't share with any other target) the RetainedSource,
DiscoveryRun/ProviderCall/RetrievalEvent/ExtractionRun/Episode nodes produced while
researching it, and the RELATES_TO edges its extractions persisted. Entity nodes
(Product/HealthSystem/etc.) are deliberately NOT deleted -- they're shared across
targets in this graph, and a real from-scratch research run would still see other
targets' entities in a live deployment. This script exists because no reset utility
exists elsewhere in the codebase (only narrow test fixtures delete-by-id); see
docs/experiments/eli-graph-value experiment.

RetainedSource is globally MERGEd by source_id (canonical URL + content hash), so
two different targets can end up with a HAS_SOURCE edge to the same node (e.g. a
shared press page, or two ResearchTarget records that turn out to be the same real
company registered under two different URLs). Deleting a shared source would also
delete the OTHER target's extractions/episodes/edges for it -- caught by automated
review on ELI-1122's PR, confirmed live (two Flatiron OncoEMR target nodes, one at
flatiron.com/, an older one at flatiron.com/oncology/oncology-ehr, both linked to
the same press-release sources). Any source still linked to another target after
this one is removed is left untouched entirely, not partially cleaned up.

Usage:
    python scripts/reset_target_state.py --name "Flatiron OncoEMR" --url https://flatiron.com
    python scripts/reset_target_state.py --name "..." --url "..." --confirm
"""

from __future__ import annotations

import argparse
import asyncio

from neo4j import AsyncGraphDatabase

from ai_internship.config import Settings
from ai_internship.research.contracts import ResearchTarget


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually delete. Without this flag, only prints what would be deleted.",
    )
    return parser.parse_args(argv)


SOURCE_DELETE_BATCH = 10


async def gather_scope(driver, target_id: str) -> dict[str, list[str]]:
    records, _, _ = await driver.execute_query(
        "MATCH (target:ResearchTarget {target_id: $target_id}) "
        "OPTIONAL MATCH (target)-[:HAS_SOURCE]->(source:RetainedSource) "
        "OPTIONAL MATCH (target)-[:TRACKS]->(frontier:FrontierUrl) "
        "RETURN target.target_id AS found_target, "
        "collect(DISTINCT source.source_id) AS all_source_ids, "
        "collect(DISTINCT frontier.canonical_url) AS frontier_urls",
        target_id=target_id,
    )
    if not records or not records[0]["found_target"]:
        return {
            "source_ids": [],
            "shared_source_ids": [],
            "extraction_ids": [],
            "frontier_urls": [],
            "discovery_run_ids": [],
        }
    row = records[0]
    all_source_ids = [v for v in row["all_source_ids"] if v]

    shared_records, _, _ = await driver.execute_query(
        "MATCH (other:ResearchTarget)-[:HAS_SOURCE]->(source:RetainedSource) "
        "WHERE source.source_id IN $source_ids AND other.target_id <> $target_id "
        "RETURN DISTINCT source.source_id AS source_id",
        source_ids=all_source_ids,
        target_id=target_id,
    )
    shared_source_ids = {r["source_id"] for r in shared_records}
    source_ids = [s for s in all_source_ids if s not in shared_source_ids]

    extraction_records, _, _ = await driver.execute_query(
        "MATCH (extraction:ExtractionRun)-[:EXTRACTED]->(source:RetainedSource) "
        "WHERE source.source_id IN $source_ids "
        "RETURN collect(DISTINCT extraction.extraction_id) AS extraction_ids",
        source_ids=source_ids,
    )
    # Matched by the target_id embedded in DiscoveryRun.config (see initialize()
    # in research/graph.py) rather than by DISCOVERED/RETRIEVED relationships --
    # a run that fails before ever producing a FrontierUrl or RetainedSource has
    # neither relationship, so relationship-based matching leaves it (and its
    # ProviderCall children) behind after a "reset". Confirmed live: 4 real runs
    # for this target vs. only 2 found via the DISCOVERED relationship alone.
    run_records, _, _ = await driver.execute_query(
        "MATCH (run:DiscoveryRun) WHERE run.config CONTAINS $target_id "
        "RETURN collect(DISTINCT run.run_id) AS discovery_run_ids",
        target_id=target_id,
    )
    return {
        "source_ids": source_ids,
        "shared_source_ids": sorted(shared_source_ids),
        "extraction_ids": [v for v in extraction_records[0]["extraction_ids"] if v],
        "frontier_urls": [v for v in row["frontier_urls"] if v],
        "discovery_run_ids": [v for v in run_records[0]["discovery_run_ids"] if v],
    }


async def delete_scope(driver, target_id: str, scope: dict[str, list[str]]) -> None:
    source_ids = scope["source_ids"]
    episode_uuids = source_ids + scope["extraction_ids"]

    await driver.execute_query(
        "MATCH ()-[edge:RELATES_TO]->() WHERE edge.source_id IN $source_ids DELETE edge",
        source_ids=source_ids,
    )
    # One label per query, not `MATCH (episode) WHERE episode:Episode OR
    # episode:Episodic`: the disjunction leaves the match unanchored, so Neo4j
    # scans every node in the database (105k here) for each reset. Run ten
    # resets at once and that exhausts dbms.memory.transaction.total.max.
    for label in ("Episode", "Episodic"):
        await driver.execute_query(
            f"MATCH (episode:{label}) WHERE episode.uuid IN $episode_uuids DETACH DELETE episode",
            episode_uuids=episode_uuids,
        )
    await driver.execute_query(
        "MATCH (extraction:ExtractionRun)-[:EXTRACTED]->(source:RetainedSource) "
        "WHERE source.source_id IN $source_ids DETACH DELETE extraction",
        source_ids=source_ids,
    )
    await driver.execute_query(
        "MATCH (source:RetainedSource)-[:LATEST_RETRIEVAL]->(event:RetrievalEvent) "
        "WHERE source.source_id IN $source_ids DETACH DELETE event",
        source_ids=source_ids,
    )
    await driver.execute_query(
        "MATCH (call:ProviderCall) WHERE call.run_id IN $run_ids DETACH DELETE call",
        run_ids=scope["discovery_run_ids"],
    )
    await driver.execute_query(
        "MATCH (run:DiscoveryRun) WHERE run.run_id IN $run_ids DETACH DELETE run",
        run_ids=scope["discovery_run_ids"],
    )
    # Chunked in small batches, unlike the deletes above: RetainedSource carries
    # the page content and every edge into it, and a DETACH DELETE driven by an
    # IN list buffers far more than the nodes themselves -- 78 sources was
    # already enough to exceed dbms.memory.transaction.total.max (1.4 GiB) on a
    # freshly restarted database, while the same 78 deleted one at a time took
    # 4 seconds. The other deletes touch far lighter nodes and stay well inside.
    for start in range(0, len(source_ids), SOURCE_DELETE_BATCH):
        await driver.execute_query(
            "MATCH (source:RetainedSource) WHERE source.source_id IN $source_ids "
            "DETACH DELETE source",
            source_ids=source_ids[start : start + SOURCE_DELETE_BATCH],
        )
    await driver.execute_query(
        "MATCH (:ResearchTarget {target_id: $target_id})-[:TRACKS]->(frontier:FrontierUrl) "
        "DETACH DELETE frontier",
        target_id=target_id,
    )
    await driver.execute_query(
        "MATCH (target:ResearchTarget {target_id: $target_id}) DETACH DELETE target",
        target_id=target_id,
    )


async def main_async(args) -> int:
    settings = Settings()
    target = ResearchTarget.build(args.name, args.url)
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    try:
        scope = await gather_scope(driver, target.target_id)
        print(f"target_id={target.target_id} canonical_url={target.canonical_url}")
        print(f"  sources (to delete): {len(scope['source_ids'])}")
        if scope["shared_source_ids"]:
            print(
                f"  sources shared with another target (left untouched): "
                f"{len(scope['shared_source_ids'])}"
            )
        print(f"  extraction_runs: {len(scope['extraction_ids'])}")
        print(f"  frontier_urls: {len(scope['frontier_urls'])}")
        print(f"  discovery_runs: {len(scope['discovery_run_ids'])}")
        if not any(v for k, v in scope.items() if k != "shared_source_ids"):
            print("Nothing found for this target -- already empty, or never researched.")
            return 0
        if not args.confirm:
            print("\nDry run only. Re-run with --confirm to actually delete.")
            return 0
        await delete_scope(driver, target.target_id, scope)
        print("\nDeleted.")
    finally:
        await driver.close()
    return 0


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
