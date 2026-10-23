import asyncio

import pytest

from ai_internship.discovery.contracts import DiscoveryCandidate, ExternalDiscoveryResult
from ai_internship.research.contracts import ResearchRequest, ResearchTarget
from ai_internship.research.graph import (
    KNOWN_ENTITY_LEAD_CAP,
    ResearchDependencies,
    _first_party_leads,
    build_research_graph,
)


def result(source_ids):
    return type(
        "Result",
        (),
        {
            "source_ids": source_ids,
            "discovered_count": len(source_ids),
            "retained_count": len(source_ids),
            "excluded_count": 0,
            "failure_count": 0,
            "provider_calls": len(source_ids),
            "elapsed_seconds": 0.1,
        },
    )()


class FakeStore:
    def __init__(self, events):
        self.events = events
        self.targets = []
        self.runs = []
        self.finished = []

    async def load_or_create_target(self, target):
        self.targets.append(target)
        return target

    async def seed_marketplace_entity(self, *, marketplace_id, name, embedder, label="Product"):
        self.seeded = getattr(self, "seeded", []) + [(marketplace_id, name, embedder, label)]

    async def start_run(self, run):
        self.runs.append(run)

    async def finish_run(self, run_id, status, metrics):
        self.events.append("finish")
        self.finished.append((run_id, status, metrics))

    async def list_target_source_ids(self, target_id, *, first_party_only=False):
        self.list_target_source_ids_calls = getattr(self, "list_target_source_ids_calls", [])
        self.list_target_source_ids_calls.append(first_party_only)
        return ["first-party-1"]

    async def list_target_frontier_urls(self, target_id):
        return []

    async def plan_extraction(
        self, target_id, source_id, profile, version, *, target_name, force=False
    ):
        self.events.append(f"plan:{source_id}:{profile}:{'force' if force else 'normal'}")
        return type(
            "Work",
            (),
            {
                "extraction_id": f"{source_id}:{profile}",
                "source_id": source_id,
                "profile": profile,
                "extractor_version": f"version:{profile}",
            },
        )()


class FakeAcquisition:
    def __init__(self, events):
        self.events = events

    async def map_first_party(self, *, target, run_id, mode):
        self.events.append("map_first_party")
        return result([])

    async def fetch_first_party(self, *, target, run_id):
        self.events.append("fetch_first_party")
        return result(["first-party-1"])

    async def run_external(self, *, target, run_id, observed, selected):
        self.events.append("acquire_external")
        return result(["external-1"])


class FakeExternalEngine:
    def __init__(self):
        self.profiles = []
        self.known_entities = []

    async def discover_external(self, **kwargs):
        kwargs["request"]
        self.profiles.append(kwargs["request"].profiles)
        self.known_entities.append(list(kwargs.get("known_entities", ())))
        candidate = DiscoveryCandidate(url="https://external.example/a")
        return ExternalDiscoveryResult(
            observed_candidates=[candidate],
            selected_candidates=[candidate],
            search_calls=1,
        )


class FailingExternalEngine:
    async def discover_external(self, **kwargs):
        raise AssertionError("external discovery must be skipped")


class FakeIngestionGraph:
    def __init__(self, events):
        self.events = events

    async def ainvoke(self, state):
        work = state["extraction"]
        self.events.append(f"extract:{work.source_id}:{work.profile}")
        return {
            "outcome": "succeeded",
            "trace_output": {
                "nodes": [{"id": "entity-1", "label": "HealthSystem", "name": "Mercy Health"}],
                "audit": {"extraction": {"id": work.extraction_id}},
            },
        }


@pytest.mark.asyncio
async def test_initialize_seeds_marketplace_entity_when_id_and_embedder_present():
    events = []
    store = FakeStore(events)
    embedder = object()
    graph = build_research_graph(
        ResearchDependencies(
            store=store,
            first_party=FakeAcquisition(events),
            ingestion_graph=FakeIngestionGraph(events),
            embedder=embedder,
            external_engine=FakeExternalEngine(),
        )
    )

    await graph.ainvoke(
        {
            "request": ResearchRequest(
                name="AgileMD Clinical Pathways",
                url="https://www.agilemd.com",
                profiles=["customers"],
                marketplace_id="mid-1",
            )
        }
    )

    assert store.seeded == [("mid-1", "AgileMD Clinical Pathways", embedder, "Product")]


