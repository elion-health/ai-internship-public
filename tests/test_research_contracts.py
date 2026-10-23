import pytest
from pydantic import ValidationError

from ai_internship.discovery.contracts import canonicalize_url, technical_exclusion_reason
from ai_internship.research.contracts import ResearchMode, ResearchRequest, ResearchTarget


def test_equivalent_target_urls_share_identity():
    first = ResearchTarget.build("Fathom", "HTTPS://FathomHealth.com/#customers")
    second = ResearchTarget.build("Another name", "https://fathomhealth.com/")

    assert first.target_id == second.target_id
    assert first.canonical_url == "https://fathomhealth.com/"


def test_canonicalization_removes_tracking_but_preserves_meaningful_query():
    assert (
        canonicalize_url("https://EXAMPLE.com/a/?utm_source=x&category=customers")
        == "https://example.com/a?category=customers"
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.example.com/a", "https://example.com/a"),
        ("https://www.wwe.com/a", "https://wwe.com/a"),
        ("https://www.w.com/a", "https://w.com/a"),
    ],
)
def test_canonicalize_url_removes_only_the_www_prefix(url, expected):
    assert canonicalize_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/logo.svg",
        "https://example.com/wp-admin/login",
        "https://example.com/calendar?year=2099&month=01",
        "https://developer.suki.ai/api-reference/send-notifications",
        "https://developer.imohealth.com/solution-accelerators",
        "https://booking.example/resource-sitemap.xml",
        "https://booking.example/page-sitemap.xml",
        "https://booking.example/post-sitemap.xml",
        "https://network.example/news-sitemap.xml",
        "https://platform.example/sitemap-resources.xml",
        "https://suki.ai/sitemap.xml",
        "https://example.com/robots.txt",
        # Macroscope review, PR #61: gzip-compressed sitemaps are a standard
        # part of the sitemap protocol (sitemaps.org), not an edge case.
        "https://example.com/sitemap1.xml.gz",
    ],
)
def test_technical_exclusions_have_reasons(url):
    assert technical_exclusion_reason(url)


def test_developer_subdomain_exclusion_does_not_fire_on_main_domain():
    """Regression: a real customer/integration page on the vendor's main domain
    must not be excluded just because it mentions developers or has "developer"
    somewhere in the path -- only the developer.* SUBDOMAIN is excluded."""
    assert (
        technical_exclusion_reason("https://suki.ai/blog/ambient-intelligence-for-engineers")
        is None
    )
    assert technical_exclusion_reason("https://suki.ai/careers/developer-relations") is None


def test_sitemap_exclusion_does_not_fire_on_a_real_page():
    """Regression: a real content page must not be excluded just because its
    path or slug happens to contain the substring "sitemap" or ends in some
    other ".xml"-adjacent pattern -- only an actual sitemap/robots file."""
    assert (
        technical_exclusion_reason("https://booking.example/blog/content_type/case-study") is None
    )
    assert (
        technical_exclusion_reason("https://booking.example/resources/sitemap-of-features") is None
    )


def test_force_profiles_must_be_requested():
    with pytest.raises(ValidationError):
        ResearchRequest(
            name="Fathom",
            url="https://fathomhealth.com",
            profiles=["integrations"],
            force_profiles=["customers"],
        )


def test_initial_mode_is_default():
    request = ResearchRequest(
        name="Fathom",
        url="https://fathomhealth.com",
        profiles=["customers"],
    )

    assert request.mode is ResearchMode.INITIAL


def test_first_party_only_defaults_to_false():
    request = ResearchRequest(
        name="Fathom",
        url="https://fathomhealth.com",
        profiles=["customers"],
    )

    assert request.first_party_only is False


@pytest.mark.parametrize("profile", ["", "unknown"])
def test_profiles_must_be_registered(profile):
    with pytest.raises(ValidationError, match="unknown profiles"):
        ResearchRequest(
            name="Fathom",
            url="https://fathomhealth.com",
            profiles=[profile],
        )


def test_profiles_must_be_unique():
    with pytest.raises(ValidationError, match="profiles must be unique"):
        ResearchRequest(
            name="Fathom",
            url="https://fathomhealth.com",
            profiles=["customers", "customers"],
        )
