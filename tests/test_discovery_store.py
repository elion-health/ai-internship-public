import json
from datetime import UTC, datetime

import pytest

from ai_internship.discovery.contracts import (
    DiscoveryRun,
    FrontierRecord,
    FrontierStatus,
    RetainedSource,
)
from ai_internship.discovery.store import DiscoveryStore
from ai_internship.research.contracts import ResearchTarget


class RecordingDriver:
    def __init__(self):
        self.calls = []

    async def execute_query(self, query, **params):
        self.calls.append({"query": query, "params": params})
        return [], None, None


class ResultDriver(RecordingDriver):
    def __init__(self, results):
        super().__init__()
        self.results = list(results)

    async def execute_query(self, query, **params):
        self.calls.append({"query": query, "params": params})
        return self.results.pop(0), None, None


def retained_source(content="same body"):
    return RetainedSource.build(
        requested_url="https://example.com/a",
        final_url="https://example.com/a",
        content=content,
        retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
        discovery_run_id="run-1",
        entrypoint="deep_agent",
    )


@pytest.mark.asyncio
async def test_store_serializes_discovery_run_config_for_neo4j_properties():
    driver = RecordingDriver()
    store = DiscoveryStore(driver)
    run = DiscoveryRun(
        run_id="run-1",
        entrypoint="discovery_langgraph",
        config={"scope_url": "https://example.com", "budgets": {"scrapes": 10}},
        started_at=datetime(2026, 7, 16, tzinfo=UTC),
    )

    await store.start_run(run)

    assert json.loads(driver.calls[0]["params"]["properties"]["config"])["budgets"] == {
        "scrapes": 10
    }


@pytest.mark.asyncio
async def test_store_persists_thread_id_without_writing_null_thread_ids():
    driver = RecordingDriver()
    store = DiscoveryStore(driver)

    await store.start_run(
        DiscoveryRun(
            run_id="run-1",
            thread_id="thread-1",
            entrypoint="stateful_research",
            started_at=datetime(2026, 7, 17, tzinfo=UTC),
        )
    )
    assert driver.calls[0]["params"]["properties"]["thread_id"] == "thread-1"

    driver.calls.clear()
    await store.start_run(
        DiscoveryRun(
            run_id="run-2",
            entrypoint="stateful_research",
            started_at=datetime(2026, 7, 17, tzinfo=UTC),
        )
    )
    assert "thread_id" not in driver.calls[0]["params"]["properties"]


@pytest.mark.asyncio
async def test_store_records_and_finishes_provider_call():
    driver = RecordingDriver()
    store = DiscoveryStore(driver)

    await store.start_provider_call(
        call_id="call-1",
        run_id="run-1",
        stage="first_party_fetch",
        url="https://example.com/a",
    )
    await store.finish_provider_call("call-1", status="succeeded", elapsed_seconds=0.25)

    assert "ProviderCall" in driver.calls[0]["query"]
    assert driver.calls[1]["params"]["elapsed_seconds"] == 0.25


@pytest.mark.asyncio
async def test_store_finishes_run_with_serialized_metrics():
    driver = RecordingDriver()

    await DiscoveryStore(driver).finish_run(
        "run-1", "completed", {"first_party": {"provider_calls": 41}}
    )

    assert json.loads(driver.calls[0]["params"]["metrics"])["first_party"]["provider_calls"] == 41


@pytest.mark.asyncio
async def test_store_summarizes_durable_provider_calls_by_stage():
    driver = ResultDriver(
        [
            [
                {
                    "stage": "first_party_fetch",
                    "calls": 2,
                    "succeeded": 1,
                    "failed": 0,
                    "running": 1,
                }
            ]
        ]
    )

    summary = await DiscoveryStore(driver).summarize_provider_calls("run-1")

    assert summary == {"first_party_fetch": {"calls": 2, "succeeded": 1, "failed": 0, "running": 1}}


@pytest.mark.asyncio
async def test_frontier_is_merged_by_target_and_canonical_url():
    driver = RecordingDriver()
    record = FrontierRecord(
        target_id="target-1",
        canonical_url="https://example.com/customers",
        requested_url="https://example.com/customers?utm_source=x",
        origin="first_party",
        status=FrontierStatus.DISCOVERED,
        discovery_method="map",
        discovery_run_id="run-1",
    )

    await DiscoveryStore(driver).upsert_frontier(record)

    assert "{target_id: $target_id, canonical_url: $canonical_url}" in driver.calls[0]["query"]
    assert driver.calls[0]["params"]["canonical_url"] == "https://example.com/customers"


@pytest.mark.asyncio
async def test_frontier_merge_persists_discovery_provenance_lists():
    driver = RecordingDriver()
    record = FrontierRecord(
        target_id="target-1",
        canonical_url="https://example.com/customers",
        requested_url="https://example.com/customers",
        origin="external",
        status=FrontierStatus.PENDING,
        discovery_method="external_agent",
        discovery_run_id="run-1",
        queries=["example customers"],
        discovery_methods=["search"],
        provider_call_ids=["search:1"],
        profiles=["customers"],
    )

    await DiscoveryStore(driver).upsert_frontier(record)

    assert "reduce(items = coalesce(frontier.queries, [])" in driver.calls[0]["query"]
    assert driver.calls[0]["params"]["provider_call_ids"] == ["search:1"]
    assert driver.calls[0]["params"]["profiles"] == ["customers"]


