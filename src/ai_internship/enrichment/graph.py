"""Fixed LangGraph pipeline for source-grounded P0 ingestion."""

from __future__ import annotations

import operator
from typing import Annotated, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from loguru import logger

from ai_internship.discovery.contracts import RetainedSource
from ai_internship.enrichment.contracts import (
    AUTHORITATIVE_INGESTION_OUTCOMES,
    Assertion,
    CandidateEntity,
    ExtractionWork,
    GroundingRejection,
    IngestionOutcome,
    SeededEntity,
)
from ai_internship.enrichment.grounding import (
    content_may_support_profile,
    contiguous_page_span,
    full_rejection_reason,
    normalize_text,
    quote_is_contiguous,
    quote_is_grounded,
    quote_names_both_endpoints,
)
from ai_internship.enrichment.profiles import PROFILES
from ai_internship.enrichment.referent_resolution import (
    confirm_relation,
)
from ai_internship.graph.edges import GraphEdge, collapse_edges, edge_pair_allowed
from ai_internship.graph.graphiti_resolve import resolve_entities_via_graphiti
from ai_internship.graph.persist import persist_grounded_graph


class SourceStore(Protocol):
    async def load_source(self, source_id: str) -> RetainedSource | None: ...

    async def start_extraction(self, work: ExtractionWork) -> None: ...

    async def finish_extraction(
        self,
        work: ExtractionWork,
        outcome: str,
        grounding_rejections: list[GroundingRejection],
    ) -> None: ...


class Extractor(Protocol):
    async def extract(
        self,
        source: RetainedSource,
        profiles: list[str],
        seeded_entities: list[SeededEntity],
        target_name: str = "",
    ) -> list[Assertion]: ...


class IngestionDependencies:
    def __init__(
        self,
        store: SourceStore,
        graph,
        extractor: Extractor,
        chat_model=None,
        graphiti_clients=None,
    ):
        self.store = store
        self.graph = graph
        self.extractor = extractor
        self.chat_model = chat_model
        self.graphiti_clients = graphiti_clients

    async def resolve_candidate_entities(self, entities, seeded_entities, target_name):
        if self.graphiti_clients is None:
            raise RuntimeError("Graphiti clients are required for entity resolution")
        await self.graph.ensure_graphiti_indexes()
        return await resolve_entities_via_graphiti(
            self.graphiti_clients,
            self.graph,
            entities,
            seeded_entities,
            target_name=target_name,
        )


class GraphState(TypedDict, total=False):
    extraction: ExtractionWork
    source: RetainedSource
    assertions: list[Assertion]
    seeded_entities: list[SeededEntity]
    entities: list[CandidateEntity]
    resolved: list
    entities_by_extracted_name: dict[tuple[str, str], object]
    edges: list[GraphEdge]
    outcome: str | None
    persistence_error: str
    persistence_diagnostics: dict[str, object]
    grounding_rejections: list[GroundingRejection]
    accepted_assertions: list[Assertion]
    referent_retry: dict[str, object]
    trace_audit: Annotated[list[dict[str, object]], operator.add]
    trace_output: dict[str, object]


