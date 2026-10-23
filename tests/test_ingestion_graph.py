from datetime import UTC, datetime

import pytest

from ai_internship.discovery.contracts import RetainedSource
from ai_internship.enrichment import graph as ingestion_graph
from ai_internship.enrichment.contracts import (
    Assertion,
    CandidateEntity,
    ExtractionWork,
    ResolvedEntity,
    SeededEntity,
)
from ai_internship.enrichment.graph import IngestionDependencies, build_ingestion_graph
from ai_internship.graph.resolve import resolve_entities


@pytest.fixture(autouse=True)
def graphiti_resolution_stub(monkeypatch):
    async def resolve(_clients, graph, entities, seeded_entities, *, target_name):
        return await resolve_entities(graph, entities, seeded_entities, target_name=target_name)

    monkeypatch.setattr(ingestion_graph, "resolve_entities_via_graphiti", resolve)


class Store:
    def __init__(self, source):
        self.source = source

    async def load_source(self, source_id):
        return self.source

    async def start_extraction(self, work):
        self.started = getattr(self, "started", []) + [work]

    async def finish_extraction(self, work, outcome, grounding_rejections):
        self.finished = getattr(self, "finished", []) + [(work, outcome)]
        self.grounding_rejections = grounding_rejections


class Graph:
    persist_calls = 0

    def __init__(self):
        self.persist_calls = 0
        self.activation_calls = []

    async def find_seed_candidates(self, labels, content):
        return []

    async def find_entities(self, label, normalized_name):
        return []

    async def ensure_graphiti_indexes(self):
        pass

    async def persist_graphiti(self, **kwargs):
        self.persist_calls += 1

    async def activate_extraction(self, **kwargs):
        self.activation_calls.append(kwargs)


def _deps(**kwargs):
    return IngestionDependencies(graphiti_clients=object(), **kwargs)


class NoFactsExtractor:
    async def extract(self, source, profiles, seeded_entities, target_name=""):
        return []


class CountingExtractor(NoFactsExtractor):
    def __init__(self):
        self.calls = 0

    async def extract(self, source, profiles, seeded_entities, target_name=""):
        self.calls += 1
        return await super().extract(source, profiles, seeded_entities)


def source():
    return RetainedSource.build(
        requested_url="https://example.com",
        final_url="https://example.com",
        content="no extractable facts",
        retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
        discovery_run_id="run-1",
        entrypoint="test",
    )


def extraction(source_id, profile="customers"):
    return ExtractionWork.build(source_id, profile, "test-extractor", revision=1)


@pytest.mark.asyncio
async def test_ingestion_graph_compiles_and_retains_no_facts():
    retained = source()
    deps = _deps(store=Store(retained), graph=Graph(), extractor=NoFactsExtractor())

    result = await build_ingestion_graph(deps).ainvoke(
        {"extraction": extraction(retained.source_id)}
    )

    assert result["outcome"] == "no_facts"
    assert deps.store.finished == [(extraction(retained.source_id), "no_facts")]


@pytest.mark.asyncio
async def test_ingestion_requires_graphiti_clients_for_entity_resolution(monkeypatch):
    async def resolver(*args, **kwargs):
        raise AssertionError("Graphiti resolution should not run without clients")

    monkeypatch.setattr(ingestion_graph, "resolve_entities_via_graphiti", resolver)
    deps = IngestionDependencies(store=Store(source()), graph=Graph(), extractor=NoFactsExtractor())

    with pytest.raises(RuntimeError, match="Graphiti clients are required"):
        await deps.resolve_candidate_entities(
            [CandidateEntity(label="Product", name="Acme", quote="Acme")], [], ""
        )


@pytest.mark.asyncio
async def test_ingestion_skips_model_when_source_does_not_name_target():
    retained = source()
    extractor = CountingExtractor()

    result = await build_ingestion_graph(
        _deps(store=Store(retained), graph=Graph(), extractor=extractor)
    ).ainvoke(
        {
            "extraction": ExtractionWork.build(
                retained.source_id,
                "customers",
                "test-extractor",
                revision=1,
                target_name="FirstHx",
            )
        }
    )

    assert result["outcome"] == "no_facts"
    assert extractor.calls == 0


