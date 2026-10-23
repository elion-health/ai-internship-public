"""Conservative seeded-graph entity resolution."""

from __future__ import annotations

from ai_internship.enrichment.contracts import CandidateEntity, ResolvedEntity, SeededEntity
from ai_internship.enrichment.grounding import _endpoint_matches_target, normalize_text


async def resolve_entities(
    graph,
    entities: list[CandidateEntity],
    seeded_entities: list[SeededEntity] | None = None,
    target_name: str = "",
) -> list[ResolvedEntity]:
    seeded: dict[tuple[str, str], list[SeededEntity]] = {}
    for entity in seeded_entities or []:
        key = (entity.label, normalize_text(entity.name).lower())
        seeded.setdefault(key, []).append(entity)
    resolved: list[ResolvedEntity] = []
    for entity in entities:
        key = (entity.label, normalize_text(entity.name).lower())
        candidates = seeded.get(key, [])
        if len(candidates) == 1:
            resolved.append(
                ResolvedEntity(
                    **entity.model_dump(), status="matched", entity_uuid=candidates[0].entity_uuid
                )
            )
            continue
        if len(candidates) > 1:
            resolved.append(ResolvedEntity(**entity.model_dump(), status="ambiguous"))
            continue
        matches = await graph.find_entities(entity.label, normalize_text(entity.name).lower())
        if not matches and target_name and _endpoint_matches_target(entity.name, target_name):
            # A paraphrase/abbreviation of the research target itself (e.g. "OncoEMR"
            # or "Flatiron's OncoEMR" for target "Flatiron OncoEMR") -- look up the
            # target's own canonical name instead of creating a duplicate node for
            # every phrasing variant.
            matches = await graph.find_entities(entity.label, normalize_text(target_name).lower())
        if len(matches) == 1:
            resolved.append(
                ResolvedEntity(
                    **entity.model_dump(), status="matched", entity_uuid=matches[0]["uuid"]
                )
            )
        elif len(matches) > 1:
            resolved.append(ResolvedEntity(**entity.model_dump(), status="ambiguous"))
        else:
            resolved.append(ResolvedEntity(**entity.model_dump(), status="new"))
    return resolved
