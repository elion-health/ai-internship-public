import pytest

from ai_internship.enrichment import grounding
from ai_internship.enrichment.contracts import Assertion
from ai_internship.enrichment.grounding import (
    content_may_support_profile,
    full_rejection_reason,
    relationship_is_grounded,
    semantic_rejection_reason,
)


def assertion(**overrides):
    values = {
        "subject": "Medica",
        "subject_type": "HealthSystem",
        "relation": "Adopts",
        "object": "Ema",
        "object_type": "Product",
        "quote": "Medica adopts Ema.",
        "profile": "customers",
    }
    values.update(overrides)
    return Assertion(**values)


def test_relationship_rejects_fabricated_abstention_quote():
    """Observed live: instead of returning an empty relationships list, the
    extractor sometimes abstains by fabricating a relationship whose quote is
    its own refusal, borrowing a seed name for the required subject/object
    fields. That quote text never appears on the real page, so it fails on
    its own account -- caught explicitly here rather than relying on the
    near-verbatim similarity score happening to stay low."""
    item = assertion(
        quote="No relevant HealthSystem -> Adopts -> Product relationship is supported here."
    )

    assert (
        relationship_is_grounded(item, "completely unrelated page content", profile="customers")
        is False
    )


def test_relationship_meta_commentary_check_does_not_reject_real_evidence():
    """Regression for a false-positive caught by automated review on PR #19:
    the original META_COMMENTARY_RE also matched standalone phrases like "not
    supported", which can appear in real grounded evidence describing
    something unrelated to the extractor abstaining."""
    content = "Medica uses Ema. Legacy fax integration is not supported on the new platform."
    item = assertion(quote="Medica uses Ema. Legacy fax integration is not supported")

    assert relationship_is_grounded(item, content, profile="customers")


def test_relationship_requires_whole_endpoint_names():
    """ "Medica" isn't a whole-word match inside "Medical teams" -- used to be
    rejected by subject_not_in_evidence. ELI-1192 removed that check for the
    customers profile (relying on the semantic check instead, not exercised
    by relationship_is_grounded's deterministic-only check), so this is now
    accepted at this layer."""
    item = assertion(quote="Medical teams adopt Ema.")

    assert relationship_is_grounded(item, item.quote, profile="customers") is True


def test_customer_relationship_rejects_target_as_customer():
    item = assertion(subject="Fathom", quote="Fathom adopts Ema.")

    assert (
        relationship_is_grounded(item, item.quote, profile="customers", target_name="Fathom")
        is False
    )


def test_customer_relationship_requires_profile_edge_shape():
    item = assertion(subject_type="Product", quote="Medica adopts Ema.")

    assert relationship_is_grounded(item, item.quote, profile="customers") is False


def test_integration_relationship_rejects_self_edge():
    item = assertion(
        subject="Fathom",
        subject_type="Product",
        relation="Integrates",
        object="Fathom",
        object_type="Product",
        quote="Fathom integrates with Fathom.",
        profile="integrations",
    )

    assert relationship_is_grounded(item, item.quote, profile="integrations") is False


def test_integration_rejects_target_as_partner_by_compound_brand_name():
    """Synthetic example: no lexical overlap between object
    "ExampleModule" and target "ExampleCare", but the quote names one compound brand
    joining them directly ("ExampleCare Care-ExampleModule") with zero words between --
    the vendor naming itself as the object's owner."""
    item = assertion(
        subject="ExampleCare",
        subject_type="Product",
        relation="Integrates",
        object="ExampleModule",
        object_type="Product",
        quote=(
            "ExampleCare Care-ExampleModule is a scalable, flexible integration that brings "
            "together ExampleModule and GuidingCare, our full-spectrum care management "
            "platform, into one unified solution."
        ),
        profile="integrations",
    )

    assert (
        relationship_is_grounded(
            item, item.quote, profile="integrations", target_name="ExampleCare"
        )
        is False
    )


def test_integration_accepts_real_third_party_despite_linking_words():
    """Regression guard for the compound-brand check: "X and Y partnered" / "X
    integrates with Y" name two distinct companies with a linking word between them
    -- must not be mistaken for a compound brand (zero words between)."""
    item = assertion(
        subject="Infermedica",
        subject_type="Product",
        relation="Integrates",
        object="Talkie.ai",
        object_type="Product",
        quote="Infermedica and Talkie.ai partnered up and integrated their solutions.",
        profile="integrations",
    )

    assert relationship_is_grounded(
        item, item.quote, profile="integrations", target_name="Infermedica"
    )


def test_compound_brand_check_requires_whole_word_match_not_prefix_or_substring():
    """Caught by review: prefix/substring matching (word.startswith, `in`) can fire on
    unrelated words that merely share letters -- "Healthgrades" starts with "Health",
    "acmeenter" contains "acme". Must not reject a real third-party integration just
    because of that coincidence."""
    item = assertion(
        subject="Health Catalyst",
        subject_type="Product",
        relation="Integrates",
        object="Acme",
        object_type="Product",
        quote="Healthgrades acmeenter of care coordination drove growth.",
        profile="integrations",
    )

    assert relationship_is_grounded(
        item, item.quote, profile="integrations", target_name="Health Catalyst"
    )


def test_compound_brand_check_does_not_crash_on_whitespace_only_names():
    """Caught by review: target_name/object_name that's truthy but whitespace-only
    normalizes to an empty string, and indexing split()[0] on that raised IndexError
    instead of returning False."""
    item = assertion(
        subject="ExampleCare",
        subject_type="Product",
        relation="Integrates",
        object="ExampleModule",
        object_type="Product",
        quote="ExampleCare Care-ExampleModule brings together ExampleModule and GuidingCare.",
        profile="integrations",
    )

    # Must not raise -- whitespace-only target_name should behave like "no
    # target_name" (no compound-brand check applied), not crash.
    reason = grounding.grounding_rejection_reason(
        item, item.quote, profile="integrations", target_name=" "
    )

    assert reason in (None, "target_as_integration_partner")


