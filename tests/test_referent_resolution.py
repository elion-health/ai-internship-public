import pytest

from ai_internship.enrichment.contracts import Assertion, GroundingRejection
from ai_internship.enrichment.referent_resolution import (
    FIRST_PARTY_ENTRYPOINT,
    FIRST_PARTY_REFERENT_PROMPT_SLUGS,
    MISSING_NAME_CODES,
    REFERENT_PROMPT_SLUGS,
    confirm_relation,
    inject_resolved_names,
    missing_endpoint_type,
    referent_slug,
)


def _rejection(code: str, **overrides) -> GroundingRejection:
    fields = {
        "subject": "she",
        "subject_type": "HealthSystem",
        "relation": "Adopts",
        "object": "FirstHx",
        "object_type": "Product",
        "quote": "That's when she implemented FirstHx.",
    }
    fields.update(overrides)
    return GroundingRejection(code=code, assertion=Assertion(**fields))


def test_missing_name_codes_cover_all_four_grounding_rejections():
    assert MISSING_NAME_CODES == {
        "unnamed_subject",
        "unnamed_object",
        "subject_not_in_evidence",
        "object_not_in_evidence",
    }


@pytest.mark.parametrize(
    "code,expected_type",
    [
        ("unnamed_subject", "HealthSystem"),
        ("subject_not_in_evidence", "HealthSystem"),
        ("unnamed_object", "Product"),
        ("object_not_in_evidence", "Product"),
    ],
)
def test_missing_endpoint_type_picks_the_right_side(code, expected_type):
    assert missing_endpoint_type(_rejection(code)) == expected_type


def test_missing_endpoint_type_is_none_for_unrelated_codes():
    assert missing_endpoint_type(_rejection("relationship_cue_missing")) is None