@pytest.mark.asyncio
async def test_authoritative_empty_revision_supersedes_prior_output():
    retained = source()
    graph = Graph()
    work = ExtractionWork.build(retained.source_id, "customers", "test-extractor", revision=2)

    result = await build_ingestion_graph(
        _deps(store=Store(retained), graph=graph, extractor=NoFactsExtractor())
    ).ainvoke({"extraction": work})

    assert result["outcome"] == "no_facts"
    assert graph.activation_calls == [
        {
            "episode_uuid": work.extraction_id,
            "source_id": retained.source_id,
            "profile": "customers",
        }
    ]


@pytest.mark.asyncio
async def test_ingestion_work_extracts_only_its_profile_and_records_its_identity():
    retained = source()
    store = Store(retained)
    work = ExtractionWork.build(retained.source_id, "customers", "extractor-v1", revision=1)
    deps = _deps(store=store, graph=Graph(), extractor=NoFactsExtractor())

    result = await build_ingestion_graph(deps).ainvoke({"extraction": work})

    assert result["extraction"] == work
    assert store.started == [work]
    assert store.finished == [(work, "no_facts")]


@pytest.mark.asyncio
async def test_ingestion_reports_persistence_exception_type_and_repr():
    class FailingGraph(Graph):
        async def persist_graphiti(self, **kwargs):
            raise RuntimeError("Neo4j write failed")

    retained = source()
    result = await build_ingestion_graph(
        _deps(store=Store(retained), graph=FailingGraph(), extractor=NoFactsExtractor())
    ).ainvoke({"extraction": extraction(retained.source_id)})

    assert result["outcome"] == "persistence_failure"
    assert result["persistence_error"] == "RuntimeError: RuntimeError('Neo4j write failed')"
    assert result["trace_output"]["audit"]["persistence_diagnostics"] == {
        "extraction_id": result["extraction"].extraction_id,
        "source_url": "https://example.com",
        "profile": "customers",
        "resolved_entity_count": 0,
        "edge_count": 0,
        "exception_type": "RuntimeError",
        "exception_repr": "RuntimeError('Neo4j write failed')",
    }


def test_ingestion_graph_does_not_have_redundant_profile_selection_node():
    retained = source()
    deps = _deps(store=Store(retained), graph=Graph(), extractor=NoFactsExtractor())

    node_names = build_ingestion_graph(deps).get_graph().nodes

    assert "select_profiles" not in node_names


@pytest.mark.asyncio
async def test_outcome_is_none_until_a_terminal_result():
    retained = source()
    deps = _deps(store=Store(retained), graph=Graph(), extractor=NoFactsExtractor())
    updates = []

    async for update in build_ingestion_graph(deps).astream(
        {"extraction": extraction(retained.source_id)},
        stream_mode="updates",
    ):
        updates.append(update)

    assert updates[0]["load_source"]["outcome"] is None
    assert updates[1]["extract"]["outcome"] == "no_facts"


@pytest.mark.asyncio
async def test_ingestion_passes_source_seed_candidates_to_extraction_and_resolution():
    retained = RetainedSource.build(
        requested_url="https://example.com",
        final_url="https://example.com",
        content="Mercy Health uses Acme Platform",
        retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
        discovery_run_id="run-1",
        entrypoint="test",
    )

    class SeededGraph(Graph):
        async def find_seed_candidates(self, labels, content):
            assert labels == {"HealthSystem", "Product"}
            assert content == retained.content
            return [SeededEntity(label="Product", name="Acme Platform", entity_uuid="product-1")]

    class Extractor:
        async def extract(self, source, profiles, seeded_entities, target_name=""):
            self.seeded_entities = seeded_entities
            return [
                Assertion(
                    subject="Mercy Health",
                    subject_type="HealthSystem",
                    relation="Adopts",
                    object="Acme Platform",
                    object_type="Product",
                    quote=retained.content,
                    profile="customers",
                )
            ]

    extractor = Extractor()
    result = await build_ingestion_graph(
        _deps(store=Store(retained), graph=SeededGraph(), extractor=extractor)
    ).ainvoke({"extraction": extraction(retained.source_id)})

    assert extractor.seeded_entities[0].entity_uuid == "product-1"
    product = next(item for item in result["resolved"] if item.label == "Product")
    assert product.entity_uuid == "product-1"


