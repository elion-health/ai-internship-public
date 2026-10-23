"""Unit tests for our own glue code in graphiti_resolve.py -- building the
EntityNode inputs Graphiti expects and mapping its native, label-scoped
similarity search output back to our ResolvedEntity contract.
"""

import pytest

from ai_internship.enrichment.contracts import CandidateEntity, SeededEntity
from ai_internship.graph import graphiti_resolve


class _FakeGraph:
    def __init__(self, entities_by_label):
        self.entities_by_label = entities_by_label
        self.calls = []

    async def find_all_entities(self, label):
        self.calls.append(label)
        return self.entities_by_label.get(label, [])


class _FakeEntityNode:
    def __init__(self, uuid, name="", labels=None):
        self.uuid = uuid
        self.name = name
        self.labels = labels or []
        self.name_embedding = None

    async def generate_name_embedding(self, embedder):
        self.name_embedding = [0.5]


class _FakeClients:
    driver = object()
    embedder = object()


def _patch_entity_nodes(monkeypatch):
    monkeypatch.setattr(
        graphiti_resolve,
        "_entity_node",
        lambda *, name, label, uuid=None: _FakeEntityNode(
            uuid or f"pending:{label}:{name}", name, [label]
        ),
    )


@pytest.mark.asyncio
async def test_resolve_entities_via_graphiti_returns_empty_for_no_entities():
    graph = _FakeGraph({})
    resolved = await graphiti_resolve.resolve_entities_via_graphiti(
        clients=object(), graph=graph, entities=[]
    )
    assert resolved == []
    assert graph.calls == []


@pytest.mark.asyncio
async def test_resolve_entities_via_graphiti_uses_label_scoped_candidates_for_deduplication(
    monkeypatch,
):
    calls = []
    dedupe_calls = []

    async def fake_node_similarity_search(driver, embedding, filters, *, group_ids, limit):
        calls.append((filters.node_labels, group_ids, limit))
        return [_FakeEntityNode(uuid="candidate", name="candidate", labels=filters.node_labels)]

    async def fake_resolve_extracted_nodes(clients, extracted_nodes, *, existing_nodes_override):
        dedupe_calls.append(existing_nodes_override)
        return extracted_nodes, {}, []

    _patch_entity_nodes(monkeypatch)
    monkeypatch.setattr(graphiti_resolve, "node_similarity_search", fake_node_similarity_search)
    monkeypatch.setattr(graphiti_resolve, "resolve_extracted_nodes", fake_resolve_extracted_nodes)

    entities = [
        CandidateEntity(label="Product", name="OncoEMR(R)", quote="q"),
        CandidateEntity(label="HealthSystem", name="Mission Cancer + Blood", quote="q"),
    ]
    await graphiti_resolve.resolve_entities_via_graphiti(
        clients=_FakeClients(), graph=object(), entities=entities
    )

    assert calls == [
        (["Product"], ["ai-internship"], 10),
        (["HealthSystem"], ["ai-internship"], 10),
    ]
    assert [[node.uuid for node in candidates] for candidates in dedupe_calls] == [
        ["candidate"],
        ["candidate"],
    ]


@pytest.mark.asyncio
async def test_resolve_entities_via_graphiti_reports_matched_when_uuid_changes(monkeypatch):
    """A fuzzy match must keep the existing entity's canonical name, not the
    extracted variant -- persist_mentions unconditionally overwrites the
    node's name from the resolved entity, so matching "OncoEMR(R)" to the
    real "OncoEMR®" node must not rename it to "OncoEMR(R)"."""
    graph = _FakeGraph({"Product": [{"uuid": "prod-1", "name": "OncoEMR®"}]})

    async def fake_node_similarity_search(*args, **kwargs):
        return [_FakeEntityNode(uuid="prod-1", name="OncoEMR®", labels=["Product"])]

    async def fake_resolve_extracted_nodes(clients, extracted_nodes, *, existing_nodes_override):
        return existing_nodes_override, {}, []

    _patch_entity_nodes(monkeypatch)
    monkeypatch.setattr(graphiti_resolve, "node_similarity_search", fake_node_similarity_search)
    monkeypatch.setattr(graphiti_resolve, "resolve_extracted_nodes", fake_resolve_extracted_nodes)

    entities = [CandidateEntity(label="Product", name="OncoEMR(R)", quote="q")]
    resolved = await graphiti_resolve.resolve_entities_via_graphiti(
        clients=_FakeClients(), graph=graph, entities=entities
    )

    assert len(resolved) == 1
    assert resolved[0].status == "matched"
    assert resolved[0].entity_uuid == "prod-1"
    assert resolved[0].name == "OncoEMR®"


