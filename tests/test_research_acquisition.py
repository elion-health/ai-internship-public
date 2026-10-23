import asyncio
import time

import pytest

from ai_internship.discovery.contracts import FrontierRecord, FrontierStatus
from ai_internship.discovery.providers import (
    FetchedPage,
    InteractiveEvidence,
    InteractiveEvidenceResult,
    LogoEvidence,
)
from ai_internship.research import acquisition as research_acquisition
from ai_internship.research.acquisition import FirstPartyAcquisition
from ai_internship.research.contracts import ResearchTarget


def target():
    return ResearchTarget.build("Example", "https://example.com")


def frontier(url, *, origin="first_party", status=FrontierStatus.PENDING):
    return FrontierRecord(
        target_id=target().target_id,
        canonical_url=url,
        requested_url=url,
        origin=origin,
        status=status,
        discovery_method="map",
        discovery_run_id="run-1",
    )


class FakeStore:
    def __init__(self, *, inventory_complete=False, inventory_limit=None, pending=None):
        self.inventory_complete = inventory_complete
        self.inventory_limit = inventory_limit
        self.pending = pending or []
        self.events = []
        self.provider_calls = []
        self.sources = []
        self.frontier_records = []
        self.inventory_mapped_count = None
        self.inventory_may_be_truncated = None

    async def has_completed_inventory(self, target_id, *, map_limit=None):
        return self.inventory_complete and (
            map_limit is None
            or self.inventory_limit is not None
            and map_limit <= self.inventory_limit
        )

    async def upsert_frontier(self, record):
        self.frontier_records.append(record)
        self.events.append(("frontier", record.canonical_url, record.status.value))
        # Mirrors the real store's list_pending_frontier query, which filters by
        # status server-side -- an upsert to a non-queryable status (e.g. EXCLUDED)
        # must drop the record from `pending`, not just leave the old entry in place.
        self.pending = [item for item in self.pending if item.canonical_url != record.canonical_url]
        if record.status in {
            FrontierStatus.DISCOVERED,
            FrontierStatus.PENDING,
            FrontierStatus.FAILED,
        }:
            self.pending.append(record)

    async def mark_inventory_completed(self, target_id, run_id, **kwargs):
        self.inventory_complete = True
        self.inventory_limit = kwargs.get("map_limit")
        self.inventory_mapped_count = kwargs.get("mapped_count")
        self.inventory_may_be_truncated = kwargs.get("inventory_may_be_truncated")

    async def list_pending_frontier(self, target_id, *, origin=None):
        return [item for item in self.pending if origin is None or item.origin == origin]

    async def retain_for_target(self, target_id, source):
        self.events.append(("retained", source.final_url, source.source_id, source.quality_status))
        self.sources.append(source)
        self.pending = [item for item in self.pending if item.canonical_url != source.final_url]

    async def mark_frontier_failed(self, record, error):
        self.events.append(("failed", record.canonical_url, error))
        self.pending = [item for item in self.pending if item.canonical_url != record.canonical_url]

    async def start_provider_call(self, **kwargs):
        self.provider_calls.append(("started", kwargs))

    async def finish_provider_call(self, call_id, **kwargs):
        self.provider_calls.append(("finished", {"call_id": call_id, **kwargs}))


class Mapper:
    def __init__(self):
        self.calls = 0

    def map_site(self, url, limit):
        self.calls += 1
        return [
            f"{url}/customers",
            f"{url}/customers?utm_source=test",
            f"{url}/logo.svg",
            f"{url}/integrations",
        ]


class Scraper:
    def __init__(self):
        self.calls = []

    def scrape(self, url, *, wait_for_ms=0):
        self.calls.append((url, wait_for_ms))
        if url.endswith("/integrations"):
            raise RuntimeError("temporary")
        return FetchedPage(url=url, content=f"content for {url}")


class SuccessfulScraper(Scraper):
    def scrape(self, url, *, wait_for_ms=0):
        self.calls.append((url, wait_for_ms))
        return FetchedPage(url=url, content=f"content for {url}")