def test_compound_brand_check_does_not_fire_across_sentence_punctuation():
    """Caught by review (Macroscope + Codex, independently): target_re.search() with
    word boundaries still matched a target immediately followed by punctuation
    ("examplecare.", "acme:"), since (?!\\w) is satisfied by punctuation too. That
    treats a sentence/label boundary as zero-words-between compound-brand adjacency
    and wrongly rejects two distinct statements naming two different companies."""
    item = assertion(
        subject="ExampleCare",
        subject_type="Product",
        relation="Integrates",
        object="Acme",
        object_type="Product",
        quote="Supported by ExampleCare. Acme integrates through our API.",
        profile="integrations",
    )

    assert relationship_is_grounded(
        item, item.quote, profile="integrations", target_name="ExampleCare"
    )


def test_compound_brand_check_does_not_fire_across_object_side_punctuation():
    """The object side had the same bug as the target-side fix above, just not
    caught yet: (?<!\\w)/(?!\\w) treats "Acme:" or "Acme," as a boundary match for
    object "Acme", so a target immediately followed by a punctuated object word --
    e.g. a heading "ExampleCare" followed by an unrelated "Acme:" label -- read as
    compound-brand adjacency. The hyphen case ("Care-ExampleModule") this check exists
    for must still work; only non-hyphen punctuation must stop counting as a
    boundary."""
    item = assertion(
        subject="ExampleCare",
        subject_type="Product",
        relation="Integrates",
        object="Acme",
        object_type="Product",
        quote="ExampleCare Acme: integrates with our platform.",
        profile="integrations",
    )

    assert relationship_is_grounded(
        item, item.quote, profile="integrations", target_name="ExampleCare"
    )


def test_compound_brand_check_does_not_fire_on_a_hyphenated_linking_phrase():
    """Macroscope review: the hyphen-boundary fix above (allowing "Care-ExampleModule")
    was too loose -- "connects-to-Acme" also ends in "-Acme" after a hyphen, but
    names an ACTION between two words ("connects to"), not a single compound brand.
    Only a single hyphen segment before the object may count; "connects-to-acme" has
    two ("connects", "to")."""
    item = assertion(
        subject="ExampleCare",
        subject_type="Product",
        relation="Integrates",
        object="Acme",
        object_type="Product",
        quote="ExampleCare connects-to-Acme for seamless data exchange.",
        profile="integrations",
    )

    assert relationship_is_grounded(
        item, item.quote, profile="integrations", target_name="ExampleCare"
    )


def test_compound_brand_check_requires_the_full_multi_word_target_name():
    """Macroscope review: comparing only the first word of a multi-word target name
    let the target's OWN name trigger a false match against an unrelated object --
    target "Acme Care Portal" naming itself in the quote satisfies a first-word-only
    check against an object "Care ExampleModule" it has nothing to do with, since the
    target's own second word ("Care") happens to equal the object's first word."""
    item = assertion(
        subject="Acme Care Portal",
        subject_type="Product",
        relation="Integrates",
        object="Care ExampleModule",
        object_type="Product",
        quote="Acme Care Portal launched a new dashboard for its customers today.",
        profile="integrations",
    )

    assert relationship_is_grounded(
        item, item.quote, profile="integrations", target_name="Acme Care Portal"
    )


def test_customer_quote_matches_visible_markdown_text():
    content = (
        "**HUDSON, N.Y.** – [UTMHealthcare](https://utm.example), a health technology company, "
        "announced a partnership with [FirstHx](https://firsthx.example)."
    )
    item = assertion(
        subject="UTMHealthcare",
        object="FirstHx",
        quote=("UTMHealthcare, a health technology company, announced a partnership with FirstHx"),
    )

    assert relationship_is_grounded(item, content, profile="customers", target_name="FirstHx")


def test_integration_can_use_target_page_context_for_target_endpoint():
    content = "# FirstHx\n\nDirect integration with Acme, Cerner, Accuro, and other EMRs."
    item = assertion(
        subject="FirstHx",
        subject_type="Product",
        relation="Integrates",
        object="Acme",
        quote="Direct integration with Acme, Cerner, Accuro, and other EMRs",
        profile="integrations",
    )

    assert relationship_is_grounded(item, content, profile="integrations", target_name="FirstHx")


def test_customer_can_use_nearby_heading_for_customer_endpoint():
    content = (
        "# The Weight Clinic\n\nDr. Hala Kufaishi wanted to reduce wait times. "
        "That is when she implemented FirstHx for patient intake."
    )
    item = assertion(
        subject="The Weight Clinic",
        object="FirstHx",
        quote="That is when she implemented FirstHx for patient intake",
    )

    assert relationship_is_grounded(item, content, profile="customers", target_name="FirstHx")


def test_customer_rejects_subject_named_only_in_hyperlink_url():
    """Synthetic URL-only example: the
    subject's name never appears anywhere in the page's visible text, only inside
    a hyperlink's target URL ("[one director notes](.../example-clinic-28-.../)").
    normalize_text already strips a markdown link down to its visible anchor text
    ("one director notes"), dropping the URL entirely -- so the name genuinely
    isn't backed by anything a reader can see on this page."""
    content = (
        "### Enhance Patient Payment Options\n\n"
        "As [one director notes]"
        "(https://www.booking.example/resources/example-clinic-28-increases-upfront-payments-reduces-costs-with-kiosks/),\n\n"
        '> "Our fictional clinic now collects payments through an automated form."'
    )
    item = assertion(
        subject="Example Clinic 28",
        object="ExampleBooking",
        quote=("Our fictional clinic now collects payments through an automated form."),
    )

    assert (
        grounding.grounding_rejection_reason(
            item, content, profile="customers", target_name="ExampleBooking"
        )
        == "subject_named_only_in_url"
    )


def test_customer_accepts_subject_named_in_visible_link_text():
    """Regression: a name genuinely present in the page's visible anchor text (not
    just a URL) must not be rejected. This fixture is synthetic."""
    content = (
        "[Example Community Clinic]"
        "(https://www.booking.example/resources/"
        "50-reactivation-rate-96-kiosk-adoption-for-example-community-clinic/) "
        'has seen this firsthand:\n\n> "The adoption of pre-registration in advance is critical."'
    )
    item = assertion(
        subject="Example Community Clinic",
        object="ExampleBooking",
        quote="The adoption of pre-registration in advance is critical.",
    )

    assert (
        grounding.grounding_rejection_reason(
            item, content, profile="customers", target_name="ExampleBooking"
        )
        is None
    )