@pytest.mark.asyncio
async def test_ingestion_persists_only_entities_from_accepted_edges():
    retained = RetainedSource.build(
        requested_url="https://example.com",
        final_url="https://example.com",
        content="Mercy Health uses Acme Platform. Use our AI to ease the burden.",
        retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
        discovery_run_id="run-1",
        entrypoint="test",
    )

    class CapturingGraph(Graph):
        async def persist_graphiti(self, **kwargs):
            await super().persist_graphiti(**kwargs)
            self.persisted_nodes = kwargs["nodes"]

    class Extractor:
        async def extract(self, source, profiles, seeded_entities, target_name=""):
            return [
                Assertion(
                    subject="Mercy Health",
                    subject_type="HealthSystem",
                    relation="Adopts",
                    object="Acme Platform",
                    object_type="Product",
                    quote=retained.content,
                    profile="customers",
                ),
                Assertion(
                    subject="Medica",
                    subject_type="HealthSystem",
                    relation="Adopts",
                    object="Ema",
                    object_type="Product",
                    quote="Use our AI to ease the burden.",
                    profile="customers",
                ),
            ]

    graph = CapturingGraph()
    store = Store(retained)
    result = await build_ingestion_graph(
        _deps(store=store, graph=graph, extractor=Extractor())
    ).ainvoke({"extraction": extraction(retained.source_id)})

    # ELI-1192: subject_not_in_evidence no longer exists for the customers
    # profile at all -- the semantic check (not exercised here, no chat_model
    # is passed) is now the only judge of whether a subject is real, so
    # "Medica"/"Ema" is accepted too, not rejected by string-matching.
    assert result["outcome"] == "succeeded"
    assert store.grounding_rejections == []
    assert {item.name for item in graph.persisted_nodes} == {
        "Mercy Health",
        "Acme Platform",
        "Medica",
        "Ema",
    }
    assert {item.name for item in result["resolved"]} == {
        "Mercy Health",
        "Acme Platform",
        "Medica",
        "Ema",
    }


# The referent's proof is verified against the page, so a double has to return a
# sentence that is really there or every relation is discarded and retried.
PROOF_SENTENCE = "The Weight Clinic chose FirstHx last spring."


class _FakeSemanticModel:
    """Fixed verdict, always claiming the relationship type isn't actually
    supported -- for testing the ELI-1147 semantic check end-to-end through
    the real graph, not grounding.py's unit tests directly."""

    def with_structured_output(self, schema, **kwargs):
        # This double backs the referent call as well as the semantic check.
        # Confirming there lets each test reach the verdict it is actually
        # about, instead of every relation dying at the referent.
        is_referent = "proof_sentence" in getattr(schema, "model_fields", {})

        class StructuredModel:
            async def ainvoke(self, messages):
                if is_referent:
                    return {
                        "reason": "stated on the page",
                        "integrated": True,
                        "proof_sentence": PROOF_SENTENCE,
                    }
                return {
                    "is_named_organization": True,
                    "relationship_actually_happened": True,
                    "relationship_claim_supported": False,
                    "is_third_party_vendor": True,
                    "is_customer_facing_integration": True,
                    "is_specific_named_entity": True,
                    "relationship_shape_matches_profile": True,
                    "subject_is_the_claimant": True,
                }

        return StructuredModel()