@pytest.mark.asyncio
async def test_provider_call_braintrust_span_logs_only_compact_metadata(monkeypatch):
    events = []
    flushes = []

    class Span:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def log(self, **kwargs):
            events.append(kwargs)

    def start_span(**kwargs):
        events.append(kwargs)
        return Span()

    monkeypatch.setattr(research_acquisition.braintrust, "start_span", start_span)
    monkeypatch.setattr(research_acquisition.braintrust, "flush", lambda: flushes.append(True))

    result = await research_acquisition._provider_call(
        FakeStore(),
        run_id="run-1",
        stage="first_party_map",
        url="https://example.com",
        timeout_seconds=10,
        function=lambda: "large provider result omitted",
    )

    assert result == "large provider result omitted"
    assert events[0] == {
        "name": "research_provider_call",
        "type": "tool",
        "input": {
            "stage": "first_party_map",
            "url": "https://example.com",
            "timeout_seconds": 10,
        },
    }
    assert events[1]["output"] == {"status": "running"}
    assert events[2]["output"] == {"status": "succeeded"}
    assert flushes == [True]


@pytest.mark.asyncio
async def test_provider_call_marks_flush_failure_and_logs_it(monkeypatch):
    events = []

    class Span:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def log(self, **kwargs):
            events.append(kwargs)

    monkeypatch.setattr(research_acquisition.braintrust, "start_span", lambda **_: Span())
    monkeypatch.setattr(
        research_acquisition.braintrust,
        "flush",
        lambda: (_ for _ in ()).throw(RuntimeError("flush failed")),
    )
    store = FakeStore()

    with pytest.raises(RuntimeError, match="flush failed"):
        await research_acquisition._provider_call(
            store,
            run_id="run-1",
            stage="first_party_map",
            url="https://example.com",
            timeout_seconds=10,
            function=lambda: None,
        )

    assert [call[1]["status"] for call in store.provider_calls if call[0] == "finished"] == [
        "failed"
    ]
    assert events[-1]["output"] == {"status": "failed", "error": "flush failed"}


@pytest.mark.asyncio
async def test_provider_call_supports_async_provider_functions(monkeypatch):
    monkeypatch.setattr(research_acquisition.braintrust, "flush", lambda: None)

    class Span:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def log(self, **kwargs):
            pass

    monkeypatch.setattr(research_acquisition.braintrust, "start_span", lambda **_: Span())

    async def expand(url, *, limit):
        return f"{url}:{limit}"

    assert (
        await research_acquisition._provider_call(
            FakeStore(),
            run_id="run-1",
            stage="first_party_interactive_evidence",
            url="https://example.com",
            timeout_seconds=10,
            function=expand,
            args=("https://example.com",),
            kwargs={"limit": 5},
        )
        == "https://example.com:5"
    )


def acquisition(
    store,
    mapper,
    scraper,
    *,
    first_party_limit=50,
    external_limit=10,
    timeout=0.05,
    interactive_limit=0,
):
    return FirstPartyAcquisition(
        store,
        mapper,
        scraper,
        first_party_page_limit=first_party_limit,
        external_fetch_limit=external_limit,
        provider_timeout_seconds=timeout,
        min_content_characters=1,
        render_retry_wait_ms=1,
        interactive_evidence_limit=interactive_limit,
    )


@pytest.mark.asyncio
async def test_inventory_and_fetch_are_separate_replayable_stages():
    store = FakeStore()
    mapper = Mapper()
    scraper = Scraper()
    client = acquisition(store, mapper, scraper)

    inventory = await client.map_first_party(target=target(), run_id="run-1", mode="initial")

    assert inventory.discovered_count == 3
    assert scraper.calls == []
    assert store.events[:3] == [
        ("frontier", "https://example.com/customers", "pending"),
        ("frontier", "https://example.com/integrations", "pending"),
        ("frontier", "https://example.com/logo.svg", "excluded"),
    ]

    fetched = await client.fetch_first_party(target=target(), run_id="run-1")

    assert [url for url, _ in scraper.calls] == [
        "https://example.com/customers",
        "https://example.com/integrations",
    ]
    assert fetched.retained_count == 1
    assert fetched.failure_count == 1