class _ChatModel:
    """Returns one answer per call. Each answer is (integrated, proof_sentence),
    mirroring the real call, which is non-deterministic enough that the same page
    yields a usable sentence on one attempt and nothing on the next."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def with_structured_output(self, schema, **kwargs):
        assert kwargs == {"method": "function_calling"}
        model = self

        class StructuredModel:
            async def ainvoke(self, messages):
                integrated, proof = model.answers[min(model.calls, len(model.answers) - 1)]
                model.calls += 1
                return {"reason": "because", "integrated": integrated, "proof_sentence": proof}

        return StructuredModel()


PAGE = "The Weight Clinic chose FirstHx last spring.\n\nThat's when she implemented FirstHx."
QUOTE = "That's when she implemented FirstHx."


async def _confirm(chat_model, **overrides):
    kwargs = {
        "subject": "The Weight Clinic",
        "object_name": "FirstHx",
        "relation": "Adopts",
        "quote": QUOTE,
        "unnamed": "The Weight Clinic",
        "content": PAGE,
        "source_url": "https://example.com/story",
        "chat_model": chat_model,
    }
    kwargs.update(overrides)
    return await confirm_relation(**kwargs)


@pytest.mark.asyncio
async def test_confirm_relation_returns_the_pages_own_copy_of_the_proof():
    """What comes back is the span located on the page, not the model's retyped
    version -- a retyped sentence can quietly gain words, and this one is handed
    to the semantic check as evidence."""
    model = _ChatModel((True, "The Weight Clinic chose FirstHx last spring."))
    found = await _confirm(model)
    assert found is not None
    span, reason = found
    assert "The Weight Clinic chose FirstHx" in span
    assert reason == "because"


@pytest.mark.asyncio
async def test_confirm_relation_stops_at_a_no_without_retrying():
    """A no is an answer, not a failure -- retrying it would just be rerolling
    until the model says yes."""
    model = _ChatModel((False, None), (True, "The Weight Clinic chose FirstHx last spring."))
    assert await _confirm(model) is None
    assert model.calls == 1


@pytest.mark.asyncio
async def test_confirm_relation_rejects_a_sentence_that_is_not_on_the_page():
    """The whole point of the check: a fabricated proof must not reach the judges."""
    model = _ChatModel(
        (True, "The Weight Clinic has a certified bi-directional FirstHx interface.")
    )
    assert await _confirm(model) is None


@pytest.mark.asyncio
async def test_confirm_relation_retries_past_an_empty_proof():
    model = _ChatModel((True, None), (True, "The Weight Clinic chose FirstHx last spring."))
    found = await _confirm(model)
    assert found is not None
    assert model.calls == 2


@pytest.mark.asyncio
async def test_confirm_relation_treats_unknown_as_no_proof():
    model = _ChatModel((True, "unknown"))
    assert await _confirm(model) is None


@pytest.mark.asyncio
async def test_confirm_relation_returns_none_without_a_chat_model():
    assert await _confirm(None) is None


def test_each_relation_type_has_its_own_referent_prompt():
    """The two ask different questions -- are these products connected, versus
    does this organisation use this product -- and ELI-1549 measured that the
    wording which works for one scores badly on the other."""
    assert REFERENT_PROMPT_SLUGS["Integrates"] != REFERENT_PROMPT_SLUGS["Adopts"]
    assert set(REFERENT_PROMPT_SLUGS) == {"Integrates", "Adopts"}


def test_a_first_party_customer_page_uses_the_first_party_prompt():
    """ELI-1550: the customers referent was tuned on a mix that was 7 to 3
    external, and a prompt tuned on a vendor's own pages beats it there --
    0.975 against 0.955 over ten runs. It is only measured on those pages, so
    it is routed to them alone rather than replacing the shared prompt."""
    assert (
        referent_slug("Adopts", FIRST_PARTY_ENTRYPOINT)
        == (FIRST_PARTY_REFERENT_PROMPT_SLUGS["Adopts"])
    )
    assert referent_slug("Adopts", "external_acquisition") == REFERENT_PROMPT_SLUGS["Adopts"]


def test_a_first_party_integration_page_uses_the_first_party_prompt():
    """The integrations referent was broken on a vendor's own site -- it accepted
    31 relations a vendor's own catalogue page does not support -- and a prompt
    written for that case scores 0.927 against 0.782 at the referent's own step.
    Expect no change to the graph: the five-vote semantic check was already
    rejecting most of what it wrongly accepted."""
    assert (
        referent_slug("Integrates", FIRST_PARTY_ENTRYPOINT)
        == (FIRST_PARTY_REFERENT_PROMPT_SLUGS["Integrates"])
    )
    assert (
        referent_slug("Integrates", "external_acquisition") == (REFERENT_PROMPT_SLUGS["Integrates"])
    )


def test_a_relation_with_no_referent_prompt_at_all_resolves_to_nothing():
    """No prompt means no opinion, not a crash -- a new relation type must not
    take the ingestion graph down."""
    assert referent_slug("Mentions", FIRST_PARTY_ENTRYPOINT) is None
    assert referent_slug("Mentions", "external_acquisition") is None


@pytest.mark.asyncio
async def test_confirm_relation_abstains_for_a_relation_it_has_no_prompt_for():
    """No prompt means no opinion, not a crash -- a new relation type must not
    take the ingestion graph down."""
    assert await _confirm(_ChatModel((True, "anything")), relation="Mentions") is None


def test_inject_resolved_names_splices_name_next_to_the_quote():
    content = "Intro.\n\nThat's when she implemented FirstHx.\n\nMore text."

    injected = inject_resolved_names(
        content, [("That's when she implemented FirstHx.", "The Weight Clinic", "Adopts")]
    )

    assert "That's when she implemented FirstHx. (referring to The Weight Clinic)" in injected


def test_inject_resolved_names_ignores_a_quote_not_found_at_all():
    content = "Some unrelated page content."

    injected = inject_resolved_names(
        content, [("Not on this page.", "The Weight Clinic", "Adopts")]
    )

    assert injected == content


def test_inject_resolved_names_tolerates_markdown_link_syntax():
    """Observed live on Pontchartrain's real page: the extractor's quote is
    "using OncoEMR®" (plain text) while the raw content has a markdown link,
    "using [OncoEMR®](https://...)" -- an exact substring match misses this
    even though the quote is a legitimate near-verbatim match, exactly the gap
    quote_is_grounded already tolerates."""
    content = (
        "She also shares the value her practice has seen in using "
        "[OncoEMR®](https://flatiron.com/oncology/oncology-ehr), Flatiron's EHR."
    )
    quote = "She also shares the value her practice has seen in using OncoEMR®"

    injected = inject_resolved_names(content, [(quote, "Pontchartrain Cancer Center", "Adopts")])

    assert "(referring to Pontchartrain Cancer Center)" in injected
    assert injected != content


def test_inject_resolved_names_does_not_splice_inside_a_markdown_link():
    """Observed live: the fuzzy match for "using OncoEMR®" (plain quote) landed
    right after the visible text inside "[OncoEMR®](url)", before the closing
    bracket -- splicing there produced "[OncoEMR® (referring to X)](url)",
    breaking the link syntax instead of annotating the sentence."""
    content = (
        "She also shares the value her practice has seen in using "
        "[OncoEMR®](https://flatiron.com/oncology/oncology-ehr), Flatiron's EHR."
    )
    quote = "She also shares the value her practice has seen in using OncoEMR®"

    injected = inject_resolved_names(content, [(quote, "Pontchartrain Cancer Center", "Adopts")])

    assert "OncoEMR® (referring to" not in injected
    assert "](https://flatiron.com/oncology/oncology-ehr) (referring to" in injected


def test_inject_resolved_names_handles_multiple_injections():
    content = "First quote here. Second quote here."

    injected = inject_resolved_names(
        content,
        [
            ("First quote here.", "Mission Cancer + Blood", "Adopts"),
            ("Second quote here.", "The Weight Clinic", "Adopts"),
        ],
    )

    assert "First quote here. (referring to Mission Cancer + Blood)" in injected
    assert "Second quote here. (referring to The Weight Clinic)" in injected
