import json

import pytest

from ai_internship.discovery.store import DiscoveryStore
from ai_internship.enrichment.contracts import (
    Assertion,
    ExtractionWork,
    GroundingRejection,
)


class ResultDriver:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def execute_query(self, query, **params):
        self.calls.append({"query": query, "params": params})
        return self.results.pop(0), None, None


@pytest.mark.asyncio
async def test_missing_extraction_schedules_revision_one():
    work = await DiscoveryStore(ResultDriver([[]])).plan_extraction(
        "target-1", "source-1", "customers", "extractor-v1", target_name="Fathom"
    )

    assert work.revision == 1
    assert work.target_id == "target-1"


@pytest.mark.asyncio
async def test_completed_current_extraction_is_skipped():
    store = DiscoveryStore(ResultDriver([[{"revision": 1, "outcome": "succeeded"}]]))
    work = await store.plan_extraction(
        "target-1", "source-1", "customers", "extractor-v1", target_name="Fathom"
    )

    assert work is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    ["no_facts", "no_profile", "grounding_failure", "resolution_ambiguity"],
)
async def test_terminal_current_extraction_is_skipped(outcome):
    store = DiscoveryStore(ResultDriver([[{"revision": 1, "outcome": outcome}]]))

    work = await store.plan_extraction(
        "target-1", "source-1", "customers", "extractor-v1", target_name="Fathom"
    )

    assert work is None


@pytest.mark.asyncio
async def test_forcing_profile_creates_next_revision():
    store = DiscoveryStore(ResultDriver([[{"revision": 2, "outcome": "succeeded"}]]))
    work = await store.plan_extraction(
        "target-1", "source-1", "customers", "extractor-v1", target_name="Fathom", force=True
    )

    assert work.revision == 3


@pytest.mark.asyncio
async def test_running_work_reuses_same_identity_for_retry():
    store = DiscoveryStore(ResultDriver([[{"revision": 2, "outcome": "running"}]]))
    work = await store.plan_extraction(
        "target-1", "source-1", "customers", "extractor-v1", target_name="Fathom"
    )

    assert work.revision == 2


@pytest.mark.asyncio
async def test_extraction_outcome_is_keyed_by_work_identity():
    driver = ResultDriver([[], []])
    work = ExtractionWork.build(
        "source-1",
        "customers",
        "extractor-v1",
        revision=1,
        target_id="target-1",
        target_name="Fathom",
        content_hash="content-1",
    )
    store = DiscoveryStore(driver)

    await store.start_extraction(work)
    await store.finish_extraction(work, "succeeded", [])

    assert all(call["params"]["extraction_id"] == work.extraction_id for call in driver.calls)


@pytest.mark.asyncio
async def test_extraction_run_persists_grounding_rejections_as_json():
    driver = ResultDriver([[]])
    work = ExtractionWork.build(
        "source-1", "customers", "extractor-v1", revision=1, target_id="target-1"
    )
    rejection = GroundingRejection(
        code="relationship_cue_missing",
        assertion=Assertion(
            subject="Mercy",
            subject_type="HealthSystem",
            relation="Adopts",
            object="FirstHx",
            object_type="Product",
            quote="Mercy evaluated FirstHx",
            profile="customers",
        ),
    )

    await DiscoveryStore(driver).finish_extraction(work, "grounding_failure", [rejection])

    assert json.loads(driver.calls[0]["params"]["grounding_rejections"]) == [
        rejection.model_dump(mode="json")
    ]


def test_extraction_identity_is_shared_by_matching_target_content():
    first = ExtractionWork.build(
        "source-1",
        "customers",
        "extractor-v1",
        revision=1,
        target_id="target-1",
        content_hash="same-content",
    )
    second = ExtractionWork.build(
        "source-2",
        "customers",
        "extractor-v1",
        revision=1,
        target_id="target-1",
        content_hash="same-content",
    )

    assert first.extraction_id == second.extraction_id
