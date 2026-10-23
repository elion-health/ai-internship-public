"""Constrained P0 relationship validation and fact collapsing."""

from __future__ import annotations

from pydantic import BaseModel

from .schema import EDGE_TYPE_MAP


class GraphEdge(BaseModel):
    source_uuid: str
    relation: str
    target_uuid: str
    fact: str


def edge_pair_allowed(source_label: str, relation: str, target_label: str) -> bool:
    return relation in EDGE_TYPE_MAP.get((source_label, target_label), set())


def collapse_edges(edges: list[GraphEdge]) -> list[GraphEdge]:
    collapsed: dict[tuple[str, str, str], GraphEdge] = {}
    for edge in edges:
        key = (edge.source_uuid, edge.relation, edge.target_uuid)
        existing = collapsed.get(key)
        if existing is None:
            collapsed[key] = edge
        elif edge.fact not in existing.fact.split(" | "):
            collapsed[key] = existing.model_copy(update={"fact": f"{existing.fact} | {edge.fact}"})
    return list(collapsed.values())