def build_ingestion_graph(deps: IngestionDependencies):
    def audit_fact(assertion: Assertion) -> dict[str, str | None]:
        return {
            "subject": assertion.subject,
            "subject_type": assertion.subject_type,
            "relation": assertion.relation,
            "object": assertion.object,
            "object_type": assertion.object_type,
            "quote": assertion.quote[:500],
        }

    def capped_audit_facts(assertions: list[Assertion]) -> list[dict[str, str | None]]:
        return [audit_fact(assertion) for assertion in assertions[:10]]

    def capped_audit_rejections(
        rejections: list[GroundingRejection],
    ) -> list[dict[str, str | None]]:
        return [
            {"code": rejection.code, **audit_fact(rejection.assertion)}
            for rejection in rejections[:10]
        ]

    async def load_source(state: GraphState):
        work = ExtractionWork.model_validate(state["extraction"])
        await deps.store.start_extraction(work)
        source = await deps.store.load_source(work.source_id)
        return {
            "extraction": work,
            "source": source,
            "outcome": None if source else IngestionOutcome.NO_FACTS.value,
        }

    async def extract(state: GraphState):
        if state["outcome"]:
            return {}
        work = state["extraction"]
        profiles = [work.profile] if work.profile in PROFILES else []
        if not profiles:
            return {"profiles": [], "outcome": IngestionOutcome.NO_PROFILE.value}
        source_context = f"{state['source'].title or ''}\n{state['source'].content}"
        if not content_may_support_profile(
            source_context, work.profile, target_name=work.target_name
        ):
            return {"profiles": profiles, "outcome": IngestionOutcome.NO_FACTS.value}
        labels = {label for profile in profiles for label in PROFILES[profile].labels}
        seeded_entities = await deps.graph.find_seed_candidates(labels, state["source"].content)
        assertions = await deps.extractor.extract(
            state["source"], profiles, seeded_entities, target_name=work.target_name
        )
        return {
            "profiles": profiles,
            "assertions": assertions,
            "seeded_entities": seeded_entities,
            "outcome": None if assertions else IngestionOutcome.NO_FACTS.value,
            "trace_audit": [{"assertions": capped_audit_facts(assertions)}],
        }

    async def resolve(state: GraphState):
        if state["outcome"]:
            return {}
        candidates: dict[tuple[str, str], CandidateEntity] = {}
        for assertion in state["assertions"]:
            candidates[(assertion.subject_type, assertion.subject)] = CandidateEntity(
                label=assertion.subject_type, name=assertion.subject, quote=assertion.quote
            )
            if assertion.object and assertion.object_type:
                candidates[(assertion.object_type, assertion.object)] = CandidateEntity(
                    label=assertion.object_type, name=assertion.object, quote=assertion.quote
                )
        candidate_list = list(candidates.values())
        resolved = await deps.resolve_candidate_entities(
            candidate_list,
            state.get("seeded_entities", []),
            target_name=state["extraction"].target_name,
        )
        # Build mapping from extracted name keys to resolved entities.
        # resolve_candidate_entities returns results in the same order as
        # candidates, but with canonical names that may differ from extracted
        # names. We key by the original extracted name so _ground_assertions
        # can find the resolved entity using the assertion's extracted name.
        entities_by_extracted_name = {
            (candidate.label, candidate.name): resolved_item
            for candidate, resolved_item in zip(candidate_list, resolved)
        }
        outcome = (
            "resolution_ambiguity"
            if resolved and all(item.status == "ambiguous" for item in resolved)
            else None
        )
        return {
            "entities": candidate_list,
            "resolved": resolved,
            "entities_by_extracted_name": entities_by_extracted_name,
            "outcome": outcome,
            "trace_audit": [
                {
                    "resolution": {
                        "candidate_count": len(candidate_list),
                        "resolved_count": len(resolved),
                        "ambiguous_count": sum(item.status == "ambiguous" for item in resolved),
                    }
                }
            ],
        }

    async def _confirm(assertion, content, source_url, missing, origin):
        """The referent, with its failures treated as "no recovery" rather than
        as a reason to abandon the page."""
        try:
            found = await confirm_relation(
                subject=assertion.subject,
                object_name=assertion.object,
                relation=assertion.relation,
                quote=assertion.quote,
                unnamed=getattr(assertion, missing),
                content=content,
                source_url=source_url,
                chat_model=deps.chat_model,
                origin=origin,
            )
        except Exception as error:  # noqa: BLE001
            logger.warning("Referent confirmation failed, skipping: {}", repr(error))
            return None
        # Both halves travel. The sentence is the page's own words; the
        # argument is why they settle the relation, and it is the only thing
        # that carries the case the referent exists for -- a page stating a
        # relation across two sentences, or through its structure, where no
        # single sentence says it.
        return found if found else None

    async def _ground_assertions(
        assertions,
        entities,
        content,
        *,
        profile,
        target_name,
        source_url="",
        origin=None,
        assume_grounded_by_id=None,
        extra_context_by_id=None,
        extra_reasoning_by_id=None,
    ):
        assume_grounded_by_id = assume_grounded_by_id or {}
        # ELI-1544: the sentence the referent step used to justify a recovered
        # name. The semantic check judges the quote plus a subsection picked by
        # matching that quote -- which never names the recovered endpoint -- so
        # without this it is asked to confirm a name it was never shown.
        extra_context_by_id = extra_context_by_id or {}
        extra_reasoning_by_id = extra_reasoning_by_id or {}
        edges: list[GraphEdge] = []
        rejections: list[GroundingRejection] = []
        for assertion in assertions:
            if not (assertion.relation and assertion.object and assertion.object_type):
                rejections.append(
                    GroundingRejection(code="incomplete_relationship", assertion=assertion)
                )
                continue
            source = entities.get((assertion.subject_type, assertion.subject))
            target = entities.get((assertion.object_type, assertion.object))
            if not source or not target:
                rejections.append(
                    GroundingRejection(code="unresolved_endpoint", assertion=assertion)
                )
                continue
            assume = assume_grounded_by_id.get(id(assertion), frozenset())
            extra = extra_context_by_id.get(id(assertion), "")
            reasoning = extra_reasoning_by_id.get(id(assertion), "")
            # A spliced quote -- two real pieces of the page with the join
            # dropped -- is cut back to the piece that is genuinely one run, and
            # the page is read before anything is decided. Both names usually
            # survive inside a splice, which is exactly why the referent was
            # being skipped and the contiguity check left to kill the relation
            # with nothing having looked at the page. So the cut forces the
            # referent rather than leaving it to the names test below.
            #
            # Gated on quote_is_grounded (caught by review): the span helper
            # returns something whenever the quote and the page share any run
            # at all, so an invented quote with one incidental match would be
            # replaced by real page text and then pass both grounding and
            # contiguity. Only a quote the page already backs is repaired; the
            # rest still reject as quote_not_in_source.
            spliced = False
            if not quote_is_contiguous(assertion.quote, content) and quote_is_grounded(
                assertion.quote, content
            ):
                span = contiguous_page_span(assertion.quote, content)
                if span:
                    # In place, not model_copy (caught by review): rejection
                    # bookkeeping and the accepted-assertion list both match on
                    # id(), against the objects in state["assertions"]. A copy
                    # is a different object, so a rejected repair was reported
                    # as accepted, and a successful one left the audit showing
                    # the spliced quote rather than the text actually judged.
                    assertion.quote = span
                    spliced = True
            # ELI-1544/1549: a quote that names only one side can't settle the
            # relation on its own, but the page often can. Ask the referent
            # before the semantic check rather than rejecting outright, and hand
            # the sentence it finds to that check as evidence.
            # No model configured means no referent, which must mean "skip the
            # second look", not "reject" -- the semantic check no-ops the same
            # way, and tests and offline runs rely on it.
            if (
                deps.chat_model is None
                or assume
                or (quote_names_both_endpoints(assertion) and not spliced)
            ):
                pass
            else:
                missing = (
                    "subject"
                    if normalize_text(assertion.subject).lower()
                    not in normalize_text(assertion.quote).lower()
                    else "object"
                )
                confirmed = await _confirm(assertion, content, source_url, missing, origin)
                if confirmed is None:
                    rejections.append(
                        GroundingRejection(code="referent_declined", assertion=assertion)
                    )
                    continue
                (extra, reasoning), assume = confirmed, frozenset({missing})
            reason = await full_rejection_reason(
                assertion,
                content,
                profile=profile,
                target_name=target_name,
                source_url=source_url,
                assume_grounded=assume,
                extra_context=extra,
                extra_reasoning=reasoning,
                chat_model=deps.chat_model,
            )
            if reason:
                rejections.append(GroundingRejection(code=reason, assertion=assertion))
                continue
            if not edge_pair_allowed(
                assertion.subject_type, assertion.relation, assertion.object_type
            ):
                rejections.append(
                    GroundingRejection(code="schema_edge_not_allowed", assertion=assertion)
                )
                continue
            edges.append(
                GraphEdge(
                    source_uuid=source.entity_uuid or f"new:{source.label}:{source.name}",
                    relation=assertion.relation,
                    target_uuid=target.entity_uuid or f"new:{target.label}:{target.name}",
                    fact=assertion.quote,
                )
            )
        return edges, rejections

    def _accepted_entities(entities_by_key, edges):
        accepted_uuids = {
            entity_uuid for edge in edges for entity_uuid in (edge.source_uuid, edge.target_uuid)
        }
        return [
            item
            for item in entities_by_key.values()
            if (item.entity_uuid or f"new:{item.label}:{item.name}") in accepted_uuids
        ]

    async def verify_grounding(state: GraphState):
        if state["outcome"] and state["outcome"] != IngestionOutcome.RESOLUTION_AMBIGUITY.value:
            return {}
        # Use entities_by_extracted_name which maps from (label, extracted_name)
        # to resolved entities. This ensures _ground_assertions can look up
        # entities using the assertion's extracted name, even when the resolved
        # entity has a different canonical name.
        entities = {
            key: item
            for key, item in state.get("entities_by_extracted_name", {}).items()
            if item.status not in ("ambiguous", "unmatched")
        }
        edges, rejections = await _ground_assertions(
            state["assertions"],
            entities,
            state["source"].content,
            profile=state["extraction"].profile,
            target_name=state["extraction"].target_name,
            source_url=state["source"].final_url,
            # ELI-1550: which prompt the referent uses depends on whose page
            # this is, so the origin has to reach it.
            origin=state["source"].entrypoint,
        )
        accepted_edges = collapse_edges(edges)
        accepted_entities = _accepted_entities(entities, accepted_edges)
        rejected_assertion_ids = {id(rejection.assertion) for rejection in rejections}
        accepted_assertions = [
            assertion
            for assertion in state["assertions"]
            if id(assertion) not in rejected_assertion_ids
        ]
        return {
            "resolved": accepted_entities,
            "edges": accepted_edges,
            "grounding_rejections": rejections,
            "accepted_assertions": accepted_assertions,
            "outcome": state["outcome"]
            or (None if accepted_edges else IngestionOutcome.GROUNDING_FAILURE.value),
            "trace_audit": [
                {
                    "accepted_facts": capped_audit_facts(accepted_assertions),
                    "grounding_rejections": capped_audit_rejections(rejections),
                }
            ],
        }

    async def persist(state: GraphState):
        outcome = state["outcome"]
        if outcome and (outcome not in AUTHORITATIVE_INGESTION_OUTCOMES or not state.get("source")):
            return {}
        try:
            await persist_grounded_graph(
                deps.graph,
                state["source"],
                state.get("resolved", []) if not outcome else [],
                state.get("edges", []) if not outcome else [],
                extraction=state.get("extraction"),
            )
        except Exception as exc:
            diagnostics = {
                "extraction_id": state["extraction"].extraction_id,
                "source_url": state["source"].final_url,
                "profile": state["extraction"].profile,
                "resolved_entity_count": len(state.get("resolved", [])),
                "edge_count": len(state.get("edges", [])),
                "exception_type": type(exc).__name__,
                "exception_repr": repr(exc),
            }
            logger.exception("Graph persistence failed: {}", diagnostics)
            return {
                "outcome": IngestionOutcome.PERSISTENCE_FAILURE.value,
                "persistence_error": f"{type(exc).__name__}: {exc!r}",
                "persistence_diagnostics": diagnostics,
                "trace_audit": [{"persistence_diagnostics": diagnostics}],
            }
        audit = {"persistence_diagnostics": state.get("persistence_diagnostics")}
        return (
            {"trace_audit": [audit]}
            if outcome
            else {
                "outcome": IngestionOutcome.SUCCEEDED.value,
                "trace_audit": [audit],
            }
        )

    async def record_outcome(state: GraphState):
        if state["outcome"] is None:
            raise RuntimeError("ingestion reached record_outcome without a terminal outcome")
        await deps.store.finish_extraction(
            state["extraction"], state["outcome"], state.get("grounding_rejections", [])
        )
        return {
            "trace_output": {
                "nodes": [
                    {
                        "id": item.entity_uuid or f"new:{item.label}:{item.name}",
                        "label": item.label,
                        "name": item.name,
                    }
                    for item in state.get("resolved", [])
                ],
                "persistence_diagnostics": state.get("persistence_diagnostics"),
                "audit": {
                    "extraction": {
                        "id": state["extraction"].extraction_id,
                        "source_url": state["source"].final_url,
                        "profile": state["extraction"].profile,
                    },
                    "assertions": next(
                        (
                            item["assertions"]
                            for item in state.get("trace_audit", [])
                            if "assertions" in item
                        ),
                        [],
                    ),
                    "accepted_facts": capped_audit_facts(state.get("accepted_assertions", [])),
                    "grounding_rejections": capped_audit_rejections(
                        state.get("grounding_rejections", [])
                    ),
                    "referent_retry": state.get("referent_retry"),
                    "persistence_diagnostics": state.get("persistence_diagnostics"),
                },
            }
        }

    graph = StateGraph(GraphState)
    for name, node in (
        ("load_source", load_source),
        ("extract", extract),
        ("resolve", resolve),
        ("verify_grounding", verify_grounding),
        ("persist", persist),
        ("record_outcome", record_outcome),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "load_source")
    graph.add_edge("load_source", "extract")
    graph.add_edge("extract", "resolve")
    graph.add_edge("resolve", "verify_grounding")
    graph.add_edge("verify_grounding", "persist")
    graph.add_edge("persist", "record_outcome")
    graph.add_edge("record_outcome", END)
    return graph.compile()