@pytest.mark.asyncio
async def test_first_party_inventory_excludes_hosts_outside_target_domain():
    class BoundaryMapper:
        def map_site(self, url, limit):
            return [
                "https://example.com/customers",
                "https://docs.example.com/integrations",
                "https://example.com.evil.test/phishing",
            ]

    store = FakeStore()

    result = await acquisition(store, BoundaryMapper(), SuccessfulScraper()).map_first_party(
        target=target(), run_id="run-1", mode="initial"
    )

    records = {record.canonical_url: record for record in store.frontier_records}
    assert records["https://example.com/customers"].status is FrontierStatus.PENDING
    assert records["https://docs.example.com/integrations"].status is FrontierStatus.PENDING
    assert records["https://example.com.evil.test/phishing"].status is FrontierStatus.EXCLUDED
    assert records["https://example.com.evil.test/phishing"].policy_reason == "external_host"
    assert result.excluded_count == 1


@pytest.mark.asyncio
async def test_resume_skips_completed_inventory_and_successful_fetches():
    store = FakeStore(inventory_complete=True)
    mapper = Mapper()
    scraper = Scraper()

    result = await acquisition(store, mapper, scraper).map_first_party(
        target=target(), run_id="run-2", mode="resume"
    )

    assert mapper.calls == 0
    assert scraper.calls == []
    assert result.provider_calls == 0


@pytest.mark.asyncio
async def test_initial_remaps_capped_inventory_when_limit_increases():
    store = FakeStore(inventory_complete=True, inventory_limit=2)
    mapper = Mapper()

    await acquisition(store, mapper, SuccessfulScraper(), first_party_limit=3).map_first_party(
        target=target(), run_id="run-2", mode="initial"
    )

    assert mapper.calls == 1


@pytest.mark.asyncio
async def test_capped_inventory_reports_map_limit_warning():
    class CappedMapper:
        def map_site(self, url, limit):
            return [f"{url}/{index}" for index in range(limit)]

    result = await acquisition(
        FakeStore(), CappedMapper(), SuccessfulScraper(), first_party_limit=3
    ).map_first_party(target=target(), run_id="run-1", mode="initial")

    assert result.warnings == [
        {"code": "first_party_map_limit_reached", "mapped_count": 3, "limit": 3}
    ]


@pytest.mark.asyncio
async def test_duplicate_capped_inventory_is_marked_may_be_truncated():
    class DuplicateCappedMapper:
        def map_site(self, url, limit):
            return [
                f"{url}/customers",
                f"{url}/customers?utm_source=one",
                f"{url}/customers?utm_source=two",
            ]

    store = FakeStore()

    result = await acquisition(
        store, DuplicateCappedMapper(), SuccessfulScraper(), first_party_limit=3
    ).map_first_party(target=target(), run_id="run-1", mode="initial")

    assert result.discovered_count == 1
    assert store.inventory_mapped_count == 1
    assert store.inventory_may_be_truncated is True
    assert result.warnings[0]["code"] == "first_party_map_limit_reached"


@pytest.mark.asyncio
async def test_first_party_fetch_limit_leaves_unattempted_urls_pending():
    records = [frontier(f"https://example.com/{index}") for index in range(3)]
    store = FakeStore(inventory_complete=True, pending=records)
    scraper = SuccessfulScraper()

    result = await acquisition(store, Mapper(), scraper, first_party_limit=2).fetch_first_party(
        target=target(), run_id="run-1"
    )

    assert result.provider_calls == 2
    assert [url for url, _ in scraper.calls] == [
        "https://example.com/0",
        "https://example.com/1",
    ]
    assert [item.requested_url for item in store.pending] == ["https://example.com/2"]
    assert result.warnings == [
        {
            "code": "first_party_fetch_limit_reached",
            "pending_count": 3,
            "scheduled_count": 2,
            "limit": 2,
        }
    ]


