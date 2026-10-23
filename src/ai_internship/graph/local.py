"""Neo4j adapter for resolved P0 entities, typed edges, and provenance mentions."""

from __future__ import annotations

from datetime import UTC, datetime

from graphiti_core.edges import EntityEdge
from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode
from graphiti_core.utils.bulk_utils import add_nodes_and_edges_bulk
from graphiti_core.utils.maintenance.edge_operations import build_episodic_edges

from ai_internship.enrichment.contracts import SeededEntity
from ai_internship.enrichment.grounding import _contains_endpoint, normalize_text

_GROUP_ID = "ai-internship"


class LocalGraphAdapter:
    def __init__(self, driver, graphiti=None):
        self.driver = driver
        self.graphiti = graphiti
        self._graphiti_indexes_ready = False

    async def find_entities(self, label, normalized_name):
        records, _, _ = await self.driver.execute_query(
            "MATCH (entity:Entity) WHERE $label IN labels(entity) "
            "AND toLower(entity.name) = $name RETURN coalesce(entity.uuid, entity.seed_id) AS uuid",
            label=label,
            name=normalized_name,
        )
        return [record.data() for record in records]

    async def find_all_entities(self, label: str) -> list[dict]:
        """All existing entities of a given label, for feeding graphiti's
        resolve_extracted_nodes() as existing_nodes_override (see ELI-1057) --
        our entities have no graphiti-compatible embeddings, so graphiti's own
        DB-side semantic search over them finds nothing; we build the
        candidate pool ourselves instead."""
        records, _, _ = await self.driver.execute_query(
            "MATCH (entity:Entity) WHERE $label IN labels(entity) "
            "RETURN coalesce(entity.uuid, entity.seed_id) AS uuid, entity.name AS name",
            label=label,
        )
        return [record.data() for record in records]

    async def find_seed_candidates(self, labels: set[str], content: str) -> list[SeededEntity]:
        # The Cypher CONTAINS check below is a coarse pre-filter only -- it's a
        # plain substring match, so "Medica" would "match" a page that only
        # contains the unrelated word "medical". Every candidate it returns is
        # re-checked against the real word-boundary rule before being trusted.
        records, _, _ = await self.driver.execute_query(
            "MATCH (entity:Entity) "
            "WHERE entity.seed_id IS NOT NULL "
            "AND any(label IN labels(entity) WHERE label IN $labels) "
            "AND size(trim(entity.name)) >= 3 "
            "AND toLower($content) CONTAINS toLower(entity.name) "
            "RETURN [label IN labels(entity) WHERE label IN $labels][0] AS label, "
            "entity.name AS name, coalesce(entity.uuid, entity.seed_id) AS entity_uuid",
            labels=sorted(labels),
            content=content,
        )
        normalized_content = normalize_text(content).lower()
        candidates = [record.data() for record in records]
        return [
            SeededEntity.model_validate(candidate)
            for candidate in candidates
            if _contains_endpoint(normalized_content, candidate["name"])
        ]

    async def persist_episode(self, **kwargs):
        await self.driver.execute_query(
            "MERGE (episode:Episode {uuid: $uuid}) "
            "SET episode.content = $content, episode += $provenance",
            uuid=kwargs["episode_uuid"],
            content=kwargs["content"],
            provenance=kwargs["provenance"],
        )

    async def ensure_graphiti_indexes(self):
        if self.graphiti is None:
            raise RuntimeError("Graphiti is required for graph persistence")
        if not self._graphiti_indexes_ready:
            await self.graphiti.build_indices_and_constraints()
            self._graphiti_indexes_ready = True

    async def persist_graphiti(self, *, episode_uuid, source, nodes, edges, provenance):
        """Write a fully Graphiti-compatible episode and its provenance."""
        if self.graphiti is None:
            raise RuntimeError("Graphiti is required for graph persistence")
        now = datetime.now(UTC)
        existing_uuids = [node.entity_uuid for node in nodes if node.entity_uuid]
        existing_attributes: dict[str, dict] = {}
        if existing_uuids:
            records, _, _ = await self.driver.execute_query(
                "MATCH (entity:Entity) WHERE entity.uuid IN $uuids "
                "RETURN entity.uuid AS uuid, properties(entity) AS attributes",
                uuids=existing_uuids,
            )
            existing_attributes = {
                record.data()["uuid"]: record.data()["attributes"] for record in records
            }
        entity_nodes = [
            EntityNode(
                uuid=node.entity_uuid or f"new:{node.label}:{node.name}",
                name=node.name,
                group_id=_GROUP_ID,
                labels=[node.label],
                created_at=now,
                attributes={
                    **existing_attributes.get(node.entity_uuid or "", {}),
                    **node.attributes,
                },
            )
            for node in nodes
        ]
        entity_edges = [
            EntityEdge(
                uuid=f"{episode_uuid}:{edge.source_uuid}:{edge.relation}:{edge.target_uuid}",
                source_node_uuid=edge.source_uuid,
                target_node_uuid=edge.target_uuid,
                group_id=_GROUP_ID,
                name=edge.relation,
                fact=edge.fact,
                episodes=[episode_uuid],
                created_at=now,
                reference_time=source.retrieved_at,
                attributes={**provenance, "extraction_id": episode_uuid},
            )
            for edge in edges
        ]
        episode = EpisodicNode(
            uuid=episode_uuid,
            name=source.title or source.source_id,
            group_id=_GROUP_ID,
            source=EpisodeType.text,
            source_description=source.final_url,
            content=source.content,
            valid_at=source.retrieved_at,
            created_at=now,
            entity_edges=[edge.uuid for edge in entity_edges],
            episode_metadata=provenance,
        )
        await add_nodes_and_edges_bulk(
            self.graphiti.driver,
            [episode],
            build_episodic_edges(entity_nodes, episode_uuid, now),
            entity_nodes,
            entity_edges,
            self.graphiti.embedder,
        )
        await self.driver.execute_query(
            "MATCH (episode:Episodic {uuid: $episode_uuid}) SET episode += $provenance",
            episode_uuid=episode_uuid,
            provenance=provenance,
        )

    async def persist_mentions(self, **kwargs):
        for node in kwargs["nodes"]:
            entity_uuid = node.entity_uuid or f"new:{node.label}:{node.name}"
            await self.driver.execute_query(
                "MERGE (episode:Episode {uuid: $episode_uuid}) "
                "MERGE (entity:Entity {uuid: $entity_uuid}) "
                "SET entity.name = $name, entity:$($label), entity += $attributes "
                "MERGE (episode)-[:MENTIONS]->(entity)",
                episode_uuid=kwargs["episode_uuid"],
                entity_uuid=entity_uuid,
                name=node.name,
                label=node.label,
                attributes=node.attributes,
            )
        for edge in kwargs["edges"]:
            await self.driver.execute_query(
                "MATCH (source:Entity {uuid: $source_uuid}) "
                "MATCH (target:Entity {uuid: $target_uuid}) "
                "MERGE (source)-[edge:RELATES_TO {name: $name, extraction_id: $episode_uuid}]->"
                "(target) SET edge.fact = $fact, edge.source_id = $source_id, "
                "edge.profile = $profile, edge.extractor_version = $extractor_version, "
                "edge.revision = $revision, edge.active = $active",
                source_uuid=edge["source_uuid"],
                target_uuid=edge["target_uuid"],
                name=edge["relation"],
                fact=edge["fact"],
                episode_uuid=kwargs["episode_uuid"],
                source_id=kwargs["provenance"].get("source_id"),
                profile=kwargs["provenance"].get("profile"),
                extractor_version=kwargs["provenance"].get("extractor_version"),
                revision=kwargs["provenance"].get("revision"),
                active=kwargs["provenance"].get("active", True),
            )

    async def activate_extraction(self, *, episode_uuid, source_id, profile):
        """Supersede the prior revision's edges only where this revision actually
        reproduces the same (subject, relation, object) fact. An edge the new
        revision doesn't mention is left active -- absence isn't treated as
        retraction, since nothing here can tell "deliberately rejected" apart
        from "this LLM call just didn't reproduce it" (see
        docs/experiments/eli-1010-stateful-graph-eval.md)."""
        await self.driver.execute_query(
            "MATCH (current:Episodic {uuid: $episode_uuid}) "
            "OPTIONAL MATCH (s)-[current_edge:RELATES_TO {extraction_id: $episode_uuid}]->(t) "
            "WITH current, collect(current_edge) AS current_edges, "
            "collect(DISTINCT CASE WHEN current_edge IS NOT NULL "
            "THEN [s.uuid, current_edge.name, t.uuid] END) AS raw_triples "
            "WITH current, current_edges, "
            "[triple IN raw_triples WHERE triple IS NOT NULL] AS current_triples "
            "OPTIONAL MATCH (old:Episodic {source_id: $source_id, profile: $profile}) "
            "WHERE old.active = true AND old.uuid <> $episode_uuid "
            "SET old.active = false "
            "WITH current, current_edges, current_triples "
            "OPTIONAL MATCH (os)-[old_edge:RELATES_TO "
            "{source_id: $source_id, profile: $profile}]->(ot) "
            "WHERE old_edge.extraction_id <> $episode_uuid "
            "AND [os.uuid, old_edge.name, ot.uuid] IN current_triples "
            "SET old_edge.active = false "
            "WITH current, current_edges "
            "SET current.active = true "
            "FOREACH (edge IN current_edges | SET edge.active = true)",
            episode_uuid=episode_uuid,
            source_id=source_id,
            profile=profile,
        )

    async def list_active_relationships(self, source_id):
        records, _, _ = await self.driver.execute_query(
            "MATCH ()-[edge:RELATES_TO {source_id: $source_id, active: true}]->() "
            "RETURN edge.profile AS profile, edge.fact AS fact, edge.revision AS revision",
            source_id=source_id,
        )
        return [record.data() for record in records]