def test_integration_rejects_object_named_only_in_hyperlink_url():
    """Integrations-side mirror of the customers-side check above -- the free-text
    endpoint for Integrates is the object, not the subject."""
    content = (
        "### Partner Spotlight\n\n"
        "As [one platform notes](https://vendor.example/resources/example-payments-integration-story/),\n\n"
        '> "Send accurate patient estimations with the push of a button."'
    )
    item = assertion(
        subject="ExampleBooking",
        subject_type="Product",
        relation="Integrates",
        object="Example Payments",
        object_type="Product",
        quote="Send accurate patient estimations with the push of a button.",
        profile="integrations",
    )

    assert (
        grounding.grounding_rejection_reason(
            item, content, profile="integrations", target_name="ExampleBooking"
        )
        == "object_named_only_in_url"
    )


def test_integration_accepts_object_named_in_visible_text():
    """Regression: a real integration partner named directly in visible text (not
    just a URL) must not be rejected."""
    content = (
        "[Example Payments](https://vendor.example/resources/example-payments-integration-story/) "
        'powers this feature:\n\n> "Send accurate patient estimations with the push of a button."'
    )
    item = assertion(
        subject="ExampleBooking",
        subject_type="Product",
        relation="Integrates",
        object="Example Payments",
        object_type="Product",
        quote="Send accurate patient estimations with the push of a button.",
        profile="integrations",
    )

    assert (
        grounding.grounding_rejection_reason(
            item, content, profile="integrations", target_name="ExampleBooking"
        )
        is None
    )


def test_customer_rejects_subject_named_only_in_underscore_slug():
    """Macroscope review, PR #55: a slug using underscores instead of hyphens
    ("marietta_eye_case_study") wasn't detected at all -- `_contains_endpoint`'s
    whole-word regex treats `_` as a word character, so the URL-derived name
    failed its own boundary check and this fabricated case slipped through as
    accepted."""
    content = (
        "As [one director notes](https://example.com/marietta_eye_case_study/),\n\n"
        '> "The new kiosks made a real difference for our patients."'
    )
    item = assertion(
        subject="Marietta Eye",
        object="ExampleBooking",
        quote="The new kiosks made a real difference for our patients.",
    )

    assert (
        grounding.grounding_rejection_reason(
            item, content, profile="customers", target_name="ExampleBooking"
        )
        == "subject_named_only_in_url"
    )


def test_customer_rejects_subject_named_only_in_url_with_parens():
    """Macroscope review, PR #55: normalize_text's link-stripping regex stopped
    at the first ")", so a URL containing one (a Wikipedia-style
    ".../Marietta_(Georgia)") only had its link syntax partly stripped, leaking
    the rest of the URL as literal visible text and defeating this check."""
    content = (
        "As [one director notes]"
        "(https://en.wikipedia.org/wiki/Marietta_Eye_(Georgia)),\n\n"
        '> "The new kiosks made a real difference for our patients."'
    )
    item = assertion(
        subject="Marietta Eye",
        object="ExampleBooking",
        quote="The new kiosks made a real difference for our patients.",
    )

    assert (
        grounding.grounding_rejection_reason(
            item, content, profile="customers", target_name="ExampleBooking"
        )
        == "subject_named_only_in_url"
    )


def test_customer_accepts_shortened_visible_anchor_even_with_a_fuller_slug():
    """Codex review, PR #55: a real, shortened anchor text ("Rush") whose own
    href happens to spell out the fuller name ("/rush-university-medical-
    center") must not be rejected -- the anchor genuinely shares a token with
    the entity, unlike the Example Clinic 28 case where the anchor shares
    nothing with it at all."""
    content = (
        "[Rush](https://example.com/rush-university-medical-center) uses Suki.\n\n"
        '> "The new workflow saved our clinicians real time."'
    )
    item = assertion(
        subject="Rush University Medical Center",
        object="Suki",
        quote="The new workflow saved our clinicians real time.",
    )

    assert (
        grounding.grounding_rejection_reason(item, content, profile="customers", target_name="Suki")
        is None
    )


def test_customer_accepts_shortened_mention_despite_unrelated_link_slugs():
    """Macroscope review, PR #55: a real abbreviated mention ("Rush" for "Rush
    University Medical Center") must not get combined with OTHER, unrelated
    links elsewhere on the page whose slugs happen to contain the entity's
    other words ("university", "medical", "center") -- checking one link's own
    anchor+href at a time means unrelated links never get combined into a
    false match."""
    content = (
        "Rush uses Suki.\n\n"
        "See our [university partnerships](https://example.com/university) and "
        "[medical center jobs](https://example.com/medical-center-careers).\n\n"
        '> "The new workflow saved our clinicians real time."'
    )
    item = assertion(
        subject="Rush University Medical Center",
        object="Suki",
        quote="The new workflow saved our clinicians real time.",
    )

    assert (
        grounding.grounding_rejection_reason(item, content, profile="customers", target_name="Suki")
        is None
    )


def test_integration_rejects_generic_endpoint():
    item = assertion(
        subject="FirstHx",
        subject_type="Product",
        relation="Integrates",
        object="electronic medical record systems",
        quote="FirstHx integrates with electronic medical record systems",
        profile="integrations",
    )

    assert not relationship_is_grounded(
        item, item.quote, profile="integrations", target_name="FirstHx"
    )


def test_relationship_requires_research_target_endpoint():
    item = assertion(
        subject="ExampleConnector",
        subject_type="Product",
        relation="Integrates",
        object="ExampleEHR",
        object_type="Product",
        quote="ExampleConnector integrates with ExampleEHR",
        profile="integrations",
    )
    content = f"# FirstHx\n\n{item.quote}."

    assert not relationship_is_grounded(
        item, content, profile="integrations", target_name="FirstHx"
    )


def test_target_endpoint_cannot_be_grounded_from_another_page_section():
    """The object ("FirstHx") only appears in a different markdown section
    than the quote -- used to be rejected by object_not_in_evidence. ELI-1192
    removed that check for the customers profile (product identity is now
    fixed via the extraction prompt instead of verified by string-matching
    here), so this is now accepted at this layer. target_not_endpoint (a
    different check, kept for all profiles) still passes since the object
    literally is the target."""
    content = (
        "# FirstHx\n\nFirstHx product page.\n\n# Customers\n\nMercy uses a patient-intake product."
    )
    item = assertion(
        subject="Mercy",
        object="FirstHx",
        quote="Mercy uses a patient-intake product",
    )

    assert relationship_is_grounded(item, content, profile="customers", target_name="FirstHx")


