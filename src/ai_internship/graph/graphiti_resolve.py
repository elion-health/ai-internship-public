"""Entity resolution via graphiti's built-in fuzzy matching (ELI-1057),
replacing the hand-rolled exact-match lookup in resolve.py's resolve_entities
-- a name variant like "OncoEMR" vs "OncoEMR®" currently mints a duplicate
node instead of resolving to the same entity.

The seeded-entity fast path (an entity already confirmed present on this page
at extraction time) is kept exactly as before -- free, deterministic, and it
preserves the "ambiguous" signal when two seeded candidates share a name,
which graphiti's own resolution has no equivalent for (ties there escalate to
its LLM tier and it always returns a definite answer or "new"). Anything not
resolved by a seed falls through to graphiti instead of our own exact-match
Cypher lookup -- this also subsumes the old target-name-paraphrase special
case (a paraphrase of the research target, e.g. "OncoEMR" for target
"Flatiron OncoEMR"), since the target's own node is already in the candidate
pool graphiti fuzzy-matches against.

Graphiti stores entity-name embeddings and retrieves persisted semantic
candidates through native similarity search scoped to the entity label.
"""

from __future__ import annotations

from datetime import UTC, datetime

from graphiti_core.graphiti_types import GraphitiClients
from graphiti_core.nodes import EntityNode
from graphiti_core.search.search_filters import SearchFilters
from graphiti_core.utils.maintenance.node_operations import (
    node_similarity_search,
    resolve_extracted_nodes,
)

from ai_internship.enrichment.contracts import CandidateEntity, ResolvedEntity, SeededEntity
from ai_internship.enrichment.grounding import normalize_text

_GROUP_ID = "ai-internship"

# Products/Vendors are only ever supposed to come from the marketplace
# dataset (seeded before crawling -- see DiscoveryStore.seed_marketplace_entity),
# never minted from crawled text. HealthSystem is the one label a crawl is
# allowed to discover fresh, since a new customer relationship is exactly
# what customer-profile extraction is for. An unresolved Product/Vendor
# candidate gets status="unmatched" instead of a placeholder node, so it's
# dropped before grounding rather than becoming a fake catalog entry.
_CREATABLE_LABELS = {"HealthSystem"}


def _entity_node(*, name: str, label: str, uuid: str | None = None) -> EntityNode:
    return EntityNode(
        uuid=uuid or f"pending:{label}:{name}",
        name=name,
        group_id=_GROUP_ID,
        labels=[label],
        created_at=datetime.now(UTC),
    )


async def resolve_entities_via_graphiti(
    clients: GraphitiClients,
    graph,
    entities: list[CandidateEntity],
    seeded_entities: list[SeededEntity] | None = None,
    target_name: str = "",
) -> list[ResolvedEntity]:
    """Same contract as resolve.py's resolve_entities, but anything not
    resolved for free by a seeded candidate falls through to Graphiti's native
    similarity search instead of our own exact-match Cypher lookup. `target_name` is
    accepted for interface parity with resolve_entities but isn't used
    directly -- see module docstring for why it's no longer needed."""
    del graph, target_name
    if not entities:
        return []

    seeded: dict[tuple[str, str], list[SeededEntity]] = {}
    for seed in seeded_entities or []:
        key = (seed.label, normalize_text(seed.name).lower())
        seeded.setdefault(key, []).append(seed)

    resolved: list[ResolvedEntity | None] = [None] * len(entities)
    unresolved_indices: list[int] = []
    for index, entity in enumerate(entities):
        key = (entity.label, normalize_text(entity.name).lower())
        candidates = seeded.get(key, [])
        if len(candidates) == 1:
            # Keep the existing entity's canonical name, not this extraction's
            # variant -- persist_mentions unconditionally overwrites the node's
            # name with whatever's on the resolved entity, so a mismatch here
            # would rename the canonical node every time a differently-cased
            # or differently-punctuated variant gets extracted.
            resolved[index] = ResolvedEntity(
                **{**entity.model_dump(), "name": candidates[0].name},
                status="matched",
                entity_uuid=candidates[0].entity_uuid,
            )
        elif len(candidates) > 1:
            resolved[index] = ResolvedEntity(**entity.model_dump(), status="ambiguous")
        else:
            unresolved_indices.append(index)

    if unresolved_indices:
        new_nodes_by_label: dict[str, list[EntityNode]] = {}

        def _unresolved(entity: CandidateEntity) -> ResolvedEntity:
            if entity.label not in _CREATABLE_LABELS:
                return ResolvedEntity(**entity.model_dump(), status="unmatched")
            new_nodes_by_label.setdefault(entity.label, []).append(
                _entity_node(
                    name=entity.name,
                    label=entity.label,
                    uuid=f"new:{entity.label}:{entity.name}",
                )
            )
            return ResolvedEntity(**entity.model_dump(), status="new")

        for index in unresolved_indices:
            entity = entities[index]
            extracted = _entity_node(name=entity.name, label=entity.label)
            await extracted.generate_name_embedding(clients.embedder)
            matches = await node_similarity_search(
                clients.driver,
                extracted.name_embedding,
                SearchFilters(node_labels=[entity.label]),
                group_ids=[_GROUP_ID],
                limit=10,
            )
            matches.extend(new_nodes_by_label.get(entity.label, []))
            if not matches:
                resolved[index] = _unresolved(entity)
                continue
            resolved_nodes, _uuid_map, _duplicates = await resolve_extracted_nodes(
                clients,
                [extracted],
                existing_nodes_override=matches,
            )
            match = resolved_nodes[0]
            # resolve_extracted_nodes runs its own unrestricted-by-label
            # semantic search internally (graphiti_core, not ours) and merges
            # those candidates in -- so it can hand back a real node whose
            # label doesn't match what we're resolving for (a HealthSystem
            # mentioned as the object of an "X Integrates Y" sentence gets
            # extracted with label="Product" by that relation's fixed shape,
            # and graphiti happily matches it back to the HealthSystem node by
            # name alone). Reject a match whose label doesn't agree with the
            # one we searched for, same as no match at all.
            if match.uuid == extracted.uuid or entity.label not in match.labels:
                resolved[index] = _unresolved(entity)
                continue
            resolved[index] = ResolvedEntity(
                **{**entity.model_dump(), "name": match.name},
                status="matched",
                entity_uuid=match.uuid,
            )

    return resolved