@pytest.mark.asyncio
async def test_ingestion_rejects_integration_without_explicit_integration_language():
    """ELI-1147: replaced the deleted INTEGRATION_CUE regex -- "deploys" isn't an
    integration cue, so this used to fail deterministically; now it's the
    semantic check's relationship_claim_supported field doing the same job."""
    retained = RetainedSource.build(
        requested_url="https://example.com",
        final_url="https://example.com",
        content="ExampleClinic deploys ExampleCoding for its coding workflow.",
        retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
        discovery_run_id="run-1",
        entrypoint="test",
    )

    class Extractor:
        async def extract(self, source, profiles, seeded_entities, target_name=""):
            return [
                Assertion(
                    subject="ExampleClinic",
                    subject_type="Product",
                    relation="Integrates",
                    object="ExampleCoding",
                    object_type="Product",
                    quote=retained.content,
                    profile="integrations",
                )
            ]

    graph = Graph()
    store = Store(retained)
    result = await build_ingestion_graph(
        _deps(store=store, graph=graph, extractor=Extractor(), chat_model=_FakeSemanticModel())
    ).ainvoke({"extraction": extraction(retained.source_id, "integrations")})

    assert result["outcome"] == "grounding_failure"
    assert result["grounding_rejections"][0].code == "relationship_claim_not_supported"
    assert result["grounding_rejections"][0].assertion.quote == retained.content
    assert store.grounding_rejections == result["grounding_rejections"]
    assert graph.persist_calls == 1
    assert len(graph.activation_calls) == 1


def _page(content, entrypoint="external_acquisition"):
    return RetainedSource.build(
        requested_url="https://example.com",
        final_url="https://example.com",
        content=content,
        retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
        discovery_run_id="run-1",
        entrypoint=entrypoint,
    )


def _one_assertion(quote, subject="The Weight Clinic"):
    class Extractor:
        async def extract(self, source, profiles, seeded_entities, target_name=""):
            return [
                Assertion(
                    subject=subject,
                    subject_type="HealthSystem",
                    relation="Adopts",
                    object="FirstHx",
                    object_type="Product",
                    quote=quote,
                    profile="customers",
                )
            ]

    return Extractor()


class _ResolvingDeps(IngestionDependencies):
    """Entity resolution is Graphiti's job and needs real clients. These tests
    are about the referent, so resolution is stubbed to accept every candidate
    under its own name."""

    async def resolve_candidate_entities(self, entities, seeded_entities, target_name):
        return [
            ResolvedEntity(
                label=candidate.label, name=candidate.name, quote=candidate.quote, status="matched"
            )
            for candidate in entities
        ]


async def _ingest(retained, extractor, chat_model):
    return await build_ingestion_graph(
        _ResolvingDeps(
            store=Store(retained),
            graph=Graph(),
            extractor=extractor,
            chat_model=chat_model,
            graphiti_clients=object(),
        )
    ).ainvoke(
        {
            "extraction": ExtractionWork.build(
                retained.source_id,
                "customers",
                "test-extractor",
                revision=1,
                target_name="FirstHx",
            )
        }
    )


@pytest.mark.asyncio
async def test_a_quote_naming_both_sides_skips_the_referent():
    """The referent costs a whole-page read, so it only runs when the quote
    cannot settle the relation by itself."""
    quote = "The Weight Clinic has used FirstHx since spring."
    chat_model = ReferentTestModel()
    await _ingest(
        _page(f"Intro line about the clinic.\n\n{quote}"), _one_assertion(quote), chat_model
    )
    assert chat_model.referent_calls == 0


@pytest.mark.asyncio
async def test_a_quote_naming_one_side_goes_to_the_referent_on_an_external_page():
    """ELI-1544: this used to be first-party only, which left third-party pages
    -- where most customers are found -- with no second look at all."""
    quote = "That's when she implemented it."
    chat_model = ReferentTestModel(justification_quote=PROOF_SENTENCE)
    await _ingest(
        _page(f"The Weight Clinic chose FirstHx last spring.\n\n{quote}"),
        _one_assertion(quote),
        chat_model,
    )
    assert chat_model.referent_calls == 1


