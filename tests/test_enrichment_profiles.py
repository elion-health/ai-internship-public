import pytest

from ai_internship.discovery.contracts import RetainedSource
from ai_internship.enrichment.contracts import SeededEntity
from ai_internship.enrichment.profiles import PROFILES, LLMAssertionExtractor


def test_p0_registry_contains_only_requested_profiles():
    assert set(PROFILES) == {"customers", "integrations"}


class _RecordingModel:
    def __init__(self):
        self.calls = []
        self.outputs = {}

    def with_structured_output(self, schema, **kwargs):
        assert kwargs == {"method": "function_calling"}
        model = self

        class StructuredModel:
            async def ainvoke(self, messages):
                model.calls.append((schema.__name__, messages))
                return model.outputs[schema.__name__]

        return StructuredModel()


def _build_source(content: str) -> RetainedSource:
    return RetainedSource.build(
        requested_url="https://example.com",
        final_url="https://example.com",
        content=content,
        retrieved_at="2026-07-16T12:00:00+00:00",
        discovery_run_id="run-1",
        entrypoint="test",
    )


@pytest.mark.asyncio
async def test_integrations_extractor_builds_prompt_from_braintrust():
    """`integrations` loads its prompt from Braintrust (stubbed in conftest.py), same as
    `customers` -- ELI-1147 moved it off the hardcoded PROFILE_INSTRUCTIONS fallback."""
    model = _RecordingModel()
    model.outputs["IntegrationsResult"] = {
        "relationships": [
            {
                "product": "Acme Platform",
                "integrated_product": "Beta EHR",
                "quote": "Acme Platform integrates with Beta EHR",
            }
        ]
    }
    source = _build_source("Acme Platform integrates with Beta EHR.")

    assertions = await LLMAssertionExtractor(model).extract(
        source,
        ["integrations"],
        [SeededEntity(label="Product", name="Acme Platform", entity_uuid="product-1")],
    )

    assert assertions[0].subject == "Acme Platform"
    assert assertions[0].profile == "integrations"
    assert assertions[0].object == "Beta EHR"
    assert assertions[0].relation == "Integrates"
    messages = model.calls[0][1]
    assert "Acme Platform" in messages[0]["content"]  # seeded candidates
    assert "Acme Platform integrates with Beta EHR." in messages[1]["content"]  # source content


@pytest.mark.asyncio
async def test_customers_extractor_builds_prompt_from_braintrust(monkeypatch):
    """`customers` loads its prompt from Braintrust (stubbed in conftest.py) instead of
    the hardcoded instructions, passing seeded candidates and source content through."""
    model = _RecordingModel()
    model.outputs["CustomersResult"] = {
        "relationships": [
            {
                "health_system": "Mercy Health",
                "product": "Acme Platform",
                "quote": "Mercy Health uses Acme Platform",
            }
        ]
    }
    source = _build_source("Mercy Health uses Acme Platform.")

    assertions = await LLMAssertionExtractor(model).extract(
        source,
        ["customers"],
        [
            SeededEntity(label="Product", name="Acme Platform", entity_uuid="product-1"),
            SeededEntity(label="HealthSystem", name="Mercy Health", entity_uuid="system-1"),
        ],
    )

    assert assertions[0].subject == "Mercy Health"
    assert assertions[0].profile == "customers"
    assert assertions[0].object == "Acme Platform"
    assert assertions[0].relation == "Adopts"
    messages = model.calls[0][1]
    assert "Acme Platform" in messages[0]["content"]  # seeded candidates
    assert "Mercy Health uses Acme Platform." in messages[1]["content"]  # source content