def test_integration_rejects_singular_generic_endpoint():
    item = assertion(
        subject="FirstHx",
        subject_type="Product",
        relation="Integrates",
        object="electronic medical records (EMR) system",
        object_type="Product",
        quote="FirstHx integrates with an electronic medical records (EMR) system",
        profile="integrations",
    )

    assert not relationship_is_grounded(
        item, item.quote, profile="integrations", target_name="FirstHx"
    )


def test_profile_gate_only_skips_content_without_named_target():
    assert content_may_support_profile(
        "Mercy chose FirstHx for patient intake", "customers", target_name="FirstHx"
    )
    assert not content_may_support_profile(
        "Mercy chose another product", "customers", target_name="FirstHx"
    )


def test_profile_gate_normalizes_target_punctuation():
    assert content_may_support_profile(
        "Mercy implemented Acme AI", "customers", target_name="Acme-AI"
    )


def test_relationship_context_does_not_cross_markdown_sections():
    """Subject "Clinic B" is only named in a different section than the quote
    (which is really about "Clinic A") -- used to be rejected by
    subject_not_in_evidence. ELI-1192 removed that check for the customers
    profile -- accepted at this deterministic layer now; catching a wrong
    subject like this is the semantic check's job going forward, not tested
    here (relationship_is_grounded is deterministic-only)."""
    content = "# Clinic A\n\nClinic A implemented FirstHx.\n\n# Clinic B\n\nUnrelated text."
    item = assertion(
        subject="Clinic B",
        object="FirstHx",
        quote="Clinic A implemented FirstHx",
    )

    assert relationship_is_grounded(item, content, profile="customers", target_name="FirstHx")


def test_relationship_context_does_not_cross_customer_cards():
    """Same idea as the test above, card-style layout instead of headings."""
    content = (
        "## Customers\n\n**Clinic A**\n\nClinic A implemented FirstHx.\n\n"
        "**Clinic B**\n\nUnrelated text."
    )
    item = assertion(
        subject="Clinic B",
        object="FirstHx",
        quote="Clinic A implemented FirstHx",
    )

    assert relationship_is_grounded(item, content, profile="customers", target_name="FirstHx")


def test_integration_accepts_lowercase_branded_endpoint():
    item = assertion(
        subject="FirstHx",
        subject_type="Product",
        relation="Integrates",
        object="athenahealth",
        object_type="Product",
        quote="FirstHx syncs with athenahealth",
        profile="integrations",
    )

    assert relationship_is_grounded(
        item,
        item.quote,
        profile="integrations",
        target_name="FirstHx",
    )


def test_customer_rejects_individual_as_health_system():
    """Confirmed live (ELI-1147): a person's name grounds and cues correctly,
    but a clinician isn't a HealthSystem -- the extraction prompt was updated
    to say so and the model still ignored it, so this needs a hard check."""
    for subject in ("Dr. Hala Kufaishi", "Dr. Johannes Piek", "Sarah Lee, MD"):
        item = assertion(
            subject=subject,
            quote=f"{subject} has implemented FirstHx in her practice.",
        )

        assert not relationship_is_grounded(
            item, item.quote, profile="customers", target_name="FirstHx"
        )


def test_customer_accepts_org_name_that_is_not_an_individual():
    item = assertion(
        subject="Alabama Oncology",
        object="FirstHx",
        quote="Alabama Oncology has selected FirstHx.",
    )

    assert relationship_is_grounded(item, item.quote, profile="customers", target_name="FirstHx")


def test_customer_accepts_a_company_name_that_starts_with_a_title():
    """Caught by review (Macroscope): a real company can legitimately start with
    a title -- "Dr. Reddy's Laboratories" is a real pharmaceutical company, not
    a clinician, and PERSON_TITLE_RE alone can't tell the two apart."""
    for subject in ("Dr. Reddy's Laboratories", "Dr. Oetker Pharmaceuticals"):
        item = assertion(
            subject=subject,
            object="FirstHx",
            quote=f"{subject} has selected FirstHx.",
        )

        assert relationship_is_grounded(
            item, item.quote, profile="customers", target_name="FirstHx"
        )


def test_relationship_rejects_generic_endpoint_phrases():
    for endpoint in ("Hospital", "The Hospital", "Patient Intake Platform"):
        item = assertion(
            subject=endpoint,
            object="FirstHx",
            quote=f"{endpoint} uses FirstHx",
        )

        assert not relationship_is_grounded(
            item, item.quote, profile="customers", target_name="FirstHx"
        )


class _FakeSemanticModel:
    """Fixed verdict on all three dimensions, independently settable so a test
    can isolate one without the others masking it."""

    def __init__(
        self,
        is_named_organization=False,
        relationship_actually_happened=True,
        relationship_claim_supported=True,
        is_third_party_vendor=True,
        is_customer_facing_integration=True,
        is_specific_named_entity=True,
        relationship_shape_matches_profile=True,
        subject_is_the_claimant=True,
    ):
        self.is_named_organization = is_named_organization
        self.relationship_actually_happened = relationship_actually_happened
        self.relationship_claim_supported = relationship_claim_supported
        self.is_third_party_vendor = is_third_party_vendor
        self.is_customer_facing_integration = is_customer_facing_integration
        self.is_specific_named_entity = is_specific_named_entity
        self.relationship_shape_matches_profile = relationship_shape_matches_profile
        self.subject_is_the_claimant = subject_is_the_claimant
        self.calls = 0

    def with_structured_output(self, schema, **kwargs):
        model = self

        class StructuredModel:
            async def ainvoke(self, messages):
                model.calls += 1
                return {
                    "is_named_organization": model.is_named_organization,
                    "relationship_actually_happened": model.relationship_actually_happened,
                    "relationship_claim_supported": model.relationship_claim_supported,
                    "is_third_party_vendor": model.is_third_party_vendor,
                    "is_customer_facing_integration": model.is_customer_facing_integration,
                    "is_specific_named_entity": model.is_specific_named_entity,
                    "relationship_shape_matches_profile": model.relationship_shape_matches_profile,
                    "subject_is_the_claimant": model.subject_is_the_claimant,
                }

        return StructuredModel()


class _RaisingModel:
    def with_structured_output(self, schema, **kwargs):
        class StructuredModel:
            async def ainvoke(self, messages):
                raise RuntimeError("simulated model failure")

        return StructuredModel()