@pytest.mark.asyncio
async def test_initialize_raises_clearly_when_marketplace_id_set_without_embedder():
    """A confusing AttributeError deep inside seed_marketplace_entity is worse
    than failing fast with a clear message (Macroscope review, PR #49)."""
    events = []
    graph = build_research_graph(
        ResearchDependencies(
            store=FakeStore(events),
            first_party=FakeAcquisition(events),
            ingestion_graph=FakeIngestionGraph(events),
            external_engine=FakeExternalEngine(),
        )
    )

    with pytest.raises(ValueError, match="embedder"):
        await graph.ainvoke(
            {
                "request": ResearchRequest(
                    name="AgileMD Clinical Pathways",
                    url="https://www.agilemd.com",
                    profiles=["customers"],
                    marketplace_id="mid-1",
                )
            }
        )


@pytest.mark.asyncio
async def test_research_trace_output_keeps_failed_profile_outcomes():
    class FailingIngestionGraph(FakeIngestionGraph):
        async def ainvoke(self, state):
            work = state["extraction"]
            if work.profile == "integrations":
                raise RuntimeError("extractor unavailable")
            return await super().ainvoke(state)

    events = []
    graph = build_research_graph(
        ResearchDependencies(
            store=FakeStore(events),
            first_party=FakeAcquisition(events),
            ingestion_graph=FailingIngestionGraph(events),
            external_engine=FakeExternalEngine(),
        )
    )

    result = await graph.ainvoke(
        {
            "request": ResearchRequest(
                name="Fathom",
                url="https://fathomhealth.com",
                profiles=["customers", "integrations"],
            )
        }
    )

    assert {item["profile"] for item in result["trace_output"]["extractions"]} == {
        "customers",
        "integrations",
    }
    assert any(
        item["profile"] == "integrations"
        and item["outcome"] == "failed"
        and item["error"] == "extractor unavailable"
        for item in result["trace_output"]["extractions"]
    )


@pytest.mark.asyncio
async def test_research_graph_reuses_shared_extraction_nodes_for_both_acquisition_batches():
    events = []
    store = FakeStore(events)
    graph = build_research_graph(
        ResearchDependencies(
            store=store,
            first_party=FakeAcquisition(events),
            ingestion_graph=FakeIngestionGraph(events),
            external_engine=FakeExternalEngine(),
        )
    )
    request = ResearchRequest(
        name="Fathom",
        url="https://fathomhealth.com",
        profiles=["customers"],
        force_profiles=["customers"],
    )

    result = await graph.ainvoke(
        {"request": request},
        config={"configurable": {"thread_id": "thread-1"}},
    )

    assert store.targets[0].canonical_url == "https://fathomhealth.com/"
    assert store.runs[0].thread_id == "thread-1"
    assert events == [
        "map_first_party",
        "fetch_first_party",
        "plan:first-party-1:customers:force",
        "extract:first-party-1:customers",
        "acquire_external",
        "plan:external-1:customers:normal",
        "extract:external-1:customers",
        "finish",
    ]
    assert result["extraction_results"] == [
        {
            "extraction_id": "first-party-1:customers",
            "profile": "customers",
            "extractor_version": "version:customers",
            "outcome": "succeeded",
            "nodes": [{"id": "entity-1", "label": "HealthSystem", "name": "Mercy Health"}],
            "audit": {"extraction": {"id": "first-party-1:customers"}},
        },
        {
            "extraction_id": "external-1:customers",
            "profile": "customers",
            "extractor_version": "version:customers",
            "outcome": "succeeded",
            "nodes": [{"id": "entity-1", "label": "HealthSystem", "name": "Mercy Health"}],
            "audit": {"extraction": {"id": "external-1:customers"}},
        },
    ]
    assert result["trace_output"] == {
        "status": "completed",
        "extractions": result["extraction_results"],
        "skip_external": False,
        "first_party_only": False,
    }
    assert result["metrics"]["extraction"] == {
        "first_party": {"scheduled": 1, "completed": 1},
        "external": {"scheduled": 1, "completed": 1},
    }
    assert store.finished == [(result["run"].run_id, "completed", result["metrics"])]


@pytest.mark.asyncio
async def test_research_graph_discovers_external_sources_per_profile():
    events = []
    external_engine = FakeExternalEngine()
    graph = build_research_graph(
        ResearchDependencies(
            store=FakeStore(events),
            first_party=FakeAcquisition(events),
            ingestion_graph=FakeIngestionGraph(events),
            external_engine=external_engine,
        )
    )

    await graph.ainvoke(
        {
            "request": ResearchRequest(
                name="Fathom",
                url="https://fathomhealth.com",
                profiles=["customers", "integrations"],
            )
        }
    )

    assert sorted(external_engine.profiles) == [["customers"], ["integrations"]]