@pytest.mark.asyncio
async def test_fetch_re_excludes_stale_pending_url_never_re_screened_by_a_map_pass():
    # A record can be sitting as PENDING in the frontier without ever having gone
    # through map_first_party's exclusion check on this code version -- e.g. a
    # graph run resumed past a checkpointed, already-complete inventory stage (see
    # test_resume_skips_completed_inventory_and_successful_fetches), or frontier
    # rows persisted before the developer.* exclusion policy (ELI-1318) existed.
    # fetch_first_party must not blindly trust old PENDING status; it has to
    # re-screen against the current policy before ever scraping.
    stale = frontier("https://developer.example.com/docs/mcp")
    store = FakeStore(inventory_complete=True, pending=[stale])
    scraper = SuccessfulScraper()

    result = await acquisition(store, Mapper(), scraper).fetch_first_party(
        target=target(), run_id="run-2"
    )

    assert scraper.calls == []
    assert result.provider_calls == 0
    assert result.excluded_count == 1
    assert result.retained_count == 0
    excluded = next(
        record
        for record in store.frontier_records
        if record.canonical_url == "https://developer.example.com/docs/mcp"
    )
    assert excluded.status is FrontierStatus.EXCLUDED
    assert excluded.policy_reason == "developer_documentation"


@pytest.mark.asyncio
async def test_stale_excluded_records_dont_consume_the_fetch_limit_slot():
    # Two stale developer.* records sort ahead of two real, fetchable URLs
    # (list_pending_frontier orders by canonical_url). With a fetch limit of 2,
    # the re-screening must not let the two exclusions eat both slots and push
    # the real URLs out of this run's window entirely.
    records = [
        frontier("https://developer.example.com/a"),
        frontier("https://developer.example.com/b"),
        frontier("https://example.com/customers"),
        frontier("https://example.com/integrations"),
    ]
    store = FakeStore(inventory_complete=True, pending=records)
    scraper = SuccessfulScraper()

    result = await acquisition(store, Mapper(), scraper, first_party_limit=2).fetch_first_party(
        target=target(), run_id="run-2"
    )

    assert sorted(url for url, _ in scraper.calls) == [
        "https://example.com/customers",
        "https://example.com/integrations",
    ]
    assert result.provider_calls == 2
    assert result.excluded_count == 2
    assert [item.requested_url for item in store.pending] == []


@pytest.mark.asyncio
async def test_provider_timeout_marks_only_attempted_url_failed():
    class SlowScraper:
        def scrape(self, url, *, wait_for_ms=0):
            time.sleep(0.1)
            return FetchedPage(url=url, content="slow")

    record = frontier("https://example.com/slow")
    store = FakeStore(inventory_complete=True, pending=[record])

    result = await acquisition(store, Mapper(), SlowScraper()).fetch_first_party(
        target=target(), run_id="run-1"
    )

    assert result.failure_count == 1
    assert store.events[-1][0:2] == ("failed", "https://example.com/slow")
    assert store.provider_calls[-1][1]["status"] == "failed"


@pytest.mark.asyncio
async def test_external_fetch_limit_keeps_unselected_and_excess_urls_unfetched():
    selected = [
        type("Candidate", (), {"url": f"https://external.example/{index}"})() for index in range(3)
    ]
    unselected = type("Candidate", (), {"url": "https://external.example/other"})()
    store = FakeStore()
    scraper = SuccessfulScraper()

    result = await acquisition(store, Mapper(), scraper, external_limit=2).run_external(
        target=target(),
        run_id="run-1",
        observed=[*selected, unselected],
        selected=selected,
    )

    assert result.provider_calls == 2
    assert [url for url, _ in scraper.calls] == [
        "https://external.example/0",
        "https://external.example/1",
    ]
    assert {item.requested_url: item.status for item in store.pending} == {
        "https://external.example/2": FrontierStatus.PENDING,
        "https://external.example/other": FrontierStatus.DISCOVERED,
    }


