from ai_internship.discovery.contracts import FrontierStatus, RetainedSource


def test_retained_source_id_changes_when_content_changes():
    first = RetainedSource.build(
        requested_url="https://example.com/a",
        final_url="https://example.com/a",
        content="first body",
        retrieved_at="2026-07-16T12:00:00+00:00",
        discovery_run_id="run-1",
        entrypoint="deep_agent",
    )

    second = first.model_copy(update={"content": "second body"}).with_recomputed_identity()

    assert first.source_id != second.source_id
    assert first.content_hash != second.content_hash


def test_frontier_status_is_closed_over_documented_values():
    assert {status.value for status in FrontierStatus} == {
        "discovered",
        "pending",
        "fetched",
        "failed",
        "excluded",
        "unchanged",
        "removed",
    }


def test_source_identity_uses_canonical_final_url():
    values = {
        "requested_url": "https://example.com/a",
        "content": "same body",
        "retrieved_at": "2026-07-16T12:00:00+00:00",
        "discovery_run_id": "run-1",
        "entrypoint": "deep_agent",
    }

    first = RetainedSource.build(final_url="HTTPS://EXAMPLE.COM/a/", **values)
    second = RetainedSource.build(final_url="https://example.com/a", **values)

    assert first.source_id == second.source_id