@pytest.mark.asyncio
async def test_external_discovery_receives_first_party_names_as_leads():
    """ELI-1485: the names first-party extraction confirmed reach the external agent.

    The customers profile extracts HealthSystem -> Adopts -> Product, so only the
    HealthSystem endpoint is a lead; integrations sees no Product node here and so
    gets nothing, which is the honest outcome rather than a silent default.
    """
    events = []
    external_engine = FakeExternalEngine()
    graph = build_research_graph(
        ResearchDependencies(
            store=FakeStore(events),
            first_party=FakeAcquisition(events),
            ingestion_graph=FakeIngestionGraph(events),
            external_engine=external_engine,
        )
    )

    await graph.ainvoke(
        {
            "request": ResearchRequest(
                name="Fathom",
                url="https://fathomhealth.com",
                profiles=["customers", "integrations"],
            )
        }
    )

    by_profile = dict(zip((p[0] for p in external_engine.profiles), external_engine.known_entities))
    assert by_profile["customers"] == ["Mercy Health"]
    assert by_profile["integrations"] == []


def test_first_party_leads_caps_the_list_and_drops_the_vendor_itself():
    """Fed 19-31 names the agent ignored the lead instruction entirely, so the cap
    is the thing that makes this work -- assert it, not just that names flow."""
    state = {
        "target": ResearchTarget.build("Fathom", "https://fathomhealth.com"),
        "extraction_results": [
            {
                "profile": "customers",
                "outcome": "succeeded",
                "nodes": [
                    {"id": "p", "label": "Product", "name": "Fathom"},
                    *(
                        {"id": f"h{i}", "label": "HealthSystem", "name": f"System {i}"}
                        for i in range(12)
                    ),
                    # A duplicate under different casing must not consume a slot.
                    {"id": "dupe", "label": "HealthSystem", "name": "system 0"},
                ],
            },
            {"profile": "customers", "outcome": "failed", "nodes": [], "error": "boom"},
            {
                "profile": "integrations",
                "outcome": "succeeded",
                "nodes": [{"id": "x", "label": "Product", "name": "Acme"}],
            },
        ],
    }

    leads = _first_party_leads(state, "customers")

    assert leads == [f"System {i}" for i in range(KNOWN_ENTITY_LEAD_CAP)]
    assert "Fathom" not in leads
    assert _first_party_leads(state, "integrations") == ["Acme"]


@pytest.mark.asyncio
async def test_research_graph_skips_external_discovery_when_requested():
    events = []
    store = FakeStore(events)
    graph = build_research_graph(
        ResearchDependencies(
            store=store,
            first_party=FakeAcquisition(events),
            ingestion_graph=FakeIngestionGraph(events),
            external_engine=FailingExternalEngine(),
        )
    )

    result = await graph.ainvoke(
        {
            "request": ResearchRequest(
                name="Fathom",
                url="https://fathomhealth.com",
                profiles=["customers"],
                skip_external=True,
            )
        }
    )

    assert events == [
        "map_first_party",
        "fetch_first_party",
        "plan:first-party-1:customers:normal",
        "extract:first-party-1:customers",
        "finish",
    ]
    assert store.runs[0].config["skip_external"] is True
    assert result["metrics"]["skip_external"] is True
    assert result["trace_output"]["skip_external"] is True
    assert result["metrics"]["extraction"] == {"first_party": {"scheduled": 1, "completed": 1}}


@pytest.mark.asyncio
async def test_research_graph_passes_first_party_only_to_the_store():
    events = []
    store = FakeStore(events)
    graph = build_research_graph(
        ResearchDependencies(
            store=store,
            first_party=FakeAcquisition(events),
            ingestion_graph=FakeIngestionGraph(events),
            external_engine=FailingExternalEngine(),
        )
    )

    result = await graph.ainvoke(
        {
            "request": ResearchRequest(
                name="Fathom",
                url="https://fathomhealth.com",
                profiles=["customers"],
                skip_external=True,
                first_party_only=True,
            )
        }
    )

    assert store.list_target_source_ids_calls == [True]
    # Recorded alongside skip_external in every place it's recorded, so a run's
    # own durable record can tell first-party-only runs apart after the fact.
    assert store.runs[0].config["first_party_only"] is True
    assert result["metrics"]["first_party_only"] is True
    assert result["trace_output"]["first_party_only"] is True