@pytest.mark.asyncio
async def test_the_referents_argument_reaches_the_semantic_check(monkeypatch):
    """The referent reads the whole page; the check only ever sees a subsection
    matched to the quote. Its sentence has always crossed that gap -- its
    argument now travels too, because the relation the referent exists for is
    the one no single sentence states, and then the argument is the only thing
    carrying it.

    Asserted through the graph rather than on the builder: the wiring between
    them is what a refactor breaks silently. The conftest prompt stub drops
    every variable but two, so the evidence is captured where it is built."""
    from ai_internship.enrichment import grounding

    built_with: list[dict] = []
    stubbed = grounding.load_prompt

    def spy(*, slug, **kwargs):
        prompt = stubbed(slug=slug, **kwargs)
        inner = prompt.build

        def build(**build_kwargs):
            built_with.append(build_kwargs)
            return inner(**build_kwargs)

        prompt.build = build
        return prompt

    monkeypatch.setattr(grounding, "load_prompt", spy)

    quote = "That's when she implemented it."
    chat_model = ReferentTestModel(justification_quote=PROOF_SENTENCE)

    await _ingest(
        _page(f"The Weight Clinic chose FirstHx last spring.\n\n{quote}"),
        _one_assertion(quote),
        chat_model,
    )

    assert chat_model.referent_calls == 1
    evidence = "\n".join(str(k.get("evidence_context", "")) for k in built_with)
    assert PROOF_SENTENCE in evidence, "the referent's sentence should reach the check"
    assert "stated on the page" in evidence, "the referent's argument should reach it too"


@pytest.mark.asyncio
async def test_a_first_party_page_routes_the_referent_to_its_own_prompt(monkeypatch):
    """ELI-1550: choosing the prompt is unit-tested, but the choice only matters
    if the page's origin actually reaches the chooser. It travels from the
    source, through grounding and the referent call, to the prompt loader, and
    that chain is what a refactor silently breaks."""
    from ai_internship.enrichment import referent_resolution
    from ai_internship.enrichment.referent_resolution import (
        FIRST_PARTY_REFERENT_PROMPT_SLUGS,
        REFERENT_PROMPT_SLUGS,
    )

    requested = []
    stubbed = referent_resolution.load_prompt

    def spy(*, slug, **kwargs):
        requested.append(slug)
        return stubbed(slug=slug, **kwargs)

    monkeypatch.setattr(referent_resolution, "load_prompt", spy)

    quote = "That's when she implemented it."
    chat_model = ReferentTestModel(justification_quote=PROOF_SENTENCE)
    await _ingest(
        _page(
            f"The Weight Clinic chose FirstHx last spring.\n\n{quote}",
            entrypoint="first_party_acquisition",
        ),
        _one_assertion(quote),
        chat_model,
    )

    assert chat_model.referent_calls == 1
    assert FIRST_PARTY_REFERENT_PROMPT_SLUGS["Adopts"] in requested
    assert REFERENT_PROMPT_SLUGS["Adopts"] not in requested


@pytest.mark.asyncio
async def test_an_external_page_keeps_the_shared_referent_prompt(monkeypatch):
    """The first-party prompt was only ever measured on first-party pages, so
    external pages must keep the prompt that was measured on them."""
    from ai_internship.enrichment import referent_resolution
    from ai_internship.enrichment.referent_resolution import (
        FIRST_PARTY_REFERENT_PROMPT_SLUGS,
        REFERENT_PROMPT_SLUGS,
    )

    requested = []
    stubbed = referent_resolution.load_prompt

    def spy(*, slug, **kwargs):
        requested.append(slug)
        return stubbed(slug=slug, **kwargs)

    monkeypatch.setattr(referent_resolution, "load_prompt", spy)

    quote = "That's when she implemented it."
    chat_model = ReferentTestModel(justification_quote=PROOF_SENTENCE)
    await _ingest(
        _page(f"The Weight Clinic chose FirstHx last spring.\n\n{quote}"),
        _one_assertion(quote),
        chat_model,
    )

    assert REFERENT_PROMPT_SLUGS["Adopts"] in requested
    assert FIRST_PARTY_REFERENT_PROMPT_SLUGS["Adopts"] not in requested