@pytest.mark.asyncio
async def test_semantic_check_ignores_organization_verdict_for_integration_subjects():
    """Regression: is_named_organization only means something for a HealthSystem
    subject -- an integrations edge's subject is a Product, which the model
    correctly never calls an organization, so enforcing that field there would
    reject every valid integration (caught by review on this PR)."""
    item = assertion(
        subject="FirstHx",
        subject_type="Product",
        relation="Integrates",
        object="Acme",
        object_type="Product",
        quote="FirstHx integrates with Acme",
        profile="integrations",
    )

    assert await semantic_rejection_reason(item, chat_model=_FakeSemanticModel()) is None


@pytest.mark.asyncio
async def test_semantic_check_still_enforces_organization_verdict_for_health_system_subjects():
    item = assertion()

    reason = await semantic_rejection_reason(item, chat_model=_FakeSemanticModel())

    assert reason == "subject_not_real_organization"


@pytest.mark.asyncio
async def test_semantic_check_rejects_acquired_product_as_integration_partner():
    """Synthetic negative example with no lexical overlap between
    object and target -- the cheap word-overlap check can't catch it, so this is
    is_third_party_vendor's job."""
    item = assertion(
        subject="ExampleCare",
        subject_type="Product",
        relation="Integrates",
        object="ExampleModule",
        object_type="Product",
        quote="ExampleCare combines GuidingCare, ExampleModule, and Source in one platform.",
        profile="integrations",
    )

    reason = await semantic_rejection_reason(
        item, chat_model=_FakeSemanticModel(is_third_party_vendor=False)
    )

    assert reason == "integration_partner_not_third_party"


@pytest.mark.asyncio
async def test_semantic_check_rejects_pronoun_self_reference_as_integration_partner():
    """Synthetic pronoun example: is_third_party_vendor already
    covers named self-references (compound brands, possessive naming) but this one is
    referenced ONLY via first-person pronoun ("us/our") on the vendor's own domain, with
    no naming cue at all. Same field, same gating -- the fix here is a wording/prompt
    improvement (recognize "we/us/our" on the vendor's own page as equivalent to naming
    the vendor), not new code -- so this test documents the wiring already handles a
    False verdict correctly; the real fix is making the real model actually answer
    False for this shape."""
    item = assertion(
        subject="ExampleBooking",
        subject_type="Product",
        relation="Integrates",
        object="ExampleScheduling",
        object_type="Product",
        quote=("After acquiring ExampleScheduling, we included its calendar in our own platform."),
        profile="integrations",
    )

    reason = await semantic_rejection_reason(
        item, chat_model=_FakeSemanticModel(is_third_party_vendor=False)
    )

    assert reason == "integration_partner_not_third_party"


@pytest.mark.asyncio
async def test_semantic_check_rejects_own_brand_name_as_customer():
    """Synthetic own-brand example: the
    vendor's own parent brand or in-house feature name gets extracted as if it were a
    separate customer organization on the customers (Adopts) profile. is_named_organization
    already gates HealthSystem subjects (ELI-1147/1314) but its current wording only
    covers generic/anonymized placeholders, not "this specific name is the vendor's own
    brand." Same fix shape as the Integrates-side test above, mirrored for the
    customers-side subject."""
    item = assertion(
        subject="ExampleTerminologyCare",
        subject_type="HealthSystem",
        relation="Adopts",
        object="ExampleTerminology",
        object_type="Product",
        quote=("Our ExampleTerminologyCare brand now includes our revised terminology tools."),
        profile="customers",
    )

    reason = await semantic_rejection_reason(
        item, chat_model=_FakeSemanticModel(is_named_organization=False)
    )

    assert reason == "subject_not_real_organization"


@pytest.mark.asyncio
async def test_semantic_check_ignores_third_party_vendor_verdict_for_customers():
    """is_third_party_vendor only means something for Integrates -- a customers-
    profile (Adopts) assertion must not be rejected by it even if the model somehow
    answers false."""
    item = assertion()

    reason = await semantic_rejection_reason(
        item,
        chat_model=_FakeSemanticModel(is_named_organization=True, is_third_party_vendor=False),
    )

    assert reason is None


@pytest.mark.asyncio
async def test_semantic_check_rejects_backend_infrastructure_as_integration_partner():
    """ELI-1305: synthetic negative example (Suki/Google Cloud-style) -- the object is genuinely a
    separate, third-party company (so is_third_party_vendor alone wouldn't catch it),
    but the quote describes the vendor building its own product on top of the
    object as backend hosting/AI infrastructure, not a customer-facing product-to-
    product integration."""
    item = assertion(
        subject="Suki",
        subject_type="Product",
        relation="Integrates",
        object="Google Cloud",
        object_type="Product",
        quote=(
            "Suki Assistant, in collaboration with Google Cloud. To further support "
            "clinicians in providing the best care possible, Suki Assistant will "
            "leverage Google Cloud’s Vertex AI platform to expand its assistive "
            "capabilities."
        ),
        profile="integrations",
    )

    reason = await semantic_rejection_reason(
        item, chat_model=_FakeSemanticModel(is_customer_facing_integration=False)
    )

    assert reason == "integration_partner_is_backend_infrastructure"


@pytest.mark.asyncio
async def test_semantic_check_rejects_ai_coding_tool_as_integration_partner():
    """ELI-1318: synthetic negative example (Suki/Cursor) -- the object is a genuinely separate,
    third-party company (Cursor), and the quote does describe a real, already-
    shipped MCP connection, so every other semantic field passes it. But the
    quote is Suki's own developer-facing blog explaining that developers write
    code in Cursor/VS Code/Claude Code against Suki's API -- Cursor is a
    coding tool a developer builds with, not a product a customer's clinical/
    operational workflow depends on. Same axis as the backend-infrastructure
    check above (vendor tooling to build/ship a product, not a customer-facing
    integration), extended to cover developer tools instead of just hosting/
    AI infrastructure."""
    item = assertion(
        subject="Suki",
        subject_type="Product",
        relation="Integrates",
        object="Cursor",
        object_type="Product",
        quote=(
            "Developers building with Suki increasingly work AI-first. Their "
            "default motion is to describe intent to a coding agent: in "
            "Cursor, VS Code, or Claude Code, and review the output. ... So "
            "we made Suki's platform native to it with MCP support."
        ),
        profile="integrations",
    )

    reason = await semantic_rejection_reason(
        item, chat_model=_FakeSemanticModel(is_customer_facing_integration=False)
    )

    assert reason == "integration_partner_is_backend_infrastructure"


