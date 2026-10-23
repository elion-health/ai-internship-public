import os

import pytest

from ai_internship.discovery.providers import FirecrawlDiscoveryProvider

pytestmark = pytest.mark.e2e


@pytest.fixture
def provider():
    if not os.getenv("FIRECRAWL_API_KEY"):
        pytest.skip("FIRECRAWL_API_KEY is required")
    return FirecrawlDiscoveryProvider(api_key=os.environ["FIRECRAWL_API_KEY"])


def test_squad_testimonial_carousel_exposes_all_cards(provider):
    page = provider.scrape("https://www.squadhealth.ai/", wait_for_ms=1000)

    result = provider.expand_interactive_evidence(page.url, page.raw_content, limit=5)

    assert len({item.content for item in result.items}) == 4
    assert {item.control_label for item in result.items} >= {
        "Show testimonial 2 of 5",
        "Show testimonial 5 of 5",
    }


def test_heidi_logo_strip_without_evidence_context_is_excluded(provider):
    page = provider.scrape("https://www.heidihealth.com/fr-ca", wait_for_ms=1000)

    result = provider.expand_interactive_evidence(page.url, page.raw_content, limit=5)

    assert result.logos == []


def test_heidi_customer_load_more_exposes_post_initial_evidence(provider):
    page = provider.scrape("https://www.heidihealth.com/en-ca/customers", wait_for_ms=1000)

    result = provider.expand_interactive_evidence(page.url, page.raw_content, limit=5)

    assert 1 <= len(result.items) <= 5
    assert "Load more articles" in {item.control_label for item in result.items}
    assert any("Connect2Care" in item.content for item in result.items)