@pytest.mark.asyncio
async def test_a_relation_the_referent_declines_is_rejected():
    class Declining:
        def with_structured_output(self, schema, **kwargs):
            is_referent = "proof_sentence" in getattr(schema, "model_fields", {})

            class StructuredModel:
                async def ainvoke(self, messages):
                    if is_referent:
                        return {"reason": "no", "integrated": False, "proof_sentence": None}
                    return dict.fromkeys(schema.model_fields, True)

            return StructuredModel()

    quote = "That's when she implemented it."
    result = await _ingest(
        _page(f"The Weight Clinic chose FirstHx last spring.\n\n{quote}"),
        _one_assertion(quote),
        Declining(),
    )
    codes = [r.code for r in result.get("grounding_rejections", [])]
    assert "referent_declined" in codes


@pytest.mark.asyncio
async def test_referent_failure_does_not_abort_the_page():
    """The referent only ever adds relations back, so a transient failure costs
    a recovery -- it must not take down a page whose other relations grounded."""
    from ai_internship.enrichment import graph as graph_module

    async def boom(**kwargs):
        raise RuntimeError("braintrust unreachable")

    original = graph_module.confirm_relation
    graph_module.confirm_relation = boom
    try:
        retained = RetainedSource.build(
            requested_url="https://example.com",
            final_url="https://example.com",
            content="FirstHx\n\nThat's when she implemented it.",
            retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
            discovery_run_id="run-1",
            entrypoint="external_acquisition",
        )

        class Extractor:
            async def extract(self, source, profiles, seeded_entities, target_name=""):
                return [
                    Assertion(
                        subject="she",
                        subject_type="HealthSystem",
                        relation="Adopts",
                        object="FirstHx",
                        object_type="Product",
                        quote="That's when she implemented it.",
                        profile="customers",
                    )
                ]

        result = await build_ingestion_graph(
            _deps(
                store=Store(retained),
                graph=Graph(),
                extractor=Extractor(),
                chat_model=ReferentTestModel(),
            )
        ).ainvoke(
            {
                "extraction": ExtractionWork.build(
                    retained.source_id,
                    "customers",
                    "test-extractor",
                    revision=1,
                    target_name="FirstHx",
                )
            }
        )
        assert result["outcome"] is not None
    finally:
        graph_module.confirm_relation = original


class ReferentTestModel:
    def __init__(self, resolved_name=None, justification_quote=None):
        # `resolved_name` is kept for the callers that still pass it positionally;
        # ELI-1544 dropped it from the referent's answer, which now reports
        # whether the two are connected and quotes the sentence proving it.
        self.resolved_name = resolved_name
        self.justification_quote = justification_quote or (
            "Dr. Hala Kufaishi, Founder & Clinical Director, The Weight Clinic"
        )
        self.calls = 0
        self.referent_calls = 0
        self.semantic_calls = 0
        # What the Semantic check was actually shown, so a test can assert the
        # referent's findings reached it rather than assume the wiring holds.
        self.semantic_prompts: list[str] = []

    def with_structured_output(self, schema, **kwargs):
        model = self
        # Schema-aware: this fake chat_model backs both the referent call and the
        # ELI-1147 semantic grounding check, which ask for different shapes.
        # Always accepts on the semantic check -- these tests are about the
        # referent, not semantic verification.
        is_referent_schema = "proof_sentence" in getattr(schema, "model_fields", {})

        class StructuredModel:
            async def ainvoke(self, messages):
                model.calls += 1
                if not is_referent_schema:
                    model.semantic_prompts.append(
                        "\n".join(
                            str(m.get("content", "")) if isinstance(m, dict) else str(m)
                            for m in (messages or [])
                        )
                    )
                if is_referent_schema:
                    model.referent_calls += 1
                    # The proof has to be findable on the page or production
                    # discards it, so lift a real sentence out of the prompt
                    # rather than returning a fixed string that only verifies
                    # on the one fixture that happens to contain it.
                    proof = model.justification_quote
                    return {
                        "reason": "stated on the page",
                        "integrated": True,
                        "proof_sentence": proof,
                    }
                model.semantic_calls += 1
                return {
                    "is_named_organization": True,
                    "relationship_actually_happened": True,
                    "relationship_claim_supported": True,
                    "is_third_party_vendor": True,
                    "is_customer_facing_integration": True,
                    "is_specific_named_entity": True,
                    "relationship_shape_matches_profile": True,
                    "subject_is_the_claimant": True,
                }

        return StructuredModel()


