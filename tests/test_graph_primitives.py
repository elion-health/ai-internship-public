from datetime import UTC, datetime

import pytest

from ai_internship.discovery.contracts import RetainedSource
from ai_internship.enrichment.contracts import CandidateEntity, SeededEntity
from ai_internship.enrichment.grounding import quote_is_grounded
from ai_internship.graph.edges import GraphEdge, collapse_edges, edge_pair_allowed
from ai_internship.graph.local import LocalGraphAdapter
from ai_internship.graph.persist import persist_grounded_graph
from ai_internship.graph.resolve import resolve_entities


def test_quote_grounding_normalizes_whitespace():
    assert quote_is_grounded("Health  System\nuses Product", "Health System uses Product.")


def test_collapse_edges_merges_same_typed_edge_facts():
    collapsed = collapse_edges(
        [
            GraphEdge(source_uuid="a", relation="Adopts", target_uuid="b", fact="first"),
            GraphEdge(source_uuid="a", relation="Adopts", target_uuid="b", fact="second"),
        ]
    )

    assert len(collapsed) == 1
    assert collapsed[0].fact == "first | second"


def test_collapse_edges_keeps_opposite_directions_distinct():
    collapsed = collapse_edges(
        [
            GraphEdge(source_uuid="a", relation="Integrates", target_uuid="b", fact="a to b"),
            GraphEdge(source_uuid="b", relation="Integrates", target_uuid="a", fact="b to a"),
        ]
    )

    assert len(collapsed) == 2


def test_edge_gate_rejects_vendor_adopts_product():
    assert not edge_pair_allowed("Vendor", "Adopts", "Product")


class FakeGraph:
    graphiti = object()

    async def find_entities(self, label, normalized_name):
        return [{"uuid": "seeded-acme"}] if normalized_name == "acme" else []

    async def persist_graphiti(self, **kwargs):
        self.episode_uuid = kwargs["episode_uuid"]
        self.mentions_written = True


@pytest.mark.asyncio
async def test_resolution_reuses_exact_seeded_entity():
    resolved = await resolve_entities(
        FakeGraph(), [CandidateEntity(label="Vendor", name="Acme", quote="Acme")]
    )

    assert resolved[0].status == "matched"
    assert resolved[0].entity_uuid == "seeded-acme"


@pytest.mark.asyncio
async def test_resolution_reuses_supplied_seeded_candidate_without_lookup():
    class Graph:
        async def find_entities(self, label, normalized_name):
            raise AssertionError("seeded candidates should resolve without another query")

    resolved = await resolve_entities(
        Graph(),
        [CandidateEntity(label="Product", name="Acme Platform", quote="Acme Platform")],
        [SeededEntity(label="Product", name="Acme Platform", entity_uuid="product-1")],
    )

    assert resolved[0].status == "matched"
    assert resolved[0].entity_uuid == "product-1"


@pytest.mark.asyncio
async def test_local_graph_finds_seeded_entities_named_in_source():
    class Record:
        def data(self):
            return {"label": "Product", "name": "Acme Platform", "entity_uuid": "product-1"}

    class Driver:
        async def execute_query(self, query, **params):
            self.query = query
            self.params = params
            return [Record()], None, None

    driver = Driver()
    candidates = await LocalGraphAdapter(driver).find_seed_candidates(
        {"Product", "HealthSystem"}, "Mercy Health uses Acme Platform"
    )

    assert candidates == [
        SeededEntity(label="Product", name="Acme Platform", entity_uuid="product-1")
    ]
    assert driver.params == {
        "labels": ["HealthSystem", "Product"],
        "content": "Mercy Health uses Acme Platform",
    }
    assert "entity.seed_id IS NOT NULL" in driver.query


@pytest.mark.asyncio
async def test_local_graph_rejects_seed_candidate_matching_only_a_substring():
    """Observed live: the Cypher CONTAINS pre-filter is a plain substring match,
    so a seed entity named "Medica" would "match" a page that only contains
    the unrelated word "medical". The word-boundary check must reject it even
    though Cypher's coarse filter returned it."""

    class Record:
        def data(self):
            return {"label": "HealthSystem", "name": "Medica", "entity_uuid": "medica-1"}

    class Driver:
        async def execute_query(self, query, **params):
            return [Record()], None, None

    candidates = await LocalGraphAdapter(Driver()).find_seed_candidates(
        {"HealthSystem"}, "This is a medical device company."
    )

    assert candidates == []


