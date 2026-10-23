import pytest

from scripts.reset_target_state import delete_scope, gather_scope


class _FakeDriver:
    """Dispatches by a distinguishing substring in each query, matching the
    four distinct queries gather_scope issues."""

    def __init__(self, responses: dict[str, list[dict]]):
        self.responses = responses
        self.queries: list[str] = []

    async def execute_query(self, query, **params):
        self.queries.append(query)
        if "OPTIONAL MATCH (target)-[:HAS_SOURCE]" in query:
            return self.responses["target_sources"], None, None
        if "MATCH (other:ResearchTarget)" in query:
            return self.responses.get("shared_sources", []), None, None
        if "EXTRACTED" in query:
            return self.responses.get("extractions", []), None, None
        if "run.config CONTAINS" in query:
            return self.responses.get("discovery_runs", []), None, None
        raise AssertionError(f"unexpected query: {query}")


class _DeleteDriver:
    def __init__(self):
        self.queries = []

    async def execute_query(self, query, **params):
        self.queries.append(query)
        return [], None, None


@pytest.mark.asyncio
async def test_gather_scope_excludes_sources_shared_with_another_target():
    """Regression for a real bug caught by automated review on ELI-1122's PR:
    RetainedSource is globally MERGEd by source_id, so two different targets
    (confirmed live: two duplicate Flatiron OncoEMR target nodes registered
    under different URLs) can share a HAS_SOURCE edge to the same node.
    Deleting a shared source must not happen -- it would destroy the other
    target's extractions too."""
    driver = _FakeDriver(
        {
            "target_sources": [
                {
                    "found_target": "target-1",
                    "all_source_ids": ["owned-source", "shared-source"],
                    "frontier_urls": ["https://example.com/a"],
                }
            ],
            "shared_sources": [{"source_id": "shared-source"}],
            "extractions": [{"extraction_ids": ["extraction-1"]}],
            "discovery_runs": [{"discovery_run_ids": ["run-1", "orphaned-run-2"]}],
        }
    )

    scope = await gather_scope(driver, "target-1")

    assert scope["source_ids"] == ["owned-source"]
    assert scope["shared_source_ids"] == ["shared-source"]
    assert scope["extraction_ids"] == ["extraction-1"]
    assert scope["discovery_run_ids"] == ["run-1", "orphaned-run-2"]


@pytest.mark.asyncio
async def test_gather_scope_includes_discovery_runs_with_no_downstream_data():
    """Regression for a real bug caught by automated review: a DiscoveryRun
    that fails before ever producing a FrontierUrl or RetainedSource has no
    DISCOVERED/RETRIEVED relationship to traverse from the target, so
    matching by relationship alone leaves it (and its ProviderCall children)
    behind after a "reset". Matching by the target_id embedded in
    DiscoveryRun.config catches it regardless. Confirmed live: 4 real runs
    for a target vs. only 2 found via relationship traversal."""
    driver = _FakeDriver(
        {
            "target_sources": [
                {"found_target": "target-1", "all_source_ids": [], "frontier_urls": []}
            ],
            "extractions": [{"extraction_ids": []}],
            "discovery_runs": [{"discovery_run_ids": ["failed-run-with-no-sources"]}],
        }
    )

    scope = await gather_scope(driver, "target-1")

    assert scope["discovery_run_ids"] == ["failed-run-with-no-sources"]


@pytest.mark.asyncio
async def test_gather_scope_returns_empty_for_unknown_target():
    driver = _FakeDriver({"target_sources": [{"found_target": None}]})

    scope = await gather_scope(driver, "missing-target")

    assert scope == {
        "source_ids": [],
        "shared_source_ids": [],
        "extraction_ids": [],
        "frontier_urls": [],
        "discovery_run_ids": [],
    }


@pytest.mark.asyncio
async def test_delete_scope_removes_graphiti_episodic_nodes():
    driver = _DeleteDriver()

    await delete_scope(
        driver,
        "target-1",
        {
            "source_ids": ["source-1"],
            "extraction_ids": ["extraction-1"],
            "frontier_urls": [],
            "discovery_run_ids": [],
        },
    )

    assert any("episode:Episodic" in query for query in driver.queries)