@pytest.mark.asyncio
async def test_loading_page_is_retried_once_and_retained_as_ineligible():
    class LoadingThenContent:
        def __init__(self):
            self.calls = []

        def scrape(self, url, *, wait_for_ms=0):
            self.calls.append(wait_for_ms)
            return FetchedPage(url=url, content="Loading ...")

    store = FakeStore(pending=[frontier("https://example.com/loading")])
    scraper = LoadingThenContent()
    client = FirstPartyAcquisition(
        store,
        Mapper(),
        scraper,
        first_party_page_limit=1,
        external_fetch_limit=1,
        provider_timeout_seconds=0.05,
        min_content_characters=200,
        render_retry_wait_ms=5000,
    )

    result = await client.fetch_first_party(target=target(), run_id="run-1")

    assert scraper.calls == [0, 5000]
    assert result.provider_calls == 2
    assert not result.source_ids
    assert store.events[-1][-1] == "excluded"


@pytest.mark.asyncio
async def test_external_page_without_target_name_is_retained_but_not_eligible_for_extraction():
    class UnrelatedScraper:
        def scrape(self, url, *, wait_for_ms=0):
            return FetchedPage(url=url, content="Unrelated vendor news and product updates.")

    record = frontier("https://external.example/news", origin="external")
    store = FakeStore(pending=[record])

    result = await acquisition(store, Mapper(), UnrelatedScraper())._fetch_pending(
        target(), "run-1", "external", max_calls=1
    )

    assert result.retained_count == 1
    assert store.sources[0].quality_status == "excluded"
    assert store.sources[0].quality_reason == "target_not_mentioned"


@pytest.mark.asyncio
async def test_external_pages_with_matching_content_hash_schedule_one_source():
    class DuplicateScraper:
        def scrape(self, url, *, wait_for_ms=0):
            return FetchedPage(url=url, content="Example customer story")

    records = [
        frontier("https://external.example/one", origin="external"),
        frontier("https://external.example/two", origin="external"),
    ]
    store = FakeStore(pending=records)

    result = await acquisition(
        store, Mapper(), DuplicateScraper(), external_limit=2
    )._fetch_pending(target(), "run-1", "external", max_calls=2)

    assert result.retained_count == 2
    assert len(result.source_ids) == 1


@pytest.mark.asyncio
async def test_acquisition_retains_raw_html_separately_from_main_content():
    class RawContentScraper:
        def scrape(self, url, *, wait_for_ms=0):
            return FetchedPage(
                url=url,
                content="Example customer story",
                raw_content="<html><nav>menu</nav><main>Example customer story</main></html>",
            )

    store = FakeStore(pending=[frontier("https://example.com/customer")])

    await acquisition(store, Mapper(), RawContentScraper()).fetch_first_party(
        target=target(), run_id="run-1"
    )

    assert store.sources[0].content == "Example customer story"
    assert store.sources[0].raw_content == (
        "<html><nav>menu</nav><main>Example customer story</main></html>"
    )


@pytest.mark.asyncio
async def test_first_party_acquisition_appends_interactive_evidence_with_provenance():
    class InteractiveScraper(SuccessfulScraper):
        def expand_interactive_evidence(self, url, raw_html, *, limit):
            assert url == "https://example.com/customer"
            assert limit == 5
            return InteractiveEvidenceResult(
                items=[
                    InteractiveEvidence(
                        control_type="carousel",
                        control_label="Show testimonial 2 of 5",
                        state=1,
                        attribution="Amy Palmer, AllerVie Health",
                        context="Customer testimonials",
                        content="AllerVie Health relies on Example.",
                    )
                ],
                logos=[
                    LogoEvidence(
                        alt_text="AllerVie Health",
                        image_url="https://example.com/allervie.svg",
                        context="Trusted by practices using Example.",
                    )
                ],
            )

    store = FakeStore(pending=[frontier("https://example.com/customer")])

    await acquisition(store, Mapper(), InteractiveScraper(), interactive_limit=5).fetch_first_party(
        target=target(), run_id="run-1"
    )

    assert (
        store.sources[0].content
        == """content for https://example.com/customer

## Interactive content

### carousel: Show testimonial 2 of 5 (state 1)
Source interaction: https://example.com/customer
Context: Customer testimonials
Attribution: Amy Palmer, AllerVie Health

AllerVie Health relies on Example.

## Supporting logo evidence

### Logo: AllerVie Health
Source image: https://example.com/allervie.svg
Alt text: AllerVie Health
Context: Trusted by practices using Example."""
    )


