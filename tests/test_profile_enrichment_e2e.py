from datetime import UTC, datetime

import pytest
from neo4j import AsyncGraphDatabase

from ai_internship.discovery.contracts import (
    DiscoveryRun,
    FrontierRecord,
    FrontierStatus,
    RetainedSource,
)
from ai_internship.discovery.store import DiscoveryStore
from ai_internship.enrichment.contracts import Assertion, ExtractionWork, IngestionOutcome
from ai_internship.enrichment.graph import IngestionDependencies, build_ingestion_graph
from ai_internship.graph.local import LocalGraphAdapter
from ai_internship.research.contracts import ResearchTarget

pytestmark = pytest.mark.e2e


class FixtureExtractor:
    async def extract(self, source, profiles, seeded_entities):
        return [
            Assertion(
                subject="ELI-986 Mercy",
                subject_type="HealthSystem",
                relation="Adopts",
                object="ELI-986 Widget",
                object_type="Product",
                quote="ELI-986 Mercy uses ELI-986 Widget.",
                profile="customers",
            )
        ]


async def cleanup_fixture(driver, source_id: str) -> None:
    """Remove only this test's deterministic source graph before and after the test."""
    await driver.execute_query(
        "MATCH (episode:Episode {source_id: $source_id}) DETACH DELETE episode",
        source_id=source_id,
    )
    await driver.execute_query(
        "MATCH (:Entity)-[edge:RELATES_TO {source_id: $source_id}]->(:Entity) DELETE edge",
        source_id=source_id,
    )
    await driver.execute_query(
        "MATCH (entity:Entity) WHERE entity.uuid IN $entity_uuids DETACH DELETE entity",
        entity_uuids=["new:HealthSystem:ELI-986 Mercy", "new:Product:ELI-986 Widget"],
    )
    await driver.execute_query(
        "MATCH (run:ExtractionRun)-[:EXTRACTED]->"
        "(source:RetainedSource {source_id: $source_id}) "
        "DETACH DELETE run",
        source_id=source_id,
    )
    await driver.execute_query(
        "MATCH (event:RetrievalEvent {run_id: 'eli-986-e2e'}) DETACH DELETE event",
    )
    await driver.execute_query(
        "MATCH (run:DiscoveryRun {run_id: 'eli-986-e2e'}) DETACH DELETE run",
    )


@pytest.mark.asyncio
@pytest.mark.local_neo4j
async def test_replaying_retained_source_is_idempotent():
    driver = AsyncGraphDatabase.driver(
        "bolt://localhost:7688", auth=("neo4j", "local-research-password")
    )
    try:
        source = RetainedSource.build(
            requested_url="https://fixture.example/eli-986",
            final_url="https://fixture.example/eli-986",
            content="ELI-986 Mercy uses ELI-986 Widget.",
            retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
            discovery_run_id="eli-986-e2e",
            entrypoint="fixture",
        )
        await cleanup_fixture(driver, source.source_id)
        store = DiscoveryStore(driver)
        await retain_for_fixture(store, source)
        graph = build_ingestion_graph(
            IngestionDependencies(
                store=store,
                graph=LocalGraphAdapter(driver),
                extractor=FixtureExtractor(),
            )
        )

        work = ExtractionWork.build(source.source_id, "customers", "fixture-v1", revision=1)
        first = await graph.ainvoke({"extraction": work})
        second = await graph.ainvoke({"extraction": work})
        records, _, _ = await driver.execute_query(
            "MATCH (episode:Episode {source_id: $source_id, active: true})-[:MENTIONS]->"
            "(entity:Entity) "
            "RETURN count(entity) AS mentions",
            source_id=source.source_id,
        )

        assert first["outcome"] == second["outcome"] == "succeeded", {
            "first": first.get("persistence_error"),
            "second": second.get("persistence_error"),
        }
        assert records[0]["mentions"] == 2
    finally:
        if "source" in locals():
            await cleanup_fixture(driver, source.source_id)
        await driver.close()


