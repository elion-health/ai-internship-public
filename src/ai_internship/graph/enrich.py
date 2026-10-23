"""Structured attribute enrichment while preserving source evidence."""

from __future__ import annotations

from ai_internship.discovery.contracts import RetainedSource
from ai_internship.enrichment.contracts import CandidateEntity


async def enrich_entities(chat_model, entities: list[CandidateEntity], source: RetainedSource):
    """Delegate optional field completion without allowing provenance replacement."""
    if chat_model is None:
        return entities
    enriched = await chat_model.enrich(entities, source.content)
    return [
        entity.model_copy(update={"quote": entity.quote or source.content}) for entity in enriched
    ]