@pytest.mark.asyncio
async def test_resolve_entities_via_graphiti_reports_new_when_uuid_is_unchanged(monkeypatch):
    """HealthSystem is the one label a crawl is allowed to mint fresh -- a new
    customer relationship is exactly what customer-profile extraction finds."""
    graph = _FakeGraph({"HealthSystem": []})

    async def fake_node_similarity_search(*args, **kwargs):
        return []

    _patch_entity_nodes(monkeypatch)
    monkeypatch.setattr(graphiti_resolve, "node_similarity_search", fake_node_similarity_search)

    entities = [CandidateEntity(label="HealthSystem", name="A Brand New Hospital", quote="q")]
    resolved = await graphiti_resolve.resolve_entities_via_graphiti(
        clients=_FakeClients(), graph=graph, entities=entities
    )

    assert len(resolved) == 1
    assert resolved[0].status == "new"
    assert resolved[0].entity_uuid is None


@pytest.mark.asyncio
async def test_resolve_entities_via_graphiti_reports_unmatched_instead_of_new_product(
    monkeypatch,
):
    """Product/Vendor nodes only ever come from the marketplace dataset seed
    (ELI-1289) -- an unresolved Product candidate must not mint a placeholder
    node, it must be dropped instead."""
    graph = _FakeGraph({"Product": []})

    async def fake_node_similarity_search(*args, **kwargs):
        return []

    _patch_entity_nodes(monkeypatch)
    monkeypatch.setattr(graphiti_resolve, "node_similarity_search", fake_node_similarity_search)

    entities = [CandidateEntity(label="Product", name="A Brand New Product", quote="q")]
    resolved = await graphiti_resolve.resolve_entities_via_graphiti(
        clients=_FakeClients(), graph=graph, entities=entities
    )

    assert len(resolved) == 1
    assert resolved[0].status == "unmatched"
    assert resolved[0].entity_uuid is None


@pytest.mark.asyncio
async def test_resolve_entities_via_graphiti_keeps_new_when_deduplication_rejects_candidate(
    monkeypatch,
):
    async def fake_node_similarity_search(*args, **kwargs):
        return [
            _FakeEntityNode(uuid="other-hospital", name="Other Hospital", labels=["HealthSystem"])
        ]

    async def fake_resolve_extracted_nodes(clients, extracted_nodes, *, existing_nodes_override):
        return extracted_nodes, {}, []

    _patch_entity_nodes(monkeypatch)
    monkeypatch.setattr(graphiti_resolve, "node_similarity_search", fake_node_similarity_search)
    monkeypatch.setattr(graphiti_resolve, "resolve_extracted_nodes", fake_resolve_extracted_nodes)

    resolved = await graphiti_resolve.resolve_entities_via_graphiti(
        clients=_FakeClients(),
        graph=object(),
        entities=[CandidateEntity(label="HealthSystem", name="New Hospital", quote="q")],
    )

    assert resolved[0].status == "new"
    assert resolved[0].entity_uuid is None


@pytest.mark.asyncio
async def test_resolve_entities_via_graphiti_reports_unmatched_when_dedup_rejects_product(
    monkeypatch,
):
    """Same dedup-rejects-candidate path as above, but for a Product -- must
    still come back unmatched, never a placeholder node."""

    async def fake_node_similarity_search(*args, **kwargs):
        return [_FakeEntityNode(uuid="other-product", name="Other Product", labels=["Product"])]

    async def fake_resolve_extracted_nodes(clients, extracted_nodes, *, existing_nodes_override):
        return extracted_nodes, {}, []

    _patch_entity_nodes(monkeypatch)
    monkeypatch.setattr(graphiti_resolve, "node_similarity_search", fake_node_similarity_search)
    monkeypatch.setattr(graphiti_resolve, "resolve_extracted_nodes", fake_resolve_extracted_nodes)

    resolved = await graphiti_resolve.resolve_entities_via_graphiti(
        clients=_FakeClients(),
        graph=object(),
        entities=[CandidateEntity(label="Product", name="New Product", quote="q")],
    )

    assert resolved[0].status == "unmatched"
    assert resolved[0].entity_uuid is None


