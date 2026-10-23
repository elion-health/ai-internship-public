"""Idempotent episode and evidence persistence through a Graphiti-compatible boundary."""

from __future__ import annotations

from ai_internship.discovery.contracts import RetainedSource
from ai_internship.enrichment.contracts import ExtractionWork

from .edges import GraphEdge


async def persist_grounded_graph(
    graph,
    source: RetainedSource,
    nodes: list,
    edges: list[GraphEdge],
    extraction: ExtractionWork | None = None,
) -> None:
    """Persist the retained source as its stable episode and link all grounded facts."""
    provenance = {
        "source_id": source.source_id,
        "source_url": source.final_url,
        "title": source.title,
        "retrieved_at": source.retrieved_at.isoformat(),
        "content_hash": source.content_hash,
    }
    episode_uuid = source.source_id
    if extraction:
        episode_uuid = extraction.extraction_id
        provenance.update(
            {
                "profile": extraction.profile,
                "extractor_version": extraction.extractor_version,
                "revision": extraction.revision,
                "active": False,
            }
        )
    await graph.persist_graphiti(
        episode_uuid=episode_uuid,
        source=source,
        nodes=nodes,
        edges=edges,
        provenance=provenance,
    )
    if extraction:
        await graph.activate_extraction(
            episode_uuid=episode_uuid,
            source_id=source.source_id,
            profile=extraction.profile,
        )
