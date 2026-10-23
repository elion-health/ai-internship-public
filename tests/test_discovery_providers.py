from types import SimpleNamespace
from urllib.parse import quote

import firecrawl

from ai_internship.discovery.providers import FirecrawlDiscoveryProvider


class RecordingClient:
    def __init__(self, responses=()):
        self.responses = iter(responses)
        self.calls = []

    def scrape(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)

    def map(self, url, *, limit):
        self.calls.append((url, {"limit": limit}))
        return SimpleNamespace(links=["https://example.com/customers"])


def response(markdown, *, raw_html=None):
    return SimpleNamespace(
        markdown=markdown,
        raw_html=raw_html,
        metadata={"title": "Example"},
    )


def test_firecrawl_provider_uses_current_sdk_surface():
    client = RecordingClient([response("Example", raw_html="<main>Example</main>")])
    provider = FirecrawlDiscoveryProvider(client=client)

    page = provider.scrape("https://example.com", wait_for_ms=250)

    assert page.content == "Example"
    assert page.raw_content == "<main>Example</main>"
    assert client.calls[0] == (
        "https://example.com",
        {
            "formats": ["markdown", "rawHtml"],
            "only_main_content": True,
            "only_clean_content": True,
            "wait_for": 250,
        },
    )
    assert provider.map_site("https://example.com", 10) == ["https://example.com/customers"]
    assert client.calls[1] == ("https://example.com", {"limit": 10})


def test_firecrawl_provider_constructs_current_client_with_timeout(monkeypatch):
    captured = {}

    class Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(firecrawl, "FirecrawlApp", Client)

    FirecrawlDiscoveryProvider(api_key="key", timeout=12)

    assert captured == {"api_key": "key", "timeout": 12}