@pytest.mark.asyncio
async def test_interactive_expansion_failure_retains_static_first_party_source():
    class FailingInteractiveScraper(SuccessfulScraper):
        def expand_interactive_evidence(self, url, raw_html, *, limit):
            raise RuntimeError("browser unavailable")

    store = FakeStore(pending=[frontier("https://example.com/customer")])

    await acquisition(
        store, Mapper(), FailingInteractiveScraper(), interactive_limit=5
    ).fetch_first_party(target=target(), run_id="run-1")

    assert store.sources[0].content == "content for https://example.com/customer"
    assert store.sources[0].provider_metadata["interactive_evidence_error"] == "browser unavailable"


@pytest.mark.asyncio
async def test_interactive_evidence_is_assessed_before_first_party_extraction_eligibility():
    class SparseScraper(SuccessfulScraper):
        def scrape(self, url, *, wait_for_ms=0):
            return FetchedPage(url=url, content="short")

        def expand_interactive_evidence(self, url, raw_html, *, limit):
            return InteractiveEvidenceResult(
                items=[
                    InteractiveEvidence(
                        control_type="carousel",
                        control_label="Show testimonial",
                        state=0,
                        content=(
                            "Example is used by AllerVie Health to manage biologics operations."
                        ),
                    )
                ]
            )

    store = FakeStore(pending=[frontier("https://example.com/customer")])
    client = FirstPartyAcquisition(
        store,
        Mapper(),
        SparseScraper(),
        first_party_page_limit=1,
        external_fetch_limit=1,
        provider_timeout_seconds=0.05,
        min_content_characters=20,
        render_retry_wait_ms=1,
        interactive_evidence_limit=5,
    )

    result = await client.fetch_first_party(target=target(), run_id="run-1")

    assert result.source_ids == [store.sources[0].source_id]
    assert store.sources[0].quality_status == "eligible"


@pytest.mark.asyncio
async def test_external_acquisition_never_expands_interactive_evidence():
    class InteractiveScraper(SuccessfulScraper):
        def expand_interactive_evidence(self, url, raw_html, *, limit):
            raise AssertionError("external acquisition must not expand interactive evidence")

    store = FakeStore(pending=[frontier("https://external.example/customer", origin="external")])

    await acquisition(store, Mapper(), InteractiveScraper(), interactive_limit=5)._fetch_pending(
        target(), "run-1", "external", max_calls=1
    )

    assert store.sources[0].content == "content for https://external.example/customer"


@pytest.mark.asyncio
async def test_external_acquisition_reuses_agent_scraped_page():
    candidate = type("Candidate", (), {"url": "https://external.example/customer"})()
    prefetched = FetchedPage(url=candidate.url, content="Example customer story")
    store = FakeStore()
    scraper = SuccessfulScraper()

    result = await acquisition(store, Mapper(), scraper).run_external(
        target=target(),
        run_id="run-1",
        observed=[candidate],
        selected=[candidate],
        prefetched_pages=[prefetched],
    )

    assert result.provider_calls == 0
    assert scraper.calls == []
    assert store.sources[0].content == "Example customer story"


class SlowScraper(SuccessfulScraper):
    """Records how many scrapes are in flight at once, so a test can tell real
    concurrency from a loop that merely awaits."""

    def __init__(self, delay=0.05):
        super().__init__()
        self.delay = delay
        self.in_flight = 0
        self.peak_in_flight = 0

    async def scrape(self, url, *, wait_for_ms=0):
        self.calls.append((url, wait_for_ms))
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1
        return FetchedPage(url=url, content=f"content for {url}")


@pytest.mark.asyncio
async def test_pages_are_fetched_concurrently_up_to_the_limit():
    """Pages within a target are independent, and each is almost entirely network
    wait -- a 500-page vendor site fetched one page at a time measured over two
    hours of provider time."""
    urls = [f"https://example.com/page-{index}" for index in range(8)]
    store = FakeStore(pending=[frontier(url) for url in urls])
    scraper = SlowScraper()
    client = acquisition(store, Mapper(), scraper, timeout=5)
    client.fetch_concurrency = 4

    started = time.monotonic()
    result = await client.fetch_first_party(target=target(), run_id="run-1")
    elapsed = time.monotonic() - started

    assert result.retained_count == 8
    assert scraper.peak_in_flight > 1
    assert scraper.peak_in_flight <= 4
    # Serial would be 8 * 0.05s; four at a time is two batches.
    assert elapsed < 8 * scraper.delay