@pytest.mark.asyncio
@pytest.mark.local_neo4j
async def test_distinct_sources_keep_distinct_relationship_evidence():
    driver = AsyncGraphDatabase.driver(
        "bolt://localhost:7688", auth=("neo4j", "local-research-password")
    )
    sources = [
        RetainedSource.build(
            requested_url=f"https://fixture.example/eli-986/{index}",
            final_url=f"https://fixture.example/eli-986/{index}",
            content="ELI-986 Mercy uses ELI-986 Widget.",
            retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
            discovery_run_id="eli-986-e2e",
            entrypoint="fixture",
        )
        for index in range(2)
    ]
    try:
        for retained in sources:
            await cleanup_fixture(driver, retained.source_id)
        store = DiscoveryStore(driver)
        graph = build_ingestion_graph(
            IngestionDependencies(
                store=store,
                graph=LocalGraphAdapter(driver),
                extractor=FixtureExtractor(),
            )
        )
        for retained in sources:
            await retain_for_fixture(store, retained)
            work = ExtractionWork.build(retained.source_id, "customers", "fixture-v1", revision=1)
            await graph.ainvoke({"extraction": work})

        records, _, _ = await driver.execute_query(
            "MATCH (:Entity)-[edge:RELATES_TO {name: 'Adopts'}]->(:Entity) "
            "WHERE edge.source_id IN $source_ids RETURN count(edge) AS evidence_count",
            source_ids=[retained.source_id for retained in sources],
        )

        assert records[0]["evidence_count"] == 2
    finally:
        for retained in sources:
            await cleanup_fixture(driver, retained.source_id)
        await driver.close()


async def retain_for_fixture(store: DiscoveryStore, source: RetainedSource) -> None:
    target = ResearchTarget.build("ELI-986 Fixture", "https://fixture.example")
    await store.load_or_create_target(target)
    run = DiscoveryRun(
        run_id=source.discovery_run_id,
        entrypoint="fixture",
        started_at=source.retrieved_at,
    )
    await store.start_run(run)
    await store.upsert_frontier(
        FrontierRecord(
            target_id=target.target_id,
            canonical_url=source.final_url,
            requested_url=source.requested_url,
            origin="first_party",
            status=FrontierStatus.PENDING,
            discovery_method="fixture",
            discovery_run_id=run.run_id,
        )
    )
    await store.retain_for_target(target.target_id, source)


class ScriptedExtractor:
    """Returns a fixed, caller-supplied assertion list regardless of input --
    used to simulate what a given extraction revision's LLM call returned."""

    def __init__(self, assertions: list[Assertion]):
        self._assertions = assertions

    async def extract(self, source, profiles, seeded_entities):
        return self._assertions


async def cleanup_supersession_fixture(driver, source_id: str, entity_names: list[str]) -> None:
    await driver.execute_query(
        "MATCH (episode:Episode {source_id: $source_id}) DETACH DELETE episode",
        source_id=source_id,
    )
    await driver.execute_query(
        "MATCH (:Entity)-[edge:RELATES_TO {source_id: $source_id}]->(:Entity) DELETE edge",
        source_id=source_id,
    )
    entity_uuids = [f"new:HealthSystem:{name}" for name in entity_names] + [
        "new:Product:ELI-1010 Widget"
    ]
    await driver.execute_query(
        "MATCH (entity:Entity) WHERE entity.uuid IN $entity_uuids DETACH DELETE entity",
        entity_uuids=entity_uuids,
    )
    await driver.execute_query(
        "MATCH (run:ExtractionRun)-[:EXTRACTED]->"
        "(source:RetainedSource {source_id: $source_id}) "
        "DETACH DELETE run",
        source_id=source_id,
    )
    await driver.execute_query(
        "MATCH (event:RetrievalEvent {run_id: 'eli-1010-supersession-e2e'}) DETACH DELETE event",
    )
    await driver.execute_query(
        "MATCH (run:DiscoveryRun {run_id: 'eli-1010-supersession-e2e'}) DETACH DELETE run",
    )


async def _active_adopts_facts(driver, source_id: str) -> dict[str, str]:
    """Map of active customer (subject) name -> fact, for this source."""
    records, _, _ = await driver.execute_query(
        "MATCH (h:Entity)-[edge:RELATES_TO {name: 'Adopts', source_id: $source_id, active: true}]"
        "->(:Entity) RETURN h.name AS name, edge.fact AS fact",
        source_id=source_id,
    )
    return {record["name"]: record["fact"] for record in records}