@pytest.mark.asyncio
async def test_ingestion_records_rejection_when_resolution_is_ambiguous():
    retained = RetainedSource.build(
        requested_url="https://example.com",
        final_url="https://example.com",
        content="Mercy Health uses FirstHx.",
        retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
        discovery_run_id="run-1",
        entrypoint="test",
    )

    class AmbiguousGraph(Graph):
        async def find_seed_candidates(self, labels, content):
            return [
                SeededEntity(label="HealthSystem", name="Mercy Health", entity_uuid="health-1"),
                SeededEntity(label="HealthSystem", name="Mercy Health", entity_uuid="health-2"),
                SeededEntity(label="Product", name="FirstHx", entity_uuid="product-1"),
                SeededEntity(label="Product", name="FirstHx", entity_uuid="product-2"),
            ]

    class Extractor:
        async def extract(self, source, profiles, seeded_entities, target_name=""):
            return [
                Assertion(
                    subject="Mercy Health",
                    subject_type="HealthSystem",
                    relation="Adopts",
                    object="FirstHx",
                    object_type="Product",
                    quote=source.content,
                    profile="customers",
                )
            ]

    store = Store(retained)
    result = await build_ingestion_graph(
        _deps(store=store, graph=AmbiguousGraph(), extractor=Extractor())
    ).ainvoke({"extraction": extraction(retained.source_id)})

    assert result["outcome"] == "resolution_ambiguity"
    assert [item.code for item in result["grounding_rejections"]] == ["unresolved_endpoint"]
    assert store.grounding_rejections == result["grounding_rejections"]


@pytest.mark.asyncio
async def test_ingestion_trace_audit_summarizes_and_caps_extraction_facts():
    quote = "Mercy Health uses FirstHx. " + "x" * 600
    retained = RetainedSource.build(
        requested_url="https://example.com/requested",
        final_url="https://example.com/final",
        content=quote,
        retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
        discovery_run_id="run-1",
        entrypoint="test",
    )

    class Extractor:
        async def extract(self, source, profiles, seeded_entities, target_name=""):
            return [
                Assertion(
                    subject="Mercy Health",
                    subject_type="HealthSystem",
                    relation="Adopts",
                    object="FirstHx",
                    object_type="Product",
                    quote=quote,
                    profile="customers",
                ),
                *[
                    Assertion(
                        subject=f"Unsupported {index}",
                        subject_type="HealthSystem",
                        relation="Adopts",
                        object="FirstHx",
                        object_type="Product",
                        quote=quote,
                        profile="customers",
                    )
                    for index in range(11)
                ],
            ]

    work = extraction(retained.source_id)
    result = await build_ingestion_graph(
        _deps(store=Store(retained), graph=Graph(), extractor=Extractor())
    ).ainvoke({"extraction": work})

    audit = result["trace_output"]["audit"]
    assert audit["extraction"] == {
        "id": work.extraction_id,
        "source_url": "https://example.com/final",
        "profile": "customers",
    }
    # 12 assertions extracted (1 real + 11 "Unsupported N"), capped at 10 for
    # the raw-extraction audit -- this cap is independent of grounding and
    # unaffected by ELI-1192.
    assert len(audit["assertions"]) == 10
    # ELI-1192: subject_not_in_evidence no longer rejects any of these for
    # the customers profile, so all 12 are accepted -- capped_audit_facts
    # caps that list at 10 too (Mercy Health + "Unsupported 0".."Unsupported 8").
    assert len(audit["accepted_facts"]) == 10
    assert audit["accepted_facts"][0] == {
        "subject": "Mercy Health",
        "subject_type": "HealthSystem",
        "relation": "Adopts",
        "object": "FirstHx",
        "object_type": "Product",
        "quote": quote[:500],
    }
    assert audit["grounding_rejections"] == []
    assert all(len(item["quote"]) == 500 for item in audit["assertions"])
    # ELI-1544: the referent runs inside grounding now, so there is no
    # separate retry stage to report on.
    assert "referent_retry" not in audit or audit["referent_retry"] is None
    assert audit["persistence_diagnostics"] is None