@pytest.mark.asyncio
async def test_resolve_entities_via_graphiti_deduplicates_new_aliases_within_batch(monkeypatch):
    async def fake_node_similarity_search(*args, **kwargs):
        return []

    async def fake_resolve_extracted_nodes(clients, extracted_nodes, *, existing_nodes_override):
        node = existing_nodes_override[0] if existing_nodes_override else extracted_nodes[0]
        return [node], {}, []

    _patch_entity_nodes(monkeypatch)
    monkeypatch.setattr(graphiti_resolve, "node_similarity_search", fake_node_similarity_search)
    monkeypatch.setattr(graphiti_resolve, "resolve_extracted_nodes", fake_resolve_extracted_nodes)

    resolved = await graphiti_resolve.resolve_entities_via_graphiti(
        clients=_FakeClients(),
        graph=object(),
        entities=[
            CandidateEntity(label="HealthSystem", name="Mission Cancer", quote="q"),
            CandidateEntity(label="HealthSystem", name="Mission Cancer Center", quote="q"),
        ],
    )

    assert [entity.status for entity in resolved] == ["new", "matched"]
    assert resolved[1].entity_uuid == "new:HealthSystem:Mission Cancer"


@pytest.mark.asyncio
async def test_resolve_entities_via_graphiti_does_not_alias_unmatched_products_within_batch(
    monkeypatch,
):
    """Since an unmatched Product never becomes a placeholder node, a second
    Product alias in the same batch has nothing to dedupe against either --
    both must independently come back unmatched."""

    async def fake_node_similarity_search(*args, **kwargs):
        return []

    _patch_entity_nodes(monkeypatch)
    monkeypatch.setattr(graphiti_resolve, "node_similarity_search", fake_node_similarity_search)

    resolved = await graphiti_resolve.resolve_entities_via_graphiti(
        clients=_FakeClients(),
        graph=object(),
        entities=[
            CandidateEntity(label="Product", name="OncoEMR", quote="q"),
            CandidateEntity(label="Product", name="OncoEMR(R)", quote="q"),
        ],
    )

    assert [entity.status for entity in resolved] == ["unmatched", "unmatched"]
    assert [entity.entity_uuid for entity in resolved] == [None, None]


@pytest.mark.asyncio
async def test_resolve_entities_via_graphiti_rejects_a_cross_label_match(monkeypatch):
    """resolve_extracted_nodes runs its own label-unrestricted search
    internally and can hand back a real node under the wrong label (e.g. a
    HealthSystem mentioned as the object of an Integrates sentence gets
    extracted as label="Product" and would otherwise get matched back to the
    HealthSystem node by name alone). A match whose real label disagrees with
    what we searched for must be rejected, not accepted."""

    async def fake_node_similarity_search(*args, **kwargs):
        return [_FakeEntityNode(uuid="other-product", name="Other Product", labels=["Product"])]

    async def fake_resolve_extracted_nodes(clients, extracted_nodes, *, existing_nodes_override):
        return (
            [_FakeEntityNode(uuid="health-1", name="Priority Health", labels=["HealthSystem"])],
            {},
            [],
        )

    _patch_entity_nodes(monkeypatch)
    monkeypatch.setattr(graphiti_resolve, "node_similarity_search", fake_node_similarity_search)
    monkeypatch.setattr(graphiti_resolve, "resolve_extracted_nodes", fake_resolve_extracted_nodes)

    resolved = await graphiti_resolve.resolve_entities_via_graphiti(
        clients=_FakeClients(),
        graph=object(),
        entities=[CandidateEntity(label="Product", name="Priority Health", quote="q")],
    )

    assert resolved[0].status == "unmatched"
    assert resolved[0].entity_uuid is None


@pytest.mark.asyncio
async def test_resolve_entities_via_graphiti_prefers_a_seeded_match_for_free(monkeypatch):
    """A seeded candidate (already confirmed present on this page at
    extraction time) resolves for free -- graphiti must not even be called
    for it."""
    graph = _FakeGraph({"Product": [{"uuid": "prod-1", "name": "OncoEMR®"}]})

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("graphiti should not be called for a seeded match")

    monkeypatch.setattr(graphiti_resolve, "node_similarity_search", fail_if_called)

    entities = [CandidateEntity(label="Product", name="OncoEMR®", quote="q")]
    seeded = [SeededEntity(label="Product", name="OncoEMR®", entity_uuid="seed-1")]
    resolved = await graphiti_resolve.resolve_entities_via_graphiti(
        clients=object(), graph=graph, entities=entities, seeded_entities=seeded
    )

    assert resolved[0].status == "matched"
    assert resolved[0].entity_uuid == "seed-1"
    assert graph.calls == []