@pytest.mark.asyncio
async def test_concurrent_fetch_keeps_counters_and_dedupe_in_scheduled_order():
    """Outcomes are folded back in the order the records were scheduled, not the
    order they finished, so a run's source_ids do not depend on network timing."""

    class SameContentScraper(SlowScraper):
        async def scrape(self, url, *, wait_for_ms=0):
            page = await super().scrape(url, wait_for_ms=wait_for_ms)
            return page.model_copy(update={"content": "Example says the same thing on every page"})

    urls = [f"https://example.com/page-{index}" for index in range(6)]
    store = FakeStore(pending=[frontier(url) for url in urls])
    client = acquisition(store, Mapper(), SameContentScraper(), timeout=5)
    client.fetch_concurrency = 6

    result = await client.fetch_first_party(target=target(), run_id="run-1")

    assert result.retained_count == 6
    # Identical content across all six: only the first scheduled one is kept.
    assert len(result.source_ids) == 1
    assert result.source_ids == [store.sources[0].source_id]


@pytest.mark.asyncio
async def test_inventory_maps_the_site_root_not_the_deep_product_url():
    """A target's canonical URL is usually a deep product page, and the provider's
    map of a deep page can return only that page."""

    class RecordingMapper(Mapper):
        def __init__(self):
            super().__init__()
            self.mapped_urls = []

        def map_site(self, url, limit):
            self.mapped_urls.append(url)
            return [f"{url}about", f"{url}customers"]

    store = FakeStore()
    mapper = RecordingMapper()
    deep = ResearchTarget.build("Example", "https://example.com/products/thing/detail")
    client = acquisition(store, mapper, Scraper())

    await client.map_first_party(target=deep, run_id="run-1", mode="initial")

    assert mapper.mapped_urls == ["https://example.com/"]


@pytest.mark.asyncio
async def test_inventory_spends_its_limit_on_the_products_own_section_first():
    """Mapping the root is what makes a deep product URL yield anything, but on a
    large corporate domain it returns the whole company. Prioritize the product
    section before unrelated consumer pages consume the budget."""

    class WideMapper(Mapper):
        def map_site(self, url, limit):
            return [
                "https://example.com/blogs/credit-scores",
                "https://example.com/jobs/engineer",
                "https://example.com/healthcare/products/thing/pricing",
                "https://example.com/healthcare/products/thing/customers",
            ]

    store = FakeStore()
    deep = ResearchTarget.build("Example", "https://example.com/healthcare/products/thing/detail")
    client = acquisition(store, WideMapper(), Scraper(), first_party_limit=2)

    await client.map_first_party(target=deep, run_id="run-1", mode="initial")

    queued = sorted(record.canonical_url for record in store.frontier_records)
    assert queued == [
        "https://example.com/healthcare/products/thing/customers",
        "https://example.com/healthcare/products/thing/pricing",
    ]


@pytest.mark.asyncio
async def test_a_root_canonical_url_keeps_the_providers_own_order():
    """A vendor whose canonical URL is already the root has no narrower section to
    prefer, so nothing is reordered."""

    class WideMapper(Mapper):
        def map_site(self, url, limit):
            return ["https://example.com/a", "https://example.com/b"]

    store = FakeStore()
    client = acquisition(store, WideMapper(), Scraper(), first_party_limit=1)

    await client.map_first_party(
        target=ResearchTarget.build("Example", "https://example.com/"),
        run_id="run-1",
        mode="initial",
    )

    assert [record.canonical_url for record in store.frontier_records] == ["https://example.com/a"]