@pytest.mark.asyncio
async def test_research_graph_runs_profile_discovery_in_parallel():
    class ParallelExternalEngine(FakeExternalEngine):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def discover_external(self, **kwargs):
            self.profiles.append(kwargs["request"].profiles)
            if len(self.profiles) == 2:
                self.started.set()
            await self.release.wait()
            return await super().discover_external(**kwargs)

    events = []
    external_engine = ParallelExternalEngine()
    graph = build_research_graph(
        ResearchDependencies(
            store=FakeStore(events),
            first_party=FakeAcquisition(events),
            ingestion_graph=FakeIngestionGraph(events),
            external_engine=external_engine,
        )
    )
    task = asyncio.create_task(
        graph.ainvoke(
            {
                "request": ResearchRequest(
                    name="Fathom",
                    url="https://fathomhealth.com",
                    profiles=["customers", "integrations"],
                )
            }
        )
    )

    await asyncio.wait_for(external_engine.started.wait(), timeout=0.1)
    external_engine.release.set()
    await task


@pytest.mark.asyncio
async def test_profile_discovery_failure_preserves_sibling_results_and_finishes_with_errors():
    class PartiallyFailingEngine:
        async def discover_external(self, **kwargs):
            profile = kwargs["request"].profiles[0]
            if profile == "integrations":
                raise RuntimeError("provider unavailable")
            candidate = DiscoveryCandidate(url="https://external.example/customer")
            return ExternalDiscoveryResult(
                observed_candidates=[candidate], selected_candidates=[candidate]
            )

    class CapturingAcquisition(FakeAcquisition):
        async def run_external(self, *, target, run_id, observed, selected):
            self.observed = observed
            return await super().run_external(
                target=target, run_id=run_id, observed=observed, selected=selected
            )

    events = []
    store = FakeStore(events)
    acquisition = CapturingAcquisition(events)
    graph = build_research_graph(
        ResearchDependencies(
            store=store,
            first_party=acquisition,
            ingestion_graph=FakeIngestionGraph(events),
            external_engine=PartiallyFailingEngine(),
        )
    )

    output = await graph.ainvoke(
        {
            "request": ResearchRequest(
                name="Fathom",
                url="https://fathomhealth.com",
                profiles=["customers", "integrations"],
            )
        }
    )

    assert [candidate.url for candidate in acquisition.observed] == [
        "https://external.example/customer"
    ]
    assert output["status"] == "completed_with_errors"
    assert output["errors"] == ["integrations: provider unavailable"]
    assert store.finished[0][1] == "completed_with_errors"


@pytest.mark.asyncio
async def test_profile_discovery_unions_canonical_candidate_provenance():
    class ProvenanceEngine:
        async def discover_external(self, **kwargs):
            profile = kwargs["request"].profiles[0]
            candidate = DiscoveryCandidate(
                url="https://external.example/shared",
                queries=[f"{profile} query"],
                discovery_methods=["search"],
                provider_call_ids=["search:1"],
            )
            return ExternalDiscoveryResult(
                observed_candidates=[candidate], selected_candidates=[candidate]
            )

    class CapturingAcquisition(FakeAcquisition):
        async def run_external(self, *, target, run_id, observed, selected):
            self.observed = observed
            return await super().run_external(
                target=target, run_id=run_id, observed=observed, selected=selected
            )

    events = []
    acquisition = CapturingAcquisition(events)
    graph = build_research_graph(
        ResearchDependencies(
            store=FakeStore(events),
            first_party=acquisition,
            ingestion_graph=FakeIngestionGraph(events),
            external_engine=ProvenanceEngine(),
        )
    )

    await graph.ainvoke(
        {
            "request": ResearchRequest(
                name="Fathom",
                url="https://fathomhealth.com",
                profiles=["customers", "integrations"],
            )
        }
    )

    assert len(acquisition.observed) == 1
    candidate = acquisition.observed[0]
    assert candidate.profiles == ["customers", "integrations"]
    assert candidate.queries == ["customers query", "integrations query"]
    assert candidate.discovery_methods == ["search"]
    assert candidate.provider_call_ids == ["customers:search:1", "integrations:search:1"]