@pytest.mark.asyncio
async def test_completed_inventory_and_pending_fetches_are_target_scoped():
    driver = ResultDriver(
        [
            [{"completed": True}],
            [
                {
                    "frontier": {
                        "target_id": "target-1",
                        "canonical_url": "https://example.com/a",
                        "requested_url": "https://example.com/a",
                        "origin": "first_party",
                        "status": "pending",
                        "discovery_method": "map",
                        "discovery_run_id": "run-1",
                    }
                }
            ],
        ]
    )
    store = DiscoveryStore(driver)

    assert await store.has_completed_inventory("target-1") is True
    pending = await store.list_pending_frontier("target-1", origin="first_party")

    assert [item.canonical_url for item in pending] == ["https://example.com/a"]


@pytest.mark.asyncio
async def test_list_target_source_ids_defaults_to_every_origin():
    driver = ResultDriver(
        [
            [
                {"source_id": "s1", "final_urls": ["https://vendor.example/a"]},
                {"source_id": "s2", "final_urls": ["https://vendor.example/b"]},
            ]
        ]
    )
    store = DiscoveryStore(driver)

    source_ids = await store.list_target_source_ids("target-1")

    assert source_ids == ["s1", "s2"]
    assert driver.calls[0]["params"]["first_party_only"] is False


@pytest.mark.asyncio
async def test_list_target_source_ids_can_scope_to_first_party_only():
    driver = ResultDriver([[{"source_id": "s1", "final_urls": ["https://vendor.example/a"]}]])
    store = DiscoveryStore(driver)

    source_ids = await store.list_target_source_ids("target-1", first_party_only=True)

    assert source_ids == ["s1"]
    call = driver.calls[0]
    assert call["params"]["first_party_only"] is True
    assert "source.entrypoint = 'first_party_acquisition'" in call["query"]


@pytest.mark.asyncio
async def test_list_target_source_ids_excludes_a_retained_excluded_host(monkeypatch):
    monkeypatch.setenv("DISCOVERY_EXCLUDED_HOSTS", "reference.example")
    # Regression: EXCLUDED_HOSTS was only enforced at
    # discovery time, so a source already sitting in Neo4j from a prior run --
    # including one whose final URL only resolved to an excluded host after a
    # redirect -- was still handed to extraction on a resumed run.
    driver = ResultDriver(
        [
            [
                {"source_id": "s1", "final_urls": ["https://vendor.example/a"]},
                {
                    "source_id": "s2",
                    "final_urls": ["https://reference.example/products/vendor/customers"],
                },
            ]
        ]
    )
    store = DiscoveryStore(driver)

    source_ids = await store.list_target_source_ids("target-1")

    assert source_ids == ["s1"]


@pytest.mark.asyncio
async def test_store_records_inventory_truncation_flag():
    driver = RecordingDriver()

    await DiscoveryStore(driver).mark_inventory_completed(
        "target-1",
        "run-1",
        map_limit=3,
        mapped_count=1,
        inventory_may_be_truncated=True,
    )

    assert "inventory_may_be_truncated = $inventory_may_be_truncated" in driver.calls[0]["query"]
    assert driver.calls[0]["params"]["mapped_count"] == 1
    assert driver.calls[0]["params"]["inventory_may_be_truncated"] is True


@pytest.mark.asyncio
async def test_store_loads_or_creates_target():
    target = ResearchTarget.build("Fathom", "https://example.com")
    driver = ResultDriver([[{"target": target.model_dump(mode="json")}]])

    loaded = await DiscoveryStore(driver).load_or_create_target(target)

    assert loaded == target
    assert "ResearchTarget" in driver.calls[0]["query"]


class FakeEmbedder:
    def __init__(self, vector):
        self.vector = vector
        self.calls = []

    async def create(self, input_data):
        self.calls.append(input_data)
        return self.vector


@pytest.mark.asyncio
async def test_seed_marketplace_entity_writes_marketplace_id_and_embedding():
    """Without name_embedding, graphiti's node_similarity_search can never
    find the seeded node -- see Macroscope review on PR #49."""
    driver = RecordingDriver()
    embedder = FakeEmbedder([0.1, 0.2, 0.3])

    await DiscoveryStore(driver).seed_marketplace_entity(
        marketplace_id="mid-1", name="AgileMD Clinical Pathways", embedder=embedder
    )

    assert embedder.calls == [["AgileMD Clinical Pathways"]]
    params = driver.calls[0]["params"]
    assert params["marketplace_id"] == "mid-1"
    assert params["name_embedding"] == [0.1, 0.2, 0.3]
    assert params["label"] == "Product"


@pytest.mark.asyncio
async def test_seed_marketplace_entity_merges_on_marketplace_id_not_seed_id():
    """graph_seed's bulk import writes seed_id as the composite
    "{marketplace_id}@{source_id}", not the bare id (graph_seed/seed.py's
    node_record) -- merging on seed_id here would miss an already-imported
    node and create a disconnected duplicate instead of enriching the real
    one (Macroscope review, PR #49)."""
    driver = RecordingDriver()
    embedder = FakeEmbedder([0.1])

    await DiscoveryStore(driver).seed_marketplace_entity(
        marketplace_id="mid-1", name="AgileMD Clinical Pathways", embedder=embedder
    )

    assert "MERGE (n:Entity {marketplace_id: $marketplace_id})" in driver.calls[0]["query"]
