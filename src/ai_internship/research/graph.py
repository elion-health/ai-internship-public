"""Stateful two-pass research orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from operator import add
from typing import Annotated, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from ai_internship.discovery.contracts import (
    DiscoveryCandidate,
    DiscoveryRun,
    ExternalDiscoveryResult,
    canonicalize_url,
)
from ai_internship.enrichment.profiles import PROFILES, extractor_version
from ai_internship.research.contracts import ResearchRequest, ResearchTarget

# ELI-1485: how many first-party-confirmed names to hand the external agent as
# search leads. The A/B found the agent follows the lead instruction with a
# short list and ignores it entirely once fed 19-31 names, so the cap is the
# part that makes this work.
KNOWN_ENTITY_LEAD_CAP = 8

# The endpoint label that is *not* the vendor, per profile: customers extract
# HealthSystem -> Adopts -> Product, integrations Product -> Integrates ->
# Product. Only the non-vendor endpoint is a useful lead.
_LEAD_LABELS = {"customers": {"HealthSystem"}, "integrations": {"Product"}}


def _first_party_leads(state: ResearchState, profile: str) -> list[str]:
    """Names first-party extraction already confirmed, as leads for external search.

    Deliberately not a set: order is the order extraction found them, so the cap
    keeps the earliest-confirmed names rather than an arbitrary sample.
    """
    labels = _LEAD_LABELS.get(profile, PROFILES[profile].labels)
    target_name = state["target"].name.strip().lower()
    leads: list[str] = []
    seen: set[str] = set()
    for result in state.get("extraction_results", []):
        if result.get("profile") != profile or result.get("outcome") == "failed":
            continue
        for node in result.get("nodes", []):
            name = str(node.get("name") or "").strip()
            key = name.lower()
            if not name or key == target_name or key in seen:
                continue
            if node.get("label") not in labels:
                continue
            seen.add(key)
            leads.append(name)
            if len(leads) >= KNOWN_ENTITY_LEAD_CAP:
                return leads
    return leads


class ResearchDependencies:
    def __init__(
        self,
        *,
        store,
        first_party,
        ingestion_graph,
        embedder=None,
        ingestion_model_name="",
        external_engine=None,
    ):
        self.store = store
        self.first_party = first_party
        self.ingestion_graph = ingestion_graph
        self.embedder = embedder
        self.ingestion_model_name = ingestion_model_name
        self.external_engine = external_engine


class ResearchState(TypedDict, total=False):
    request: ResearchRequest
    target: ResearchTarget
    run: DiscoveryRun
    first_party_source_ids: list[str]
    external_source_ids: list[str]
    acquisition_phase: Literal["first_party", "external"]
    extraction_source_ids: list[str]
    extraction_force_profiles: list[str]
    extraction_work: list[object]
    extraction_results: list[dict[str, object]]
    trace_output: dict[str, object]
    external_discoveries: Annotated[list[object], add]
    profile: str
    known_entities: list[str]
    metrics: dict[str, object]
    errors: list[str]
    status: str


def build_research_graph(deps: ResearchDependencies):
    def merge_candidate(
        current: DiscoveryCandidate | None, candidate: DiscoveryCandidate
    ) -> DiscoveryCandidate:
        if current is None:
            return candidate
        return current.model_copy(
            update={
                "title": current.title or candidate.title,
                "snippet": current.snippet or candidate.snippet,
                "parent_url": current.parent_url or candidate.parent_url,
                "depth": min(current.depth, candidate.depth),
                "queries": sorted(set(current.queries + candidate.queries)),
                "discovery_methods": sorted(
                    set(current.discovery_methods + candidate.discovery_methods)
                ),
                "provider_call_ids": sorted(
                    set(current.provider_call_ids + candidate.provider_call_ids)
                ),
                "profiles": sorted(set(current.profiles + candidate.profiles)),
            }
        )

    def phase_metrics(state: ResearchState, phase: str, result) -> dict[str, object]:
        metrics = dict(state.get("metrics", {}))
        metrics[phase] = {
            "discovered_count": result.discovered_count,
            "retained_count": result.retained_count,
            "excluded_count": result.excluded_count,
            "failure_count": result.failure_count,
            "provider_calls": result.provider_calls,
            "elapsed_seconds": result.elapsed_seconds,
            "warnings": getattr(result, "warnings", []),
        }
        return metrics

    async def initialize(state: ResearchState, config: RunnableConfig) -> dict[str, object]:
        if state.get("run"):
            return {}
        request = ResearchRequest.model_validate(state["request"])
        if request.marketplace_id:
            if deps.embedder is None:
                raise ValueError(
                    "request.marketplace_id is set but ResearchDependencies.embedder is "
                    "None -- seed_marketplace_entity needs it to make the seeded node "
                    "findable by graphiti's similarity search"
                )
            await deps.store.seed_marketplace_entity(
                marketplace_id=request.marketplace_id,
                name=request.name,
                embedder=deps.embedder,
            )
        target = ResearchTarget.build(request.name, request.url)
        target = await deps.store.load_or_create_target(target)
        configurable = config.get("configurable", {})
        metadata = config.get("metadata", {})
        thread_id = configurable.get("thread_id") or metadata.get("thread_id")
        run = DiscoveryRun(
            thread_id=thread_id if isinstance(thread_id, str) else None,
            entrypoint="stateful_research",
            started_at=datetime.now(UTC),
            config={
                "target_id": target.target_id,
                "mode": request.mode.value,
                "profiles": request.profiles,
                "force_profiles": request.force_profiles,
                "skip_external": request.skip_external,
                "first_party_only": request.first_party_only,
            },
        )
        await deps.store.start_run(run)
        return {
            "request": request,
            "target": target,
            "run": run,
            "metrics": {
                "skip_external": request.skip_external,
                "first_party_only": request.first_party_only,
            },
            "errors": [],
        }

    async def map_first_party(state: ResearchState) -> dict[str, object]:
        result = await deps.first_party.map_first_party(
            target=state["target"],
            run_id=state["run"].run_id,
            mode=state["request"].mode.value,
        )
        return {"metrics": phase_metrics(state, "first_party_map", result)}

    async def fetch_first_party(state: ResearchState) -> dict[str, object]:
        result = await deps.first_party.fetch_first_party(
            target=state["target"], run_id=state["run"].run_id
        )
        source_ids = await deps.store.list_target_source_ids(
            state["target"].target_id, first_party_only=state["request"].first_party_only
        )
        return {
            "first_party_source_ids": result.source_ids,
            "acquisition_phase": "first_party",
            "extraction_source_ids": source_ids,
            "extraction_force_profiles": state["request"].force_profiles,
            "metrics": phase_metrics(state, "first_party_fetch", result),
        }

    async def plan_extractions(state: ResearchState) -> dict[str, object]:
        if deps.ingestion_graph is None:
            return {"extraction_work": []}
        work_items = []
        for source_id in state.get("extraction_source_ids", []):
            for profile in state["request"].profiles:
                version = await extractor_version(profile, model=deps.ingestion_model_name)
                work = await deps.store.plan_extraction(
                    state["target"].target_id,
                    source_id,
                    profile,
                    version,
                    target_name=state["target"].name,
                    force=profile in state.get("extraction_force_profiles", []),
                )
                if work:
                    work_items.append(work)
        return {"extraction_work": work_items}

    async def run_extractions(state: ResearchState) -> dict[str, object]:
        new_results = []
        completed = 0
        errors = list(state.get("errors", []))
        for work in state.get("extraction_work", []):
            try:
                output = await deps.ingestion_graph.ainvoke({"extraction": work})
                new_results.append(
                    {
                        "extraction_id": work.extraction_id,
                        "profile": work.profile,
                        "extractor_version": work.extractor_version,
                        "outcome": output["outcome"],
                        "nodes": output.get("trace_output", {}).get("nodes", []),
                        "audit": output.get("trace_output", {}).get("audit"),
                    }
                )
                completed += 1
            except Exception as exc:
                errors.append(f"{work.extraction_id}: {exc}")
                new_results.append(
                    {
                        "extraction_id": work.extraction_id,
                        "profile": work.profile,
                        "extractor_version": work.extractor_version,
                        "outcome": "failed",
                        "error": str(exc),
                        "nodes": [],
                    }
                )
        metrics = dict(state.get("metrics", {}))
        extraction_metrics = dict(metrics.get("extraction", {}))
        extraction_metrics[state["acquisition_phase"]] = {
            "scheduled": len(state.get("extraction_work", [])),
            "completed": completed,
        }
        metrics["extraction"] = extraction_metrics
        return {
            "extraction_results": [*state.get("extraction_results", []), *new_results],
            "errors": errors,
            "metrics": metrics,
        }

    async def fanout_external(state: ResearchState) -> dict[str, object]:
        return {}

    def route_external_profiles(state: ResearchState):
        if deps.external_engine is None:
            return "acquire_external"
        return [
            Send(
                "discover_external_profile",
                {
                    "profile": profile,
                    "request": state["request"],
                    "target": state["target"],
                    "run": state["run"],
                    "known_entities": _first_party_leads(state, profile),
                },
            )
            for profile in state["request"].profiles
        ]

    async def discover_external_profile(
        state: ResearchState, config: RunnableConfig
    ) -> dict[str, object]:
        profile = state["profile"]
        try:
            result = await deps.external_engine.discover_external(
                request=state["request"].model_copy(update={"profiles": [profile]}),
                existing_urls=await deps.store.list_target_frontier_urls(state["target"].target_id),
                run_id=state["run"].run_id,
                config=config,
                known_entities=state.get("known_entities", ()),
            )
            result = ExternalDiscoveryResult.model_validate(result)
            observed = [
                candidate.model_copy(
                    update={
                        "profiles": [profile],
                        "provider_call_ids": [
                            f"{profile}:{call_id}" for call_id in candidate.provider_call_ids
                        ],
                    }
                )
                for candidate in result.observed_candidates
            ]
            selected = [
                candidate.model_copy(
                    update={
                        "profiles": [profile],
                        "provider_call_ids": [
                            f"{profile}:{call_id}" for call_id in candidate.provider_call_ids
                        ],
                    }
                )
                for candidate in result.selected_candidates
            ]
            result = result.model_copy(
                update={"observed_candidates": observed, "selected_candidates": selected}
            )
        except Exception as exc:
            return {
                "external_discoveries": [
                    ExternalDiscoveryResult(failures=[(profile, str(exc) or type(exc).__name__)])
                ]
            }
        return {"external_discoveries": [result]}

    async def acquire_external(state: ResearchState) -> dict[str, object]:
        if deps.external_engine is None:
            return {
                "external_source_ids": [],
                "acquisition_phase": "external",
                "extraction_source_ids": [],
                "extraction_force_profiles": [],
            }
        observed = {}
        selected = {}
        prefetched_pages = {}
        failures = []
        search_calls = scrape_calls = 0
        for result in state.get("external_discoveries", []):
            for candidate in result.observed_candidates:
                canonical = canonicalize_url(candidate.url)
                observed[canonical] = merge_candidate(observed.get(canonical), candidate)
            for candidate in result.selected_candidates:
                canonical = canonicalize_url(candidate.url)
                selected[canonical] = merge_candidate(selected.get(canonical), candidate)
            prefetched_pages.update(
                {
                    canonicalize_url(page.url): page
                    for page in getattr(result, "prefetched_pages", [])
                }
            )
            failures.extend(result.failures)
            search_calls += result.search_calls
            scrape_calls += result.scrape_calls
        acquired = await deps.first_party.run_external(
            target=state["target"],
            run_id=state["run"].run_id,
            observed=list(observed.values()),
            selected=list(selected.values()),
            **({"prefetched_pages": list(prefetched_pages.values())} if prefetched_pages else {}),
        )
        metrics = phase_metrics(state, "external_acquisition", acquired)
        metrics["external"] = {
            "observed_urls": len(observed),
            "selected_urls": len(selected),
            "failure_count": len(failures) + acquired.failure_count,
            "agent_search_calls": search_calls,
            "agent_scrape_calls": scrape_calls,
            "acquisition_fetch_calls": acquired.provider_calls,
            "acquisition_elapsed_seconds": acquired.elapsed_seconds,
        }
        errors = list(state.get("errors", []))
        errors.extend(f"{scope}: {error}" for scope, error in failures)
        return {
            "external_source_ids": acquired.source_ids,
            "acquisition_phase": "external",
            "extraction_source_ids": acquired.source_ids,
            "extraction_force_profiles": [],
            "metrics": metrics,
            "errors": errors,
        }

    def route_after_extractions(state: ResearchState) -> str:
        if state["acquisition_phase"] == "first_party" and state["request"].skip_external:
            return "finish"
        return "fanout_external" if state["acquisition_phase"] == "first_party" else "finish"

    async def finish(state: ResearchState) -> dict[str, object]:
        status = "completed_with_errors" if state.get("errors") else "completed"
        metrics = dict(state.get("metrics", {}))
        summarize = getattr(deps.store, "summarize_provider_calls", None)
        if summarize is not None:
            metrics["provider_calls"] = await summarize(state["run"].run_id)
        await deps.store.finish_run(state["run"].run_id, status, metrics)
        return {
            "status": status,
            "metrics": metrics,
            "trace_output": {
                "status": status,
                "extractions": state.get("extraction_results", []),
                "skip_external": state["request"].skip_external,
                "first_party_only": state["request"].first_party_only,
            },
        }

    graph = StateGraph(ResearchState)
    graph.add_node("initialize", initialize)
    graph.add_node("map_first_party", map_first_party)
    graph.add_node("fetch_first_party", fetch_first_party)
    graph.add_node("plan_extractions", plan_extractions)
    graph.add_node("run_extractions", run_extractions)
    graph.add_node("fanout_external", fanout_external)
    graph.add_node("discover_external_profile", discover_external_profile)
    graph.add_node("acquire_external", acquire_external)
    graph.add_node("finish", finish)
    graph.add_edge(START, "initialize")
    graph.add_edge("initialize", "map_first_party")
    graph.add_edge("map_first_party", "fetch_first_party")
    graph.add_edge("fetch_first_party", "plan_extractions")
    graph.add_edge("plan_extractions", "run_extractions")
    graph.add_conditional_edges(
        "run_extractions", route_after_extractions, ["fanout_external", "finish"]
    )
    graph.add_conditional_edges(
        "fanout_external",
        route_external_profiles,
        ["discover_external_profile", "acquire_external"],
    )
    graph.add_edge("discover_external_profile", "acquire_external")
    graph.add_edge("acquire_external", "plan_extractions")
    graph.add_edge("finish", END)
    return graph.compile()