def _adopts_assertion(subject: str, quote: str) -> Assertion:
    return Assertion(
        subject=subject,
        subject_type="HealthSystem",
        relation="Adopts",
        object="ELI-1010 Widget",
        object_type="Product",
        quote=quote,
        profile="customers",
    )


@pytest.mark.asyncio
@pytest.mark.local_neo4j
async def test_reextraction_keeps_unreproduced_edge_active():
    """Core 1020.1 case: a rerun that doesn't reproduce a prior fact must not
    silently deactivate it -- absence isn't retraction."""
    driver = AsyncGraphDatabase.driver(
        "bolt://localhost:7688", auth=("neo4j", "local-research-password")
    )
    source = RetainedSource.build(
        requested_url="https://fixture.example/eli-1010-supersession",
        final_url="https://fixture.example/eli-1010-supersession",
        content=(
            "ELI-1010 Alpha Health uses the ELI-1010 Widget. "
            "ELI-1010 Beta Health uses the ELI-1010 Widget."
        ),
        retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
        discovery_run_id="eli-1010-supersession-e2e",
        entrypoint="fixture",
    )
    names = ["ELI-1010 Alpha Health", "ELI-1010 Beta Health"]
    try:
        await cleanup_supersession_fixture(driver, source.source_id, names)
        store = DiscoveryStore(driver)
        await retain_for_fixture(store, source)

        # Revision 1: both Alpha and Beta extracted.
        graph_r1 = build_ingestion_graph(
            IngestionDependencies(
                store=store,
                graph=LocalGraphAdapter(driver),
                extractor=ScriptedExtractor(
                    [
                        _adopts_assertion(
                            "ELI-1010 Alpha Health",
                            "ELI-1010 Alpha Health uses the ELI-1010 Widget.",
                        ),
                        _adopts_assertion(
                            "ELI-1010 Beta Health",
                            "ELI-1010 Beta Health uses the ELI-1010 Widget.",
                        ),
                    ]
                ),
            )
        )
        work_r1 = ExtractionWork.build(source.source_id, "customers", "fixture-v1", revision=1)
        result_r1 = await graph_r1.ainvoke({"extraction": work_r1})
        assert result_r1["outcome"] == "succeeded"
        assert set((await _active_adopts_facts(driver, source.source_id)).keys()) == set(names)

        # Revision 2 (forced rerun): only Alpha reproduced, Beta not mentioned.
        graph_r2 = build_ingestion_graph(
            IngestionDependencies(
                store=store,
                graph=LocalGraphAdapter(driver),
                extractor=ScriptedExtractor(
                    [
                        _adopts_assertion(
                            "ELI-1010 Alpha Health",
                            "ELI-1010 Alpha Health uses the ELI-1010 Widget.",
                        )
                    ]
                ),
            )
        )
        work_r2 = ExtractionWork.build(source.source_id, "customers", "fixture-v1", revision=2)
        result_r2 = await graph_r2.ainvoke({"extraction": work_r2})
        assert result_r2["outcome"] == "succeeded"

        active = await _active_adopts_facts(driver, source.source_id)
        assert set(active.keys()) == set(names), (
            "Beta's edge must survive a rerun that simply didn't reproduce it"
        )
    finally:
        await cleanup_supersession_fixture(driver, source.source_id, names)
        await driver.close()