@pytest.mark.asyncio
async def test_semantic_check_ignores_backend_infrastructure_verdict_for_customers():
    """is_customer_facing_integration only means something for Integrates -- a
    customers-profile (Adopts) assertion must not be rejected by it even if the
    model somehow answers false."""
    item = assertion()

    reason = await semantic_rejection_reason(
        item,
        chat_model=_FakeSemanticModel(
            is_named_organization=True, is_customer_facing_integration=False
        ),
    )

    assert reason is None


@pytest.mark.asyncio
async def test_semantic_check_rejects_generic_category_as_integration_partner():
    """Synthetic generic-product example: a generic product
    category, not a specific named product, extracted as an integration partner. The
    cheap checks and every other semantic field pass this (the quote is synthetic, the
    relationship is genuinely described), so this is is_specific_named_entity's job."""
    item = assertion(
        subject="ExampleBooking",
        subject_type="Product",
        relation="Integrates",
        object="practice management system",
        object_type="Product",
        quote=(
            "Step 5: The staff checks the patient into ExampleBooking, where they are "
            "automatically checked into the Practice Management System."
        ),
        profile="integrations",
    )

    reason = await semantic_rejection_reason(
        item, chat_model=_FakeSemanticModel(is_specific_named_entity=False)
    )

    assert reason == "integration_partner_not_specific_entity"


@pytest.mark.asyncio
async def test_semantic_check_ignores_specific_entity_verdict_for_customers():
    """is_specific_named_entity only means something for Integrates -- a
    customers-profile (Adopts) assertion must not be rejected by it even if the
    model somehow answers false (the customers-side equivalent is
    is_named_organization, checked separately)."""
    item = assertion()

    reason = await semantic_rejection_reason(
        item,
        chat_model=_FakeSemanticModel(is_named_organization=True, is_specific_named_entity=False),
    )

    assert reason is None


@pytest.mark.asyncio
async def test_semantic_check_rejects_adopting_customer_extracted_as_integration():
    """ELI-1315: synthetic negative example (ExampleBooking/"Example Practice") -- a named customer
    practice plainly adopting/implementing ExampleBooking for its own patients, extracted
    under Integrates instead of Adopts. Every other semantic field passes this (the
    quote is real, the organization is specific) -- this is
    relationship_shape_matches_profile's job: the quote's shape (org adopts vendor's
    product for its own use) contradicts the extracted relation (Integrates)."""
    item = assertion(
        subject="ExampleBooking",
        subject_type="Product",
        relation="Integrates",
        object="Example Practice",
        object_type="Product",
        quote=(
            "Example Practice offers online scheduling for their 26 "
            "providers through ExampleBooking."
        ),
        profile="integrations",
    )

    reason = await semantic_rejection_reason(
        item, chat_model=_FakeSemanticModel(relationship_shape_matches_profile=False)
    )

    assert reason == "relationship_shape_contradicts_profile"


@pytest.mark.asyncio
async def test_semantic_check_rejects_integration_product_extracted_as_customer():
    """Synthetic example of the mirror-image direction:
    a genuine independent EHR product described as a technical integration, extracted
    under Adopts as if it were a customer organization. Same field,
    relationship_shape_matches_profile, catches both directions."""
    item = assertion(
        subject="ExampleEHR",
        subject_type="HealthSystem",
        relation="Adopts",
        object="ExampleBooking",
        object_type="Product",
        quote="Ensure Seamless integration with ExampleEHR",
        profile="customers",
    )

    reason = await semantic_rejection_reason(
        item,
        chat_model=_FakeSemanticModel(
            is_named_organization=True, relationship_shape_matches_profile=False
        ),
    )

    assert reason == "relationship_shape_contradicts_profile"


@pytest.mark.asyncio
async def test_semantic_check_accepts_matching_shape_for_both_profiles():
    """Regression guard: relationship_shape_matches_profile must not reject a
    genuinely correct extraction on either profile."""
    integration_item = assertion(
        subject="FirstHx",
        subject_type="Product",
        relation="Integrates",
        object="Acme",
        object_type="Product",
        quote="FirstHx integrates with Acme",
        profile="integrations",
    )
    customer_item = assertion()

    assert (
        await semantic_rejection_reason(integration_item, chat_model=_FakeSemanticModel()) is None
    )
    assert (
        await semantic_rejection_reason(
            customer_item, chat_model=_FakeSemanticModel(is_named_organization=True)
        )
        is None
    )


class _VotingFakeSemanticModel:
    """Returns a different is_third_party_vendor answer on each successive call (all
    other fields fixed accept-friendly) -- for proving semantic_rejection_reason
    actually votes across multiple calls instead of trusting a single one."""

    def __init__(self, is_third_party_vendor_sequence):
        self._sequence = list(is_third_party_vendor_sequence)
        self.calls = 0

    def with_structured_output(self, schema, **kwargs):
        model = self

        class StructuredModel:
            async def ainvoke(self, messages):
                verdict = model._sequence[model.calls % len(model._sequence)]
                model.calls += 1
                return {
                    "is_named_organization": True,
                    "relationship_actually_happened": True,
                    "relationship_claim_supported": True,
                    "is_third_party_vendor": verdict,
                    "is_customer_facing_integration": True,
                    "is_specific_named_entity": True,
                    "relationship_shape_matches_profile": True,
                    "subject_is_the_claimant": True,
                }

        return StructuredModel()


@pytest.mark.asyncio
async def test_semantic_check_takes_majority_vote_across_multiple_calls():
    """ELI-1304 validation found real run-to-run flicker on a single semantic-check
    call, in both directions (missing a real same-vendor case, and wrongly rejecting
    a real third party). A 3-reject/2-accept split should reject; a 2-reject/3-accept
    split should accept -- the majority wins either way, not whichever call happened
    to run once."""
    item = assertion(
        subject="ExampleCare",
        subject_type="Product",
        relation="Integrates",
        object="ExampleModule",
        object_type="Product",
        quote="ExampleCare Care-ExampleModule brings together ExampleModule and GuidingCare.",
        profile="integrations",
    )

    # First element deliberately disagrees with the eventual majority in each case,
    # so this only passes if every call is actually consulted -- a naive
    # single-call implementation would get both of these backwards.
    reject_majority = await semantic_rejection_reason(
        item, chat_model=_VotingFakeSemanticModel([True, False, False, False, True])
    )
    accept_majority = await semantic_rejection_reason(
        item, chat_model=_VotingFakeSemanticModel([False, True, True, True, False])
    )

    assert reject_majority == "integration_partner_not_third_party"
    assert accept_majority is None