class RaisingSemanticModel:
    def with_structured_output(self, schema, **kwargs):
        class StructuredModel:
            async def ainvoke(self, messages):
                raise RuntimeError("simulated model failure")

        return StructuredModel()


@pytest.mark.asyncio
async def test_semantic_check_error_does_not_abort_ingestion():
    retained = RetainedSource.build(
        requested_url="https://example.com",
        final_url="https://example.com",
        content="Mercy Health uses FirstHx.",
        retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
        discovery_run_id="run-1",
        entrypoint="test",
    )

    class Extractor:
        async def extract(self, source, profiles, seeded_entities, target_name=""):
            return [
                Assertion(
                    subject="Mercy Health",
                    subject_type="HealthSystem",
                    relation="Adopts",
                    object="FirstHx",
                    object_type="Product",
                    quote=source.content,
                    profile="customers",
                )
            ]

    store = Store(retained)
    result = await build_ingestion_graph(
        _deps(
            store=store,
            graph=Graph(),
            extractor=Extractor(),
            chat_model=RaisingSemanticModel(),
        )
    ).ainvoke({"extraction": extraction(retained.source_id)})

    assert result["outcome"] == "succeeded"
    assert store.finished == [(extraction(retained.source_id), "succeeded")]


@pytest.mark.asyncio
async def test_a_spliced_quote_is_cut_back_and_sent_to_the_referent():
    """The extractor splices about 1 quote in 11 -- it lifts two real pieces of
    the page and drops what sits between them, which happens more as the number
    of relations in one call grows. Both names are usually inside the splice, so
    the referent is skipped, and the contiguity check then kills the relation
    with nothing having read the page. 447 good relations died this way on the
    corpus.

    A splice is deterministic to spot, so cut the quote back to its longest real
    run and let the referent settle it. The cut is unconditional about the
    referent: whether the surviving run still names both sides or not, the page
    gets read before anything is thrown away.
    """
    page = (
        "The Weight Clinic signed up last spring.\n\n"
        + "Filler about something else entirely. " * 20
        + "\n\nFirstHx is live across all of their sites."
    )
    spliced = "The Weight Clinic signed up last spring. FirstHx is live across all of their sites."
    chat_model = ReferentTestModel(justification_quote=PROOF_SENTENCE)

    await _ingest(_page(page), _one_assertion(spliced), chat_model)

    assert chat_model.referent_calls == 1


@pytest.mark.asyncio
async def test_a_spliced_quote_is_never_rejected_for_being_spliced():
    """The contiguity check must never be the thing that kills a relation.

    A splice is the extractor lifting two real pieces of the page and dropping
    what sits between them. Both pieces are on the page, so the span that holds
    them is too, and taking that span back off the page gives a quote that is a
    page substring by construction. Nothing is invented and nothing is thrown
    away, so there is no case left for `quote_not_contiguous` to fire on -- a
    quote that is not on the page at all is already rejected before it, as
    `quote_not_in_source`.
    """
    # Neither piece is long enough to stand on its own, so there is no run to
    # fall back to -- only the span across both of them saves this.
    page = (
        "The Weight Clinic is here.\n\n"
        + "Filler about something else entirely. " * 20
        + "\n\nFirstHx runs the intake."
    )
    spliced = "The Weight Clinic is here. FirstHx runs the intake."
    chat_model = ReferentTestModel(justification_quote=PROOF_SENTENCE)

    state = await _ingest(_page(page), _one_assertion(spliced), chat_model)

    assert "quote_not_contiguous" not in {
        rejection.code for rejection in state["grounding_rejections"]
    }