@pytest.mark.asyncio
async def test_local_graph_initializes_graphiti_indexes_once():
    class Graphiti:
        def __init__(self):
            self.calls = 0

        async def build_indices_and_constraints(self):
            self.calls += 1

    graphiti = Graphiti()
    adapter = LocalGraphAdapter(object(), graphiti)

    await adapter.ensure_graphiti_indexes()
    await adapter.ensure_graphiti_indexes()

    assert graphiti.calls == 1


@pytest.mark.asyncio
async def test_persistence_assigns_episode_and_mentions():
    source = RetainedSource.build(
        requested_url="https://example.com",
        final_url="https://example.com",
        content="Acme makes Widget",
        retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
        discovery_run_id="run-1",
        entrypoint="test",
    )
    graph = FakeGraph()

    await persist_grounded_graph(
        graph,
        source,
        [],
        [GraphEdge(source_uuid="a", relation="Makes", target_uuid="b", fact="Acme makes Widget")],
    )

    assert graph.episode_uuid == source.source_id
    assert graph.mentions_written


@pytest.mark.asyncio
async def test_local_graph_persists_graphiti_nodes_edges_and_embeddings(monkeypatch):
    from ai_internship.enrichment.contracts import ResolvedEntity

    class Graphiti:
        driver = object()
        embedder = object()

    class Driver:
        async def execute_query(self, query, **params):
            if "RETURN entity.uuid" in query:
                return [Record("vendor-1", {"seed_id": "seed-acme", "tier": "free"})], None, None
            captured["provenance_query"] = (query, params)

    class Record:
        def __init__(self, uuid, attributes):
            self.uuid = uuid
            self.attributes = attributes

        def data(self):
            return {"uuid": self.uuid, "attributes": self.attributes}

    captured = {}

    async def add_bulk(driver, episodes, mentions, entities, edges, embedder):
        captured.update(
            driver=driver,
            episodes=episodes,
            mentions=mentions,
            entities=entities,
            edges=edges,
            embedder=embedder,
        )

    monkeypatch.setattr("ai_internship.graph.local.add_nodes_and_edges_bulk", add_bulk)
    source = RetainedSource.build(
        requested_url="https://example.com",
        final_url="https://example.com",
        content="Acme makes Widget",
        retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
        discovery_run_id="run-1",
        entrypoint="test",
    )

    await LocalGraphAdapter(Driver(), Graphiti()).persist_graphiti(
        episode_uuid="extract-1",
        source=source,
        nodes=[
            ResolvedEntity(label="Vendor", name="Acme", quote="Acme", entity_uuid="vendor-1"),
            ResolvedEntity(label="Product", name="Widget", quote="Widget"),
        ],
        edges=[
            GraphEdge(
                source_uuid="vendor-1",
                relation="Makes",
                target_uuid="new:Product:Widget",
                fact="Acme makes Widget",
            )
        ],
        provenance={"source_id": source.source_id, "profile": "customers", "active": False},
    )

    assert captured["driver"] is Graphiti.driver
    assert captured["embedder"] is Graphiti.embedder
    assert captured["episodes"][0].uuid == "extract-1"
    assert captured["episodes"][0].group_id == "ai-internship"
    assert {node.uuid for node in captured["entities"]} == {"vendor-1", "new:Product:Widget"}
    assert {node.labels[0] for node in captured["entities"]} == {"Vendor", "Product"}
    assert next(node for node in captured["entities"] if node.uuid == "vendor-1").attributes == {
        "seed_id": "seed-acme",
        "tier": "free",
    }
    assert captured["edges"][0].episodes == ["extract-1"]
    assert captured["edges"][0].attributes["profile"] == "customers"
    assert captured["edges"][0].attributes["extraction_id"] == "extract-1"
    assert {edge.source_node_uuid for edge in captured["mentions"]} == {"extract-1"}
    assert "Episodic" in captured["provenance_query"][0]