class _PartiallyFailingSemanticModel:
    """The first vote raises (a transient timeout/malformed-output error); the rest
    succeed with a fixed verdict. Used to prove the vote survives one bad call
    instead of failing the whole check open on a single flaky vote."""

    def __init__(self, is_third_party_vendor):
        self.is_third_party_vendor = is_third_party_vendor
        self.calls = 0

    def with_structured_output(self, schema, **kwargs):
        model = self

        class StructuredModel:
            async def ainvoke(self, messages):
                model.calls += 1
                if model.calls == 1:
                    raise RuntimeError("simulated transient model error")
                return {
                    "is_named_organization": True,
                    "relationship_actually_happened": True,
                    "relationship_claim_supported": True,
                    "is_third_party_vendor": model.is_third_party_vendor,
                    "is_customer_facing_integration": True,
                    "is_specific_named_entity": True,
                    "relationship_shape_matches_profile": True,
                    "subject_is_the_claimant": True,
                }

        return StructuredModel()


@pytest.mark.asyncio
async def test_semantic_check_survives_one_failed_vote_out_of_five():
    """Caught by review: gather()'s default return_exceptions=False means one flaky
    vote used to fail the whole check open (wasting the other 4 successful votes,
    and leaving orphaned in-flight tasks) instead of voting on whatever succeeded."""
    item = assertion(
        subject="ExampleCare",
        subject_type="Product",
        relation="Integrates",
        object="ExampleModule",
        object_type="Product",
        quote="ExampleCare Care-ExampleModule brings together ExampleModule and GuidingCare.",
        profile="integrations",
    )

    reason = await semantic_rejection_reason(
        item, chat_model=_PartiallyFailingSemanticModel(is_third_party_vendor=False)
    )

    assert reason == "integration_partner_not_third_party"


class _TiedAfterOneFailureSemanticModel:
    """The first vote raises, leaving 4 surviving votes that split 2-2 -- the case
    SEMANTIC_CHECK_VOTES=5 being odd was supposed to rule out."""

    def __init__(self, is_third_party_vendor_votes):
        self._votes = iter(is_third_party_vendor_votes)
        self.calls = 0

    def with_structured_output(self, schema, **kwargs):
        model = self

        class StructuredModel:
            async def ainvoke(self, messages):
                model.calls += 1
                if model.calls == 1:
                    raise RuntimeError("simulated transient model error")
                return {
                    "is_named_organization": True,
                    "relationship_actually_happened": True,
                    "relationship_claim_supported": True,
                    "is_third_party_vendor": next(model._votes),
                    "is_customer_facing_integration": True,
                    "is_specific_named_entity": True,
                    "relationship_shape_matches_profile": True,
                    "subject_is_the_claimant": True,
                }

        return StructuredModel()


@pytest.mark.asyncio
async def test_semantic_check_fails_open_on_tied_surviving_votes():
    """Caught by review (Macroscope + Codex, independently): a failed ainvoke can
    drop the panel from 5 (odd, never ties) to 4 (even, can tie 2-2). `_majority`
    used to treat that tie as a negative verdict, wrongly rejecting a valid
    integration off a transient error plus an even split -- it must fail open
    instead, same policy as when every vote fails."""
    item = assertion(
        subject="ExampleCare",
        subject_type="Product",
        relation="Integrates",
        object="ExampleModule",
        object_type="Product",
        quote="ExampleCare Care-ExampleModule brings together ExampleModule and GuidingCare.",
        profile="integrations",
    )

    reason = await semantic_rejection_reason(
        item, chat_model=_TiedAfterOneFailureSemanticModel([True, True, False, False])
    )

    assert reason is None


@pytest.mark.asyncio
async def test_semantic_check_votes_multiple_times_for_both_relations():
    """ELI-1304 scoped voting to Integrates because that is where the flicker
    was observed, not because Adopts was shown to be steadier. It is the same
    close judgment, by the same model, on the same kind of evidence, and on a
    single call it had no protection against the same flicker."""
    integrates_item = assertion(
        subject="ExampleCare",
        subject_type="Product",
        relation="Integrates",
        object="ExampleModule",
        object_type="Product",
        quote="ExampleCare Care-ExampleModule brings together ExampleModule and GuidingCare.",
        profile="integrations",
    )
    customers_model = _FakeSemanticModel(is_named_organization=True)
    integrates_model = _FakeSemanticModel()

    await semantic_rejection_reason(assertion(), chat_model=customers_model)
    await semantic_rejection_reason(integrates_item, chat_model=integrates_model)

    assert customers_model.calls == grounding.SEMANTIC_CHECK_VOTES
    assert integrates_model.calls == grounding.SEMANTIC_CHECK_VOTES


@pytest.mark.asyncio
async def test_semantic_check_rejects_co_mention_without_relationship_claim():
    """Replaces the deleted CUSTOMER_CUE/INTEGRATION_CUE regexes -- same real cases,
    now judged by the model instead of a keyword match. Each of these is a real
    endpoint pair that's merely co-mentioned, not actually in the relationship:
    a network membership, a "chose someone else instead" quote, a bio mention of
    someone's alma mater, and a bio mention of someone's employer."""
    quotes = [
        ("American Heart Association", "FirstHx", "customers", {}),
        ("Clinic A", "FirstHx", "customers", {}),
        ("University of Toronto", "FirstHx", "customers", {}),
        ("FirstHx", "VetHx", "integrations", {"subject_type": "Product", "relation": "Integrates"}),
    ]
    for subject, obj, profile, overrides in quotes:
        item = assertion(
            subject=subject, object=obj, quote=f"{subject} / {obj} co-mention", **overrides
        )

        reason = await semantic_rejection_reason(
            item,
            chat_model=_FakeSemanticModel(
                is_named_organization=True, relationship_claim_supported=False
            ),
        )

        assert reason == "relationship_claim_not_supported"