def test_firecrawl_provider_bounds_the_overall_crawl_job_separately():
    # PR #73 review (Macroscope, High): crawl()'s own `timeout` kwarg bounds
    # the whole job (Firecrawl polls until it finishes or this elapses); the
    # client-construction `timeout` above only bounds individual HTTP
    # requests and never reaches a stuck crawl job without this.
    class Client:
        def __init__(self):
            self.calls = []

        def crawl(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return SimpleNamespace(data=[])

    client = Client()
    provider = FirecrawlDiscoveryProvider(client=client, crawl_timeout=600)

    provider.crawl("https://example.com", max_depth=2, limit=10)

    assert client.calls[0][1]["timeout"] == 600


def test_firecrawl_provider_does_not_call_crawl_with_a_non_positive_limit():
    # PR #73 review (Macroscope, Medium), verified against the Firecrawl
    # SDK's request validation: it raises for limit <= 0 instead of being a
    # no-op, and DiscoverySession.crawl_site can legitimately compute 0
    # once its page cap is already reached.
    class Client:
        def crawl(self, url, **kwargs):
            raise AssertionError("must not call the provider with a non-positive limit")

    provider = FirecrawlDiscoveryProvider(client=Client())

    assert provider.crawl("https://example.com", max_depth=1, limit=0) == []


def test_interactive_expansion_collects_distinct_states_and_repeated_logo_alts():
    provider = FirecrawlDiscoveryProvider(client=RecordingClient())
    raw_html = """
        <section><h2>Customer testimonials</h2>
          <button aria-label="Show testimonial 1 of 3">1</button>
          <img alt="AllerVie Health" src="allervie.svg">
          <img alt="AllerVie Health" src="allervie-copy.svg">
          <img alt="Beacon Clinic" src="beacon.svg">
          <img alt="Beacon Clinic" src="beacon-copy.svg">
        </section>
    """
    provider._interactive_states = lambda url, limit: [
        {
            "control_type": "button",
            "control_label": "Show testimonial 1 of 3",
            "content": "AllerVie Health\nSquad helps patients.",
            "attribution": "AllerVie Health",
            "context": "Customer testimonials",
        },
        {
            "control_type": "button",
            "control_label": "Show testimonial 2 of 3",
            "content": "Beacon Clinic\nSquad helps patients.",
            "attribution": "Beacon Clinic",
            "context": "Customer testimonials",
        },
        {
            "control_type": "button",
            "control_label": "Show testimonial 3 of 3",
            "content": "AllerVie Health\nSquad helps patients.",
            "attribution": "AllerVie Health",
            "context": "Customer testimonials",
        },
        {
            "control_type": "button",
            "control_label": "Show testimonial 4 of 4",
            "content": "Care Clinic\nSquad helps patients.",
            "attribution": "Care Clinic",
            "context": "Customer testimonials",
        },
    ]

    result = provider.expand_interactive_evidence("https://example.com", raw_html, limit=5)

    assert [item.content for item in result.items] == [
        "AllerVie Health\nSquad helps patients.",
        "Beacon Clinic\nSquad helps patients.",
        "Care Clinic\nSquad helps patients.",
    ]
    assert [item.state for item in result.items] == [0, 1, 3]
    assert [logo.alt_text for logo in result.logos] == ["AllerVie Health", "Beacon Clinic"]
    assert [logo.image_url for logo in result.logos] == ["allervie.svg", "beacon.svg"]
    assert all("Customer testimonials" in logo.context for logo in result.logos)
    assert [item.attribution for item in result.items] == [
        "AllerVie Health",
        "Beacon Clinic",
        "Care Clinic",
    ]


def test_interactive_expansion_ignores_valueless_and_non_evidence_logos():
    provider = FirecrawlDiscoveryProvider(client=RecordingClient())

    result = provider.expand_interactive_evidence(
        "https://example.com",
        """
        <h2>Site header</h2><img alt src>
        <img alt="Example" src="one.svg"><img alt="Example" src="two.svg">
        <h2>Customer logos</h2>
        <img alt="AllerVie" src="allervie.svg"><img alt="AllerVie" src="allervie-copy.svg">
        """,
        limit=5,
    )

    assert [logo.alt_text for logo in result.logos] == ["AllerVie"]


def test_interactive_expansion_does_not_carry_logo_context_into_footer():
    provider = FirecrawlDiscoveryProvider(client=RecordingClient())

    result = provider.expand_interactive_evidence(
        "https://example.com",
        """
        <section><h2>Customer logos</h2><img alt="AllerVie" src="one.svg"></section>
        <footer><img alt="AllerVie" src="two.svg"></footer>
        """,
        limit=5,
    )

    assert [(logo.image_url, logo.context) for logo in result.logos] == [
        ("one.svg", "Customer logos")
    ]


def test_interactive_expansion_skips_pages_without_evidence_cues():
    client = RecordingClient()
    provider = FirecrawlDiscoveryProvider(client=client)

    result = provider.expand_interactive_evidence(
        "https://example.com", "<main><button>Next feature</button></main>", limit=5
    )

    assert result.items == []
    assert result.logos == []
    assert client.calls == []


def test_interactive_expansion_uses_rendered_evidence_when_raw_html_has_no_cue():
    provider = FirecrawlDiscoveryProvider(client=RecordingClient())
    provider._interactive_states = lambda url, limit: [
        {
            "control_type": "button",
            "control_label": "Show customer",
            "content": "Customer story",
            "attribution": "",
            "context": "Customers",
        }
    ]

    result = provider.expand_interactive_evidence(
        "https://example.com", "<main><div id='app'></div></main>", limit=5
    )

    assert [item.content for item in result.items] == ["Customer story"]


def test_interactive_expansion_keeps_single_logo_in_evidence_context():
    provider = FirecrawlDiscoveryProvider(client=RecordingClient())

    result = provider.expand_interactive_evidence(
        "https://example.com",
        '<section><h2>Customer logos</h2><img alt="AllerVie" src="allervie.svg"></section>',
        limit=5,
    )

    assert [logo.alt_text for logo in result.logos] == ["AllerVie"]


def test_interactive_expansion_ignores_repeated_logo_strips_without_evidence_context():
    provider = FirecrawlDiscoveryProvider(client=RecordingClient())

    result = provider.expand_interactive_evidence(
        "https://example.com",
        '<img alt="Bupa" src="bupa.svg"><img alt="Bupa" src="bupa-copy.svg">',
        limit=5,
    )

    assert result.logos == []


def test_interactive_states_keep_evidence_controls_after_dom_replacement():
    provider = FirecrawlDiscoveryProvider(client=RecordingClient())
    html = """
        <section id="customers">
          <h2>Stories</h2>
          <div id="customer-panel">Initial customer</div>
          <button role="tab" aria-controls="customer-panel" aria-label="Show testimonial AllerVie"
            onclick="this.closest('section').outerHTML = `
              <section id='customers'><h2>Stories</h2>
              <div id='customer-panel'>AllerVie chose ExampleTeam.</div>
              <button role='tab' aria-controls='customer-panel'
                aria-label='Show testimonial AllerVie'>AllerVie</button>
              </section>`">AllerVie</button>
        </section>
        <section>
          <h2>Latest product news</h2>
          <div id="blog-content">Initial blog</div>
          <button aria-label="Load more articles"
            onclick="document.getElementById('blog-content').innerText = 'More blog'">
            Load more articles</button>
        </section>
    """

    states = provider._interactive_states(f"data:text/html,{quote(html)}", limit=3)

    assert [state["content"] for state in states] == ["AllerVie chose ExampleTeam."]
    assert states[0]["control_label"] == "Show testimonial AllerVie"