@pytest.mark.asyncio
async def test_resolve_entities_via_graphiti_seeded_match_keeps_canonical_name(monkeypatch):
    """Same canonical-name-preservation rule applies to the seeded fast path --
    matching a normalized-equal but differently-cased variant must not rename
    the seed's canonical entity."""
    graph = _FakeGraph({})

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("graphiti should not be called for a seeded match")

    monkeypatch.setattr(graphiti_resolve, "node_similarity_search", fail_if_called)

    entities = [CandidateEntity(label="Product", name="oncoemr®", quote="q")]
    seeded = [SeededEntity(label="Product", name="OncoEMR®", entity_uuid="seed-1")]
    resolved = await graphiti_resolve.resolve_entities_via_graphiti(
        clients=object(), graph=graph, entities=entities, seeded_entities=seeded
    )

    assert resolved[0].status == "matched"
    assert resolved[0].entity_uuid == "seed-1"
    assert resolved[0].name == "OncoEMR®"


@pytest.mark.asyncio
async def test_resolve_entities_via_graphiti_reports_ambiguous_for_two_seeded_matches(
    monkeypatch,
):
    """Two seeded candidates sharing a name is exactly the case graphiti's own
    resolution has no equivalent for (it escalates ties to its LLM tier and
    always returns a definite answer) -- preserved as its own fast path so we
    keep refusing to guess here."""
    graph = _FakeGraph({})

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("graphiti should not be called for an ambiguous seeded match")

    monkeypatch.setattr(graphiti_resolve, "node_similarity_search", fail_if_called)

    entities = [CandidateEntity(label="Product", name="OncoEMR®", quote="q")]
    seeded = [
        SeededEntity(label="Product", name="OncoEMR®", entity_uuid="seed-1"),
        SeededEntity(label="Product", name="OncoEMR®", entity_uuid="seed-2"),
    ]
    resolved = await graphiti_resolve.resolve_entities_via_graphiti(
        clients=object(), graph=graph, entities=entities, seeded_entities=seeded
    )

    assert resolved[0].status == "ambiguous"
    assert resolved[0].entity_uuid is None


@pytest.mark.asyncio
async def test_resolve_entities_via_graphiti_mixes_seeded_and_graphiti_resolved(monkeypatch):
    """A batch with one seeded entity and one that needs graphiti -- only the
    unresolved one should reach graphiti, and results must line up with the
    original order."""
    graph = _FakeGraph({"HealthSystem": [{"uuid": "health-1", "name": "Mission Cancer + Blood"}]})

    async def fake_node_similarity_search(*args, **kwargs):
        return [
            _FakeEntityNode(uuid="health-1", name="Mission Cancer + Blood", labels=["HealthSystem"])
        ]

    async def fake_resolve_extracted_nodes(clients, extracted_nodes, *, existing_nodes_override):
        return existing_nodes_override, {}, []

    _patch_entity_nodes(monkeypatch)
    monkeypatch.setattr(graphiti_resolve, "node_similarity_search", fake_node_similarity_search)
    monkeypatch.setattr(graphiti_resolve, "resolve_extracted_nodes", fake_resolve_extracted_nodes)

    entities = [
        CandidateEntity(label="Product", name="OncoEMR®", quote="q"),
        CandidateEntity(label="HealthSystem", name="Mission Cancer + Blood", quote="q"),
    ]
    seeded = [SeededEntity(label="Product", name="OncoEMR®", entity_uuid="seed-1")]
    resolved = await graphiti_resolve.resolve_entities_via_graphiti(
        clients=_FakeClients(), graph=graph, entities=entities, seeded_entities=seeded
    )

    assert resolved[0].name == "OncoEMR®"
    assert resolved[0].status == "matched"
    assert resolved[0].entity_uuid == "seed-1"
    assert resolved[1].name == "Mission Cancer + Blood"
    assert resolved[1].status == "matched"
    assert resolved[1].entity_uuid == "health-1"