@pytest.mark.asyncio
async def test_semantic_check_accepts_utilize_as_a_genuine_adoption_claim():
    """Real page text (Pontchartrain Cancer Center, ELI-1056 investigation):
    "figure out how to best utilize OncoEMR" is a genuine adoption statement --
    the deleted CUSTOMER_CUE regex only had "use[sd]?|using", missing this exact
    synonym, which caused a real, observed recovery to fail. The point of
    replacing the regex with a model judgment is that it doesn't need this
    (or the next) synonym added by hand."""
    item = assertion(
        subject="Pontchartrain Cancer Center",
        object="OncoEMR",
        quote="Pontchartrain Cancer Center figured out how to best utilize OncoEMR",
    )

    reason = await semantic_rejection_reason(
        item,
        chat_model=_FakeSemanticModel(
            is_named_organization=True, relationship_claim_supported=True
        ),
    )

    assert reason is None


@pytest.mark.asyncio
async def test_semantic_check_fails_open_when_model_errors():
    """`full_rejection_reason` (not this function itself) is the one that fails
    open on a model error -- verified below, not here. This just confirms the
    exception actually propagates out of `semantic_rejection_reason` rather
    than being swallowed at its own level."""
    item = assertion()

    with pytest.raises(RuntimeError):
        await semantic_rejection_reason(item, chat_model=_RaisingModel())


@pytest.mark.asyncio
async def test_full_rejection_reason_catches_co_mention_the_sync_check_misses():
    """Caught by review (Macroscope): with CUSTOMER_CUE/INTEGRATION_CUE gone,
    `relationship_is_grounded` alone silently accepts a bare co-mention -- its
    replacement (`relationship_claim_supported`) only lives in the separate,
    opt-in semantic check. `full_rejection_reason` is the single entry point
    that always runs both, so this real case can't slip through by a caller
    forgetting to also call the async half."""
    item = assertion(
        subject="American Heart Association",
        object="FirstHx",
        quote="FirstHx joined the American Heart Association Innovators Network",
    )

    # The sync-only check has no way to know this is a network membership, not
    # an adoption -- that's exactly the gap full_rejection_reason closes.
    assert relationship_is_grounded(item, item.quote, profile="customers", target_name="FirstHx")

    reason = await full_rejection_reason(
        item,
        item.quote,
        profile="customers",
        target_name="FirstHx",
        chat_model=_FakeSemanticModel(
            is_named_organization=True, relationship_claim_supported=False
        ),
    )

    assert reason == "relationship_claim_not_supported"


@pytest.mark.asyncio
async def test_full_rejection_reason_skips_semantic_check_when_deterministic_check_fails():
    """The semantic check is never spent on something the free check already
    rejected -- confirmed here via a model that would otherwise raise.
    Previously used subject_not_in_evidence for this (ELI-1192 removed that
    check for customers); unnamed_subject is still a live deterministic check
    for every profile, so it still demonstrates the short-circuit."""
    item = assertion(subject="", quote="adopt Ema.")

    reason = await full_rejection_reason(
        item, item.quote, profile="customers", chat_model=_RaisingModel()
    )

    assert reason == "unnamed_subject"


@pytest.mark.asyncio
async def test_full_rejection_reason_fails_open_when_semantic_check_errors():
    item = assertion()

    reason = await full_rejection_reason(
        item, item.quote, profile="customers", chat_model=_RaisingModel()
    )

    assert reason is None


@pytest.mark.asyncio
async def test_semantic_grounding_receives_only_the_matching_evidence_section(monkeypatch):
    captured = {}

    class Prompt:
        def build(self, **kwargs):
            captured.update(kwargs)
            return {"messages": [{"role": "user", "content": "check evidence"}]}

    async def load_prompt(relation):
        return Prompt()

    monkeypatch.setattr(grounding, "_load_semantic_check_prompt", load_prompt)
    item = assertion(
        subject="AllerVie Health",
        object="ExampleTeam",
        quote="AllerVie Health relies on ExampleTeam.",
    )
    content = """### Testimonial: AllerVie Health
Attribution: Amy Palmer, AllerVie Health
AllerVie Health relies on ExampleTeam.

### Testimonial: Other Practice
Unrelated text."""

    assert (
        await full_rejection_reason(
            item,
            content,
            profile="customers",
            target_name="ExampleTeam",
            chat_model=_FakeSemanticModel(is_named_organization=True),
        )
        is None
    )
    assert "amy palmer, allervie health" in captured["evidence_context"].lower()
    assert "other practice" not in captured["evidence_context"].lower()


def test_semantic_evidence_context_uses_fuzzy_quote_and_matching_endpoints():
    content = """## First testimonial
Beacon Clinic chose ExampleTeam for patient outreach.

## Second testimonial
AllerVie Health has chosen ExampleTeam for patient outreach.

## Other
Unrelated text."""

    context = grounding._semantic_evidence_context(
        "AllerVie Health chose ExampleTeam for patient outreach",
        content,
        endpoints=("AllerVie Health", "ExampleTeam"),
    )

    assert "Second testimonial" in context
    assert "First testimonial" not in context


def test_semantic_evidence_context_bounds_unmatched_content():
    content = "x" * (grounding.SEMANTIC_CONTEXT_MAX_CHARACTERS + 1)

    assert len(grounding._semantic_evidence_context("unmatched quote", content)) == (
        grounding.SEMANTIC_CONTEXT_MAX_CHARACTERS
    )


def test_the_referents_argument_travels_with_its_sentence():
    """The referent reads the whole page and decides the relation holds. Its
    sentence is the page's own words, but often no single sentence carries the
    relation alone -- that is the case the referent exists for. Sent only the
    sentence, the semantic check has to re-derive the connection the referent
    already worked out. The argument is that connection, so it travels too,
    labelled separately so a model's claim is never mistaken for the page's."""
    built = grounding._with_extra_context(
        "A paragraph from the page.",
        "Mercy went live last spring.",
        reasoning="The heading above the list says these organisations all run the product.",
    )

    assert "A paragraph from the page." in built
    assert "Mercy went live last spring." in built
    assert "The heading above the list says these organisations all run the product." in built
    assert built.index("Mercy went live") < built.index("The heading above")


def test_a_referent_sentence_with_no_argument_is_unchanged():
    """A caller that passes no reasoning must get exactly what it got before."""
    assert grounding._with_extra_context("Page text.", "A sentence.") == (
        grounding._with_extra_context("Page text.", "A sentence.", reasoning="")
    )