@pytest.mark.asyncio
@pytest.mark.local_neo4j
async def test_reextraction_replaces_reproduced_edge_with_updated_fact():
    """True supersession: when a later revision DOES reproduce the same
    (subject, relation, object) triple, the old edge is replaced, not
    duplicated -- exactly one active edge remains, with the new fact."""
    driver = AsyncGraphDatabase.driver(
        "bolt://localhost:7688", auth=("neo4j", "local-research-password")
    )
    source = RetainedSource.build(
        requested_url="https://fixture.example/eli-1010-supersession-replace",
        final_url="https://fixture.example/eli-1010-supersession-replace",
        content=(
            "ELI-1010 Gamma Health uses the ELI-1010 Widget. "
            "ELI-1010 Gamma Health rolled out the ELI-1010 Widget across every clinic."
        ),
        retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
        discovery_run_id="eli-1010-supersession-e2e",
        entrypoint="fixture",
    )
    names = ["ELI-1010 Gamma Health"]
    try:
        await cleanup_supersession_fixture(driver, source.source_id, names)
        store = DiscoveryStore(driver)
        await retain_for_fixture(store, source)

        graph_r1 = build_ingestion_graph(
            IngestionDependencies(
                store=store,
                graph=LocalGraphAdapter(driver),
                extractor=ScriptedExtractor(
                    [
                        _adopts_assertion(
                            "ELI-1010 Gamma Health",
                            "ELI-1010 Gamma Health uses the ELI-1010 Widget.",
                        )
                    ]
                ),
            )
        )
        work_r1 = ExtractionWork.build(source.source_id, "customers", "fixture-v1", revision=1)
        await graph_r1.ainvoke({"extraction": work_r1})

        new_quote = "ELI-1010 Gamma Health rolled out the ELI-1010 Widget across every clinic."
        graph_r2 = build_ingestion_graph(
            IngestionDependencies(
                store=store,
                graph=LocalGraphAdapter(driver),
                extractor=ScriptedExtractor(
                    [_adopts_assertion("ELI-1010 Gamma Health", new_quote)]
                ),
            )
        )
        work_r2 = ExtractionWork.build(source.source_id, "customers", "fixture-v1", revision=2)
        await graph_r2.ainvoke({"extraction": work_r2})

        active = await _active_adopts_facts(driver, source.source_id)
        assert active == {"ELI-1010 Gamma Health": new_quote}
    finally:
        await cleanup_supersession_fixture(driver, source.source_id, names)
        await driver.close()


@pytest.mark.asyncio
@pytest.mark.local_neo4j
async def test_total_abstention_rerun_leaves_all_prior_edges_active():
    """Guard case: a revision that extracts nothing at all must not wipe any
    prior active edges."""
    driver = AsyncGraphDatabase.driver(
        "bolt://localhost:7688", auth=("neo4j", "local-research-password")
    )
    source = RetainedSource.build(
        requested_url="https://fixture.example/eli-1010-supersession-abstain",
        final_url="https://fixture.example/eli-1010-supersession-abstain",
        content="ELI-1010 Delta Health uses the ELI-1010 Widget.",
        retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
        discovery_run_id="eli-1010-supersession-e2e",
        entrypoint="fixture",
    )
    names = ["ELI-1010 Delta Health"]
    try:
        await cleanup_supersession_fixture(driver, source.source_id, names)
        store = DiscoveryStore(driver)
        await retain_for_fixture(store, source)

        graph_r1 = build_ingestion_graph(
            IngestionDependencies(
                store=store,
                graph=LocalGraphAdapter(driver),
                extractor=ScriptedExtractor(
                    [
                        _adopts_assertion(
                            "ELI-1010 Delta Health",
                            "ELI-1010 Delta Health uses the ELI-1010 Widget.",
                        )
                    ]
                ),
            )
        )
        work_r1 = ExtractionWork.build(source.source_id, "customers", "fixture-v1", revision=1)
        await graph_r1.ainvoke({"extraction": work_r1})
        assert set((await _active_adopts_facts(driver, source.source_id)).keys()) == set(names)

        graph_r2 = build_ingestion_graph(
            IngestionDependencies(
                store=store, graph=LocalGraphAdapter(driver), extractor=ScriptedExtractor([])
            )
        )
        work_r2 = ExtractionWork.build(source.source_id, "customers", "fixture-v1", revision=2)
        result_r2 = await graph_r2.ainvoke({"extraction": work_r2})
        assert result_r2["outcome"] == IngestionOutcome.NO_FACTS.value

        active = await _active_adopts_facts(driver, source.source_id)
        assert set(active.keys()) == set(names), "total abstention must not wipe prior active edges"
    finally:
        await cleanup_supersession_fixture(driver, source.source_id, names)
        await driver.close()


