import pytest

from ai_internship.enrichment import profiles
from ai_internship.enrichment.contracts import ExtractionWork
from ai_internship.enrichment.profiles import PROFILES, extractor_version


def test_profiles_define_versions():
    # discovery_instructions used to live here too, but moved to Braintrust
    # (customer-discovery-f006 / integration-discovery-c2a2, ELI-1309) --
    # see discovery/deep_agent.py's DISCOVERY_PROMPT_SLUGS.
    for profile in (PROFILES["customers"], PROFILES["integrations"]):
        assert profile.version


@pytest.mark.asyncio
async def test_extractor_version_is_stable_and_profile_specific():
    first = await extractor_version("customers", model="openai/gpt-4.1-mini")

    assert first == await extractor_version("customers", model="openai/gpt-4.1-mini")
    assert first != await extractor_version("integrations", model="openai/gpt-4.1-mini")


@pytest.mark.asyncio
async def test_extractor_version_changes_when_semantic_check_prompt_changes(monkeypatch):
    """Editing only the semantic-check prompt (grounding.py's semantic_rejection_reason)
    must still change extractor_version -- otherwise before/after extraction runs would
    collide under the same hash and be indistinguishable in Neo4j."""
    from types import SimpleNamespace

    from ai_internship.enrichment import profiles

    semantic_check_version = "v1"

    def _fake_load_prompt(*, slug, **kwargs):
        version = (
            f"test-version:{semantic_check_version}"
            if slug == "semantic-grounding-check-customers-fe3b"
            else f"test-version:{slug}"
        )
        return SimpleNamespace(version=version, build=lambda **kw: {"messages": []})

    monkeypatch.setattr(profiles, "load_prompt", _fake_load_prompt)

    before = await extractor_version("customers", model="openai/gpt-4.1-mini")
    semantic_check_version = "v2"
    after = await extractor_version("customers", model="openai/gpt-4.1-mini")

    assert before != after


@pytest.mark.asyncio
async def test_customer_extractor_version_loads_prompt_off_event_loop(monkeypatch):
    calls = []

    class Loop:
        async def run_in_executor(self, executor, function):
            calls.append((executor, function))
            return function()

    monkeypatch.setattr(profiles.asyncio, "get_running_loop", lambda: Loop())

    await extractor_version("customers", model="openai/gpt-4.1-mini")

    assert calls[0][0] is None


def test_extraction_identity_includes_revision():
    first = ExtractionWork.build("source-1", "customers", "extractor-v1", revision=1)
    second = ExtractionWork.build("source-1", "customers", "extractor-v1", revision=2)

    assert first.extraction_id != second.extraction_id