@pytest.mark.asyncio
async def test_inventory_accepts_the_host_a_moved_site_redirects_to():
    """A vendor whose site has moved answers its old address with a redirect, and
    the provider maps the destination rather than the address it was given.
    Comparing against the pre-redirect host alone can exclude every URL."""

    class MovedSiteMapper:
        def map_site(self, url, limit):
            return [
                "https://moved.test/customers",
                "https://moved.test/integrations",
            ]

    store = FakeStore()

    result = await acquisition(store, MovedSiteMapper(), SuccessfulScraper()).map_first_party(
        target=target(), run_id="run-1", mode="initial"
    )

    records = {record.canonical_url: record for record in store.frontier_records}
    assert records["https://moved.test/customers"].status is FrontierStatus.PENDING
    assert records["https://moved.test/integrations"].status is FrontierStatus.PENDING
    assert result.excluded_count == 0


@pytest.mark.asyncio
async def test_inventory_accepts_subdomains_of_the_host_a_moved_site_redirects_to():
    """The destination of a move can include both a root host and subdomains."""

    class MovedSiteMapper:
        def map_site(self, url, limit):
            return [
                "https://moved.test/customers",
                "https://moved.test/pricing",
                "https://portal.api.moved.test/status",
            ]

    store = FakeStore()

    result = await acquisition(store, MovedSiteMapper(), SuccessfulScraper()).map_first_party(
        target=target(), run_id="run-1", mode="initial"
    )

    records = {record.canonical_url: record for record in store.frontier_records}
    assert all(record.status is FrontierStatus.PENDING for record in records.values())
    assert result.excluded_count == 0


@pytest.mark.asyncio
async def test_a_sibling_section_does_not_count_as_the_products_own():
    """The product's section is matched as a path, not as a string prefix. A
    canonical URL of /products/thing gives the section /products, and
    /products/thing-other is a different product that would otherwise lead the
    ordering and spend the page limit before the real one."""

    class SiblingMapper(Mapper):
        def map_site(self, url, limit):
            return [
                "https://example.com/elsewhere/a",
                "https://example.com/products/thing-other/pricing",
                "https://example.com/products/thing/overview",
            ]

    store = FakeStore()
    deep = ResearchTarget.build("Example", "https://example.com/products/thing/detail")
    client = acquisition(store, SiblingMapper(), SuccessfulScraper(), first_party_limit=1)

    await client.map_first_party(target=deep, run_id="run-1", mode="initial")

    assert [record.canonical_url for record in store.frontier_records] == [
        "https://example.com/products/thing/overview"
    ]


@pytest.mark.asyncio
async def test_a_moved_site_mapped_mostly_on_a_subdomain_keeps_the_parent():
    """The destination is a site, and the busiest host on it is not necessarily
    its root. A map dominated by docs.moved.test must not exclude moved.test
    and its other subdomains as external."""

    class MovedSiteMapper:
        def map_site(self, url, limit):
            return [
                "https://docs.moved.test/a",
                "https://docs.moved.test/b",
                "https://docs.moved.test/c",
                "https://moved.test/customers",
                "https://support.moved.test/help",
            ]

    store = FakeStore()

    result = await acquisition(store, MovedSiteMapper(), SuccessfulScraper()).map_first_party(
        target=target(), run_id="run-1", mode="initial"
    )

    assert all(record.status is FrontierStatus.PENDING for record in store.frontier_records)
    assert result.excluded_count == 0


@pytest.mark.asyncio
async def test_two_unrelated_domains_under_one_suffix_do_not_become_one_site():
    """vendor.example and unrelated.example share a suffix, not a site. Only a
    host the map actually returned can be chosen, so no shared parent is
    invented and the pages of an unrelated company are never queued."""

    class SplitMapper:
        def map_site(self, url, limit):
            return [
                "https://vendor.example/a",
                "https://vendor.example/b",
                "https://unrelated.example/c",
            ]

    store = FakeStore()

    await acquisition(store, SplitMapper(), SuccessfulScraper()).map_first_party(
        target=target(), run_id="run-1", mode="initial"
    )

    records = {r.canonical_url: r for r in store.frontier_records}
    assert records["https://vendor.example/a"].status is FrontierStatus.PENDING
    assert records["https://unrelated.example/c"].status is FrontierStatus.EXCLUDED
    assert records["https://unrelated.example/c"].policy_reason == "external_host"