@pytest.mark.asyncio
@pytest.mark.local_neo4j
async def test_uuid_stability_across_revisions_documents_adversarial_duplication():
    """1020.1's invariant is keyed on entity uuid, not raw name -- confirm the
    identical-name case resolves to the same uuid and replaces cleanly (no
    duplication), then document what happens when a later revision spells the
    same real-world entity differently: resolution mints a new uuid (exact
    match, per graph/resolve.py + LocalGraphAdapter.find_entities), the old
    edge is never touched because its triple isn't reproduced, and the result
    is two active edges for what is really one customer. This is a known,
    documented limitation of 1020.1 (converts data loss into duplication risk
    for near-duplicate spellings), not something this test is meant to fix --
    see the entity-resolution fuzzy-matching work tracked separately."""
    driver = AsyncGraphDatabase.driver(
        "bolt://localhost:7688", auth=("neo4j", "local-research-password")
    )
    source = RetainedSource.build(
        requested_url="https://fixture.example/eli-1010-supersession-uuid",
        final_url="https://fixture.example/eli-1010-supersession-uuid",
        content=(
            "ELI-1010 Duplicate Co uses the ELI-1010 Widget. "
            "ELI-1010 Duplicate Company uses the ELI-1010 Widget."
        ),
        retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
        discovery_run_id="eli-1010-supersession-e2e",
        entrypoint="fixture",
    )
    stable_name = "ELI-1010 Duplicate Co"
    # Genuinely different normalized key from stable_name -- NOT just a
    # trailing-period variant, since normalize_text() rstrip(".")s the whole
    # string and would silently collapse "Co." back to "Co", defeating the
    # adversarial case this test exists to document.
    renamed = "ELI-1010 Duplicate Company"
    names = [stable_name, renamed]
    try:
        await cleanup_supersession_fixture(driver, source.source_id, names)
        store = DiscoveryStore(driver)
        await retain_for_fixture(store, source)

        graph_r1 = build_ingestion_graph(
            IngestionDependencies(
                store=store,
                graph=LocalGraphAdapter(driver),
                extractor=ScriptedExtractor(
                    [_adopts_assertion(stable_name, f"{stable_name} uses the ELI-1010 Widget.")]
                ),
            )
        )
        work_r1 = ExtractionWork.build(source.source_id, "customers", "fixture-v1", revision=1)
        await graph_r1.ainvoke({"extraction": work_r1})

        # Revision 2 re-extracts the IDENTICAL name string: same uuid, clean
        # replacement, no duplication.
        graph_r2 = build_ingestion_graph(
            IngestionDependencies(
                store=store,
                graph=LocalGraphAdapter(driver),
                extractor=ScriptedExtractor(
                    [
                        _adopts_assertion(
                            stable_name, f"{stable_name} uses the ELI-1010 Widget, still."
                        )
                    ]
                ),
            )
        )
        work_r2 = ExtractionWork.build(source.source_id, "customers", "fixture-v1", revision=2)
        await graph_r2.ainvoke({"extraction": work_r2})
        active_after_same_name = await _active_adopts_facts(driver, source.source_id)
        assert set(active_after_same_name.keys()) == {stable_name}, (
            "identical name across revisions must resolve to the same uuid and replace cleanly"
        )

        # Revision 3 (adversarial): a differently-spelled mention of what a
        # human would recognize as the same real-world entity.
        graph_r3 = build_ingestion_graph(
            IngestionDependencies(
                store=store,
                graph=LocalGraphAdapter(driver),
                extractor=ScriptedExtractor(
                    [_adopts_assertion(renamed, f"{renamed} uses the ELI-1010 Widget.")]
                ),
            )
        )
        work_r3 = ExtractionWork.build(source.source_id, "customers", "fixture-v1", revision=3)
        await graph_r3.ainvoke({"extraction": work_r3})

        active_after_rename = await _active_adopts_facts(driver, source.source_id)
        # Documented, accepted limitation: a differently-spelled mention mints
        # a new uuid, so BOTH spellings end up active -- this is duplication,
        # not the data-loss bug 1020.1 targets. Entity-resolution fuzzy
        # matching (tracked separately) is the real fix for this axis.
        assert set(active_after_rename.keys()) == {stable_name, renamed}
    finally:
        await cleanup_supersession_fixture(driver, source.source_id, names)
        await driver.close()
