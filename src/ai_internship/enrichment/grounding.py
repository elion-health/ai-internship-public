"""Exact-quote evidence checks against retained source content."""

from __future__ import annotations

import asyncio
import difflib
import functools
import re
import unicodedata

from pydantic import BaseModel, Field

from ai_internship.enrichment.contracts import Assertion
from ai_internship.public_assets import load_prompt, project_name
from ai_internship.research.content_quality import assess_page_relevance

BRAINTRUST_PROJECT = project_name()
# Load a versioned prompt rather than hardcoding it, same
# as profiles.py's customer-extraction prompt, so it can be iterated without a deploy.
# Split into one prompt per relation: customers and integrations have
# different standards worth tuning separately -- e.g. what counts as a valid "customer
# organization" vs. what counts as real technical interoperability are unrelated
# judgment calls that a single shared prompt was blurring together.
SEMANTIC_CHECK_PROMPT_SLUGS = {
    "Adopts": "semantic-grounding-check-customers-fe3b",
    "Integrates": "semantic-grounding-check-integrations-a12e",
}


async def _load_semantic_check_prompt(relation: str):
    slug = SEMANTIC_CHECK_PROMPT_SLUGS.get(relation)
    if slug is None:
        return None
    return await asyncio.get_running_loop().run_in_executor(
        None,
        functools.partial(load_prompt, slug=slug),
    )


PROFILE_EDGE_SHAPES = {
    "customers": {("HealthSystem", "Adopts", "Product")},
    "integrations": {("Product", "Integrates", "Product")},
}

GENERIC_ENDPOINTS = {
    "app",
    "application",
    "clinic",
    "ehr",
    "electronic health record",
    "electronic health record systems",
    "electronic medical record",
    "electronic medical record systems",
    "emr",
    "ehr systems",
    "emr systems",
    "health system",
    "hospital",
    "platform",
    "product",
    "software",
    "solution",
    "system",
}


# Delimiter-anchored, not a nullable alternation under `*` -- the earlier version
# (`(?:[^()]*|\([^()]*\))*`) let an unterminated link ("[x](aaaa...", no closing
# paren) backtrack catastrophically, hanging on a single crawled page (Codex
# review, PR #55). This still tolerates one level of nested parens in the URL,
# but each character is only ever consumed one way.
_MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]*)\]\(([^()]*(?:\([^()]*\)[^()]*)*)\)")
# Reference-style links -- `[text][ref]` (or shorthand `[text][]`, using `text`
# itself as the ref) plus a separate `[ref]: url` definition line elsewhere in
# the document. Not handled by _MARKDOWN_LINK_RE at all (no inline `(url)`), so
# a URL-derived name hidden this way was invisible to both normalize_text and
# the URL-only check (Macroscope review, PR #55).
_REFERENCE_LINK_RE = re.compile(r"\[([^\]]*)\]\[([^\]]*)\]")
_REFERENCE_DEF_RE = re.compile(r"^[ \t]*\[([^\]]+)\]:[ \t]*(\S+).*$", re.MULTILINE)


def normalize_text(text: str) -> str:
    """Normalize to a fixed point, not just once.

    One pass is not idempotent: a markdown link carrying a title
    (`[text](url "title")`) only partly collapses, and normalizing the result
    again strips more. That made a quote taken out of already-normalized page
    text stop matching that same page, because the page had been through one
    pass and the quote through two. Looping until the text stops changing makes
    "normalized" mean one thing everywhere.
    """
    for _ in range(4):
        normalized = _normalize_once(text)
        if normalized == text:
            return normalized
        text = normalized
    return text


def _normalize_once(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    # One level of nested parens tolerated in the URL -- a plain `[^)]*` stops at
    # the first `)`, so a URL containing one (e.g. a Wikipedia-style
    # ".../wiki/Marietta_(Georgia)") only had its link syntax partly stripped,
    # leaking the rest of the URL as literal visible text (Macroscope review,
    # PR #55).
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = _REFERENCE_DEF_RE.sub("", text)
    text = _REFERENCE_LINK_RE.sub(r"\1", text)
    text = re.sub(r"[*_`~#>®™©]", "", text)
    return " ".join(text.split()).rstrip(".")


QUOTE_MIN_SIMILARITY = 0.75
SEMANTIC_CONTEXT_MAX_CHARACTERS = 6000

# Catches the extractor abstaining by fabricating a relationship instead of
# returning an empty list -- e.g. quote="No relevant HealthSystem -> Adopts ->
# Product relationship is supported...". quote_is_grounded's near-verbatim
# check usually rejects these already (fabricated text rarely scores close to
# real page content), but that's incidental, not a designed guarantee -- this
# is an explicit, cheap check for the same failure mode instead of relying on
# a similarity score staying low by chance.
#
# Deliberately narrow: only the literal schema-triple arrow notation
# (Type -> Relation -> Type), not standalone phrases like "not supported" or
# "does not state" -- those can appear in real grounded evidence describing
# something unrelated (e.g. "Hospital uses Product; legacy software is not
# supported"), and would wrongly reject it. Caught by automated review on
# PR #19 against the original, broader version of this pattern.
META_COMMENTARY_RE = re.compile(r"\w+\s*->\s*\w+\s*->\s*\w+")


def quote_is_grounded(quote: str, content: str) -> bool:
    """Accept quotes that are near-verbatim, not just character-exact.

    LLMs reliably paraphrase or blend in boilerplate (e.g. prepending "X today
    announced that...") even when told to copy text exactly, so an exact
    substring check rejects real evidence. This falls back to a similarity
    ratio against the source when the quote isn't found verbatim.
    """
    normalized_quote = normalize_text(quote)
    if not normalized_quote:
        return False
    normalized_content = normalize_text(content)
    if normalized_quote in normalized_content:
        return True
    matcher = difflib.SequenceMatcher(None, normalized_quote, normalized_content, autojunk=False)
    matched_chars = sum(block.size for block in matcher.get_matching_blocks())
    return (matched_chars / len(normalized_quote)) >= QUOTE_MIN_SIMILARITY


def quote_names_both_endpoints(assertion: Assertion) -> bool:
    """Does the quote name both sides of the relation it is supposed to prove?

    When it doesn't, the relation isn't necessarily wrong -- a page often states
    one side in a heading and the other in a sentence -- but the quote alone
    cannot settle it, so it needs the referent's second look (ELI-1544/1549).
    """
    quote = normalize_text(assertion.quote).lower()
    return (
        normalize_text(assertion.subject).lower() in quote
        and normalize_text(assertion.object or "").lower() in quote
    )


CONTIGUOUS_MIN_RATIO = 0.95


def quote_is_contiguous(quote: str, content: str) -> bool:
    """Does the quote appear as ONE unbroken run of the page?

    `quote_is_grounded` sums every matching chunk no matter how far apart they
    sit, which a stitched quote passes with a near-perfect score -- adsc.com's
    "<prose> ... ExamplePartner ... ExampleBooking" is each piece really on the page, never
    together. Here the single longest contiguous match has to cover almost all
    of the quote, so ordinary whitespace or punctuation drift still passes while
    pieces gathered from across the page do not (ELI-1544).
    """
    needle = normalize_text(quote)
    if not needle:
        return False
    haystack = normalize_text(content)
    if needle in haystack:
        return True
    longest = difflib.SequenceMatcher(None, needle, haystack, autojunk=False).find_longest_match(
        0, len(needle), 0, len(haystack)
    )
    return longest.size / len(needle) >= CONTIGUOUS_MIN_RATIO


def contiguous_page_span(quote: str, content: str) -> str | None:
    """The page's own text spanning everything the quote really matched.

    The extractor splices: it lifts two real pieces of a page and drops what
    sits between them, about 1 quote in 11, and more often the more relations it
    has to emit in one call. The pieces are genuine -- what it skipped is the few
    characters joining a heading to the paragraph under it, or a paragraph in the
    middle. The quote then matches the page in pieces rather than as one span,
    `quote_is_contiguous` rejects it, and the relation dies with nothing having
    read the page. That cost 447 good relations on the corpus.

    Taking the page from the start of the first matched piece to the end of the
    last gives back a quote that IS a run of the page, by construction. Nothing
    is invented -- every character comes off the page -- and nothing the model
    pointed at is dropped, so both endpoint names survive whenever the model put
    them in.

    Returns None only when the quote matched nothing worth the name, which
    `quote_is_grounded` has already rejected as `quote_not_in_source` before this
    is ever reached.
    """
    needle, haystack = normalize_text(quote), normalize_text(content)
    if not needle or not haystack:
        return None
    blocks = [
        block
        for block in difflib.SequenceMatcher(
            None, needle, haystack, autojunk=False
        ).get_matching_blocks()
        if block.size
    ]
    if not blocks:
        return None
    span = haystack[blocks[0].b : blocks[-1].b + blocks[-1].size].strip()
    return span or None


def _endpoint_matches_target(endpoint: str, target_name: str) -> bool:
    """True if `endpoint` and `target_name` refer to the same thing, in either
    direction -- `endpoint` may be an abbreviation of `target_name` (object
    "OncoEMR" for target "Flatiron OncoEMR") or a paraphrase that adds words
    (object "Flatiron Health OncoEMR" for the same target)."""
    normalized_endpoint = normalize_text(endpoint).lower()
    normalized_target = normalize_text(target_name).lower()
    return _contains_endpoint(normalized_endpoint, target_name) or _contains_endpoint(
        normalized_target, endpoint
    )


def _hyphen_qualifier_match(token: str, object_token: str) -> bool:
    """True if `token` IS `object_token`, or is exactly one hyphen-joined qualifier
    word followed by it -- "wellframe" or "care-wellframe" for object "wellframe".

    Only ONE hyphen segment is allowed before the object, deliberately -- "connects-
    to-acme" also ends in "-acme" but names an action between two words ("connects
    to"), not a single compound brand, and multiple hyphen segments is what tells
    the two apart. `token.rpartition("-")` on "connects-to-acme" leaves "connects-
    to" as the qualifier, which itself still contains a hyphen -- reject. On "care-
    wellframe" the qualifier is "care" alone -- accept. Found re-reviewing the fix
    below, not by an automated review pass: the first version of this check allowed
    any hyphen-adjacency and wrongly flagged "connects-to-Acme" as a compound brand."""
    if token == object_token:
        return True
    qualifier, sep, suffix = token.rpartition("-")
    return bool(sep) and suffix == object_token and "-" not in qualifier


def _quote_names_compound_brand(quote: str, target_name: str, object_name: str) -> bool:
    """True if the quote names one compound brand joining the target vendor's full
    name directly to the object's name -- e.g. "ExampleCare Care-ExampleModule" for target
    "ExampleCare", object "ExampleModule" -- with zero words between them (a hyphenated
    or run-together brand, not "X and Y" / "X integrates with Y", which name two
    distinct companies with linking words between them). Deliberately strict (only
    the words immediately after the target) so it doesn't fire on real third-party
    mentions like "Infermedica and Talkie.ai partnered" or "Infermedica integrates
    with Acme"."""
    words = normalize_text(quote).lower().split()
    target_tokens = normalize_text(target_name).lower().split()
    object_tokens = normalize_text(object_name).lower().split()
    if not target_tokens or not object_tokens:
        return False
    span = len(target_tokens)
    # The FULL target name, not just its first word -- caught by review: comparing
    # only the first word means the target's own multi-word name can trigger a false
    # match against an unrelated object purely because the target's second word
    # happens to equal the object's first word (target "Acme Care Portal", object
    # "Care ExampleModule" -- the target's own "Acme Care..." satisfied the old
    # first-word-only check against an object it has nothing to do with).
    for i in range(len(words) - span):
        if words[i : i + span] != target_tokens:
            continue
        next_word = words[i + span]
        if len(object_tokens) == 1:
            if _hyphen_qualifier_match(next_word, object_tokens[0]):
                return True
        elif words[i + span : i + span + len(object_tokens)] == object_tokens:
            return True
    return False


def _contains_endpoint(text: str, endpoint: str) -> bool:
    """Require every significant word of `endpoint` to appear in `text` as a whole
    word, not necessarily contiguous -- so a paraphrase like "Flatiron Health's
    OncoEMR" still grounds the endpoint "Flatiron OncoEMR"."""
    tokens = [token for token in re.split(r"\s+", normalize_text(endpoint).lower()) if token]
    if not tokens:
        return False
    return all(re.search(rf"(?<!\w){re.escape(token)}(?!\w)", text) for token in tokens)


def _shares_any_token(text: str, endpoint: str) -> bool:
    """True if AT LEAST ONE significant word of `endpoint` appears in `text` as a
    whole word -- unlike `_contains_endpoint`'s all(), this is what tells a real
    (possibly abbreviated) mention apart from a name with no connection to the
    visible text at all. "Rush" genuinely shares a token with "Rush University
    Medical Center"; "one director notes" shares none with "Example Clinic 28"."""
    tokens = [token for token in re.split(r"\s+", normalize_text(endpoint).lower()) if token]
    normalized = normalize_text(text).lower()
    return any(re.search(rf"(?<!\w){re.escape(token)}(?!\w)", normalized) for token in tokens)


def _endpoint_named_only_via_url(endpoint: str, content: str) -> bool:
    """True only if some ONE hyperlink's own anchor text plus its target URL
    together name `endpoint` (a slug like "example-clinic-28-increases-upfront-
    payments" whole-word-matches "CAO", "Division", "28" via its hyphens), while
    that SAME link's anchor text alone shares nothing with it at all (e.g.
    "[one director notes](.../example-clinic-28-.../)" -- the reader sees no
    connection whatsoever between the visible words and the entity the
    extractor named).

    Deliberately narrower than "is this name visible anywhere at all" (which
    ELI-1192/1195 already tried, as a blanket subject_not_in_evidence /
    object_not_in_evidence check, and reverted for customers/integrations
    specifically because it broke on real name variants like "Rush" for "Rush
    University Medical Center"). Scoped to one link at a time, and to sharing
    ANY token rather than requiring the anchor to spell out the whole name, so
    a real abbreviated mention is never mistaken for a fabricated one:
    - "Rush" as a link's own anchor text, even when that same link's href is
      "/rush-university-medical-center", must NOT trip this -- the anchor
      genuinely shares "Rush" with the entity (Codex review, PR #55).
    - An unrelated OTHER link elsewhere on the page whose slug happens to
      contain "university"/"medical"/"center" must NOT combine with a real,
      separate "Rush" mention to manufacture a match -- checking one link's
      own anchor+href at a time, instead of the whole page's raw content
      against the whole page's visible content, means unrelated links never
      get combined in the first place (Macroscope review, PR #55).

    Underscores are normalized to hyphens before matching a link's raw text,
    since a slug commonly uses either as its word separator and
    `_contains_endpoint`'s whole-word regex treats `_` (but not `-`) as a word
    character -- an underscore-separated slug otherwise never satisfies it at
    all (Macroscope review, PR #55).

    Reference-style links (`[text][ref]` plus a separate `[ref]: url`
    definition line) are resolved to the same (anchor, href) shape as an
    inline link -- _MARKDOWN_LINK_RE alone never sees these at all, so a name
    hidden this way was invisible to this check entirely (Macroscope review,
    PR #55)."""
    # First definition wins on a duplicate label, matching CommonMark -- a
    # plain dict(findall(...)) keeps the LAST one instead (Macroscope review,
    # PR #55).
    definitions: dict[str, str] = {}
    for label, url in _REFERENCE_DEF_RE.findall(content):
        definitions.setdefault(label, url)
    links = [
        *_MARKDOWN_LINK_RE.findall(content),
        *(
            (anchor, definitions.get(ref) or definitions.get(anchor, ""))
            for anchor, ref in _REFERENCE_LINK_RE.findall(content)
        ),
    ]
    for anchor_text, href in links:
        raw = f"{anchor_text} {href}".replace("_", "-").lower()
        # Even when THIS link's own anchor+href looks fabricated, the name is
        # genuinely on the page (not URL-only) if it's independently visible
        # anywhere else in the page's real text -- an unrelated link's href
        # coincidentally containing the same words must not override that
        # (Macroscope review, PR #55).
        if (
            _contains_endpoint(raw, endpoint)
            and not _shares_any_token(anchor_text, endpoint)
            and not _contains_endpoint(normalize_text(content).lower(), endpoint)
        ):
            return True
    return False


def _evidence_context(quote: str, content: str) -> str:
    normalized_quote = normalize_text(quote).lower()
    sections = re.split(r"(?m)(?=^\s{0,3}#{1,6}\s+)", content)
    matching_section = next(
        (section for section in sections if normalized_quote in normalize_text(section).lower()),
        content,
    )
    heading = re.match(r"^\s{0,3}#{1,6}\s+([^\n]+)", matching_section)
    return normalize_text(f"{heading.group(1) if heading else ''} {quote}").lower()


def _semantic_evidence_context(quote: str, content: str, *, endpoints: tuple[str, ...] = ()) -> str:
    normalized_quote = normalize_text(quote).lower()
    sections = re.split(r"(?m)(?=^\s{0,3}#{1,6}\s+)", content)
    matches = []
    for section in sections:
        normalized_section = normalize_text(section).lower()
        matcher = difflib.SequenceMatcher(
            None, normalized_quote, normalized_section, autojunk=False
        )
        similarity = sum(block.size for block in matcher.get_matching_blocks()) / max(
            len(normalized_quote), 1
        )
        if similarity >= QUOTE_MIN_SIMILARITY:
            matches.append(
                (
                    sum(_contains_endpoint(normalized_section, endpoint) for endpoint in endpoints),
                    similarity,
                    section,
                )
            )
    return max(
        matches,
        key=lambda match: match[:2],
        default=(0, 0, content[:SEMANTIC_CONTEXT_MAX_CHARACTERS]),
    )[2][:SEMANTIC_CONTEXT_MAX_CHARACTERS]


# ELI-1147: a HealthSystem subject that's actually an individual person (a
# clinician's name, with or without a title/credential) passes every other
# check -- it's a real, grounded, proper-named endpoint -- but isn't an
# organization. Confirmed live: "Dr. Hala Kufaishi", "Dr. Johannes Piek"
# accepted as HealthSystem subjects. Scoped to explicit title/credential
# markers rather than a bare "two capitalized words" heuristic, which would
# false-positive on real org names like "Alabama Oncology" or "Horizon
# Health Network".
PERSON_TITLE_RE = re.compile(r"^(?:Dr|Mrs|Mr|Ms|Prof(?:essor)?)\.?\s+\S", re.IGNORECASE)
PERSON_CREDENTIAL_RE = re.compile(
    r",\s*(?:MD|DO|DVM|PhD|MSc|MS|RN|NP|DDS|DPM|OD|PA-C)\.?\s*$", re.IGNORECASE
)
# Caught by review (Macroscope): a real company can legitimately start with a title,
# e.g. "Dr. Reddy's Laboratories" -- PERSON_TITLE_RE alone can't tell that apart from
# "Dr. Hala Kufaishi". A possessive apostrophe or a corporate-entity word after the
# title is a real person's name only in the rarest edge case, so treat either as
# excusing the title match rather than trying to enumerate every such company by name.
ORG_SUFFIX_RE = re.compile(
    r"'s\b|\b(?:laborator(?:y|ies)|labs?|pharma(?:ceutical)?s?|industries|enterprises|"
    r"group|holdings?|company|corp(?:oration)?|inc|llc|ltd|technolog(?:y|ies)|"
    r"solutions|systems)\.?\s*$",
    re.IGNORECASE,
)


def _is_individual_name(name: str) -> bool:
    normalized = normalize_text(name)
    if ORG_SUFFIX_RE.search(normalized):
        return False
    return bool(PERSON_TITLE_RE.match(normalized) or PERSON_CREDENTIAL_RE.search(normalized))


def _is_named_endpoint(endpoint: str) -> bool:
    normalized = normalize_text(endpoint)
    lower = normalized.lower()
    generic_system = re.search(
        r"\b(?:electronic (?:health|medical) records?|ehr|emr)\b.*\bsystems?\b", lower
    )
    generic_label = re.fullmatch(
        r"(?:the\s+)?(?:(?:patient|intake|digital|health|healthcare|medical|clinical|care|"
        r"electronic|record|records|ehr|emr)\s+)*(?:hospital|clinic|health system|platform|"
        r"product|software|solution|system|application|app)s?",
        lower,
    )
    return (
        lower not in GENERIC_ENDPOINTS
        and not generic_system
        and not generic_label
        and bool(normalized)
    )


def content_may_support_profile(content: str, profile: str, *, target_name: str = "") -> bool:
    del profile
    return not target_name or assess_page_relevance(content, target_name).eligible


def relationship_is_grounded(
    assertion: Assertion,
    content: str,
    *,
    profile: str = "",
    target_name: str = "",
) -> bool:
    """Deterministic layer only -- quote/endpoint match, shape, entity type. Does NOT
    catch a co-mention that never actually states the relationship (e.g. "Mercy and
    FirstHx announced a partnership") -- that's `semantic_rejection_reason`'s job. Use
    `full_rejection_reason` for a complete check; this one exists for callers (mostly
    tests) that specifically want the free, synchronous checks in isolation."""
    return (
        grounding_rejection_reason(
            assertion,
            content,
            profile=profile,
            target_name=target_name,
        )
        is None
    )


def grounding_rejection_reason(
    assertion: Assertion,
    content: str,
    *,
    profile: str = "",
    target_name: str = "",
    assume_grounded: frozenset[str] = frozenset(),
) -> str | None:
    """`assume_grounded` skips the nearby-to-the-quote check for "subject" and/or
    "object" -- for an endpoint resolve_missing_name already independently
    verified against this same page, re-deriving "is it near the quote" from
    scratch would fail real referent-distance cases (the name is real, just
    not adjacent to this specific quote), and that check adds no protection
    resolve_missing_name hasn't already provided (see ELI-1146)."""
    if not assertion.object or not assertion.object_type or not assertion.relation:
        return "incomplete_relationship"
    if not _is_named_endpoint(assertion.subject):
        return "unnamed_subject"
    if not _is_named_endpoint(assertion.object):
        return "unnamed_object"
    if assertion.subject_type == "HealthSystem" and _is_individual_name(assertion.subject):
        return "subject_is_individual"
    if META_COMMENTARY_RE.search(assertion.quote):
        return "meta_commentary_quote"
    if not quote_is_grounded(assertion.quote, content):
        return "quote_not_in_source"
    if not quote_is_contiguous(assertion.quote, content):
        return "quote_not_contiguous"
    quote = normalize_text(assertion.quote).lower()
    context = _evidence_context(assertion.quote, content)
    normalized_target = normalize_text(target_name).lower()
    active_profile = profile or assertion.profile

    # ELI-1192: target_not_endpoint stays for every profile, customers
    # included. Prompt-injecting target_product_name makes the model far
    # less likely to name a different product, but it's not a guarantee --
    # an LLM that ignores the instruction and still emits a co-mentioned
    # platform (e.g. "ExampleEHR") as the object needs to still be
    # caught here; the semantic check judges the stated relationship, not
    # whether the object is actually the vendor being researched, so it
    # can't substitute for this (PR #36 review, Codex + Macroscope).
    if normalized_target and not (
        _endpoint_matches_target(assertion.subject, target_name)
        or _endpoint_matches_target(assertion.object, target_name)
    ):
        return "target_not_endpoint"

    # object_not_in_evidence and subject_not_in_evidence ARE still skipped
    # for customers AND integrations below -- those are the brittle
    # whole-word string-match checks (breaks on name variants like "Suki
    # Assistant" vs "Suki", or shortened references like "Rush" for "Rush
    # University Medical Center"), a genuinely different problem from
    # target_not_endpoint's job of catching the wrong product entirely.
    # Integrations gets the same skip as customers (ELI-1195): extraction
    # now pins the target directly for both profiles, so these two checks
    # have no "wrong product" case left to catch either -- kept for any
    # other profile that doesn't get the same target-injection treatment.
    #
    # Macroscope (PR #38 review) flagged this as leaving a fabricated
    # endpoint unverified at the deterministic layer. Deliberate, not an
    # oversight: skipping only one side would just move the brittleness
    # rather than remove it -- for customers the pinned side (object) needs
    # the same skip as the free-text side (subject) because it can be
    # legitimately named only in a different page section than the quote
    # (test_target_endpoint_cannot_be_grounded_from_another_page_section),
    # and integrations' pinned side is the subject, so the same reasoning
    # applies with subject/object swapped. Real verification for both sides
    # is `semantic_rejection_reason`'s job now (relationship_claim_supported
    # / relationship_actually_happened evaluate the full evidence_context,
    # not just the quote). Its fail-open on a missing chat_model or a
    # transient model error is a separate, pre-existing, deliberate
    # tradeoff (see full_rejection_reason's docstring) that predates this
    # change and already applied to customers; the one production caller
    # (enrichment/graph.py) always passes chat_model.

    def endpoint_is_grounded(endpoint: str) -> bool:
        normalized_endpoint = normalize_text(endpoint).lower()
        return (
            _contains_endpoint(quote, endpoint)
            or _contains_endpoint(context, endpoint)
            or normalized_endpoint == normalized_target
            and _contains_endpoint(context, endpoint)
        )

    if (
        active_profile not in ("customers", "integrations")
        and "subject" not in assume_grounded
        and not endpoint_is_grounded(assertion.subject)
    ):
        return "subject_not_in_evidence"
    if (
        active_profile not in ("customers", "integrations")
        and "object" not in assume_grounded
        and not endpoint_is_grounded(assertion.object)
    ):
        return "object_not_in_evidence"
    if normalize_text(assertion.subject).lower() == normalize_text(assertion.object).lower():
        return "self_edge"
    shape = (assertion.subject_type, assertion.relation, assertion.object_type)
    if active_profile in PROFILE_EDGE_SHAPES and shape not in PROFILE_EDGE_SHAPES[active_profile]:
        return "profile_shape_mismatch"
    if (
        active_profile == "customers"
        and target_name
        and normalize_text(assertion.subject).lower() == normalize_text(target_name).lower()
    ):
        return "target_as_customer_subject"
    # ELI-1304: a compound brand joining the vendor's name directly to the object's
    # name ("ExampleCare Care-ExampleModule" for target "ExampleCare", object "ExampleModule")
    # is the vendor naming itself as the object's owner -- catch this deterministically
    # rather than leaving it to the semantic check alone, which was unreliable on this
    # exact pattern in practice (flickered between catching and missing it).
    if (
        active_profile == "integrations"
        and target_name
        and _quote_names_compound_brand(assertion.quote, target_name, assertion.object)
    ):
        return "target_as_integration_partner"
    # ELI-1317: real FP (ExampleBooking/Example Clinic 28 via a blog roundup) -- the
    # extractor sometimes names an endpoint after a hyperlink's TARGET URL
    # ("[one director notes](.../example-clinic-28-.../)") rather than anything a
    # reader can actually see.
    if active_profile == "customers" and _endpoint_named_only_via_url(assertion.subject, content):
        return "subject_named_only_in_url"
    if active_profile == "integrations" and _endpoint_named_only_via_url(assertion.object, content):
        return "object_named_only_in_url"
    return None


# ELI-1304: the semantic check's LLM call is noisy on close judgments -- validation
# against real FP/TP cases showed a single call flicker both ways (missing a real
# same-vendor case one run, wrongly rejecting a real third party the next). Voting
# only applies to Integrates, where this flicker was actually observed; customers
# (Adopts) stays a single call.
SEMANTIC_CHECK_VOTES = 5


def _majority(values: list[bool]) -> bool:
    """Majority vote. SEMANTIC_CHECK_VOTES is odd so a full panel never ties, but a
    failed ainvoke (see `outcomes`/`successes` below) can drop the surviving panel to
    an even number -- a tie there fails open (True) rather than being treated as a
    negative verdict, consistent with fully-failing open when every vote errors.
    Caught by review (Macroscope + Codex, independently)."""
    return sum(values) >= len(values) / 2


# ELI-1147: three qualitative judgments that don't hold up as regex/word-list
# checks, all folded into one LLM call instead of growing keyword lists one
# real miss at a time (see ELI-1120's CUSTOMER_CUE/INTEGRATION_CUE history):
#
# 1. is_named_organization -- generic non-named settings ("urgent care",
#    "emergency departments") pass every check above (the quote is real, the
#    endpoint is a whole-word match).
# 2. relationship_actually_happened -- hypothetical/counterfactual language
#    ("derailing our plans") also passes every check above.
# 3. relationship_claim_supported -- replaces the CUSTOMER_CUE/INTEGRATION_CUE
#    regexes entirely. Same job -- reject two names that are merely
#    co-mentioned (an investor, a co-announcement partner, a quoted person, a
#    bio affiliation, an award/network membership) rather than actually in
#    the stated relationship -- as a judgment instead of a keyword match.
#
# Deliberately runs *after* every cheap regex check above, and only for
# assertions that already passed them, so the (slow, costly) LLM call is
# never spent on something a free check would have rejected anyway.
class _SemanticVerdict(BaseModel):
    is_named_organization: bool = Field(
        description=(
            "True only if the subject is an actual named organization (a specific hospital, "
            "health system, clinic, or medical practice) -- false for an individual person, a "
            "place, or a generic category/type of setting (e.g. 'urgent care', 'emergency "
            "departments', 'hospitals'). Also false for a second-person placeholder ('your "
            "practice'), a generic label from a demo/CTA ('an ExampleBooking customer'), or an "
            "anonymized case-study description with no real name given ('a national primary "
            "care organization', 'a large orthopedic practice') -- these are not specific real "
            "organizations even though the surrounding quote is genuine. Also false when the "
            "'organization' named is actually the vendor's own parent brand, sub-brand, or "
            "in-house feature name (e.g. a vendor's own company name appearing as if it were a "
            "separate customer, or a feature name that literally contains the vendor's own "
            "name/product) -- the vendor describing its own capabilities is not a customer."
        )
    )
    relationship_actually_happened: bool = Field(
        description=(
            "True only if the quote states the relationship as a real, completed fact -- false "
            "if it describes a wish, hope, plan, or intention, or explicitly says the "
            "relationship was cancelled, derailed, postponed, or never came to be."
        )
    )
    relationship_claim_supported: bool = Field(
        description=(
            "True only if the quote explicitly states this specific type of relationship "
            "(active use/deployment for Adopts, an actual technical integration for Integrates) "
            "between the two named entities -- false if they're merely co-mentioned, e.g. an "
            "investor, a co-announcement partner, a quoted person, a bio affiliation, or an "
            "award/network membership."
        )
    )
    is_third_party_vendor: bool = Field(
        description=(
            "For an Integrates relationship only: true if the object is an independently-"
            "operated company or product, genuinely separate from the subject vendor -- false "
            "if the object is the subject vendor's own product, a sub-brand, a division, or a "
            "product it acquired, even when it's marketed under a completely different name "
            "(e.g. a health-tech platform vendor's own acquired care-management product is not "
            "a third-party integration partner). Also false when the quote uses first-person "
            "pronouns ('us', 'our', 'we') on the vendor's own page to say the object became "
            "part of the vendor -- this holds even when the object itself is explicitly named "
            "(e.g. 'the acquisition of Odoro gives us features...' on the vendor's own blog: "
            "Odoro is named, but 'us' is the vendor speaking about itself, so Odoro is now "
            "part of the vendor -- not a separate partner)."
        )
    )
    is_customer_facing_integration: bool = Field(
        description=(
            "For an Integrates relationship only: true if the quote describes an actual "
            "product-to-product integration a customer would use -- INCLUDING consuming a "
            "third party's API when that specific system is itself the point (e.g. syncing "
            "patient records via a named EHR's FHIR API is a real integration: which EHR it "
            "talks to is exactly what a customer cares about). False if the object is "
            "general-purpose, swappable infrastructure the subject vendor built its own "
            "product on top of and could replace with any equivalent provider without "
            "changing what the product does for a customer (a cloud host, a generic LLM/AI "
            "provider, a generic database), even when described with partnership language "
            "like 'collaboration with' or 'leveraging'. The test: does the object's SPECIFIC "
            "identity matter to a customer, or is it interchangeable plumbing? (e.g. a "
            "clinical-AI vendor leveraging a cloud provider's platform to build its own "
            "summarization feature is backend infrastructure -- which cloud provider is "
            "irrelevant to what the feature does; a records product syncing with a named "
            "EHR via its API is a real integration -- which EHR it connects to is the "
            "entire point). Also false if the object is an AI coding assistant or IDE (e.g. "
            "Cursor, VS Code, Claude Code, Codex, Windsurf, GitHub Copilot) that developers "
            "use to write code against the subject vendor's own API/SDK/MCP server -- that's "
            "a developer's build-time tool, not a product a customer's clinical/operational "
            "workflow depends on, even when the quote describes real, shipped MCP/API "
            "support for it."
        )
    )
    is_specific_named_entity: bool = Field(
        description=(
            "For an Integrates relationship only: true if the object is a specific, real, "
            "named product or company -- false if it's a generic product category or system "
            "type rather than a specific product (e.g. 'EHR/PMS', 'practice management "
            "system', 'scheduler', 'EHRs') or an anonymized description with no real name "
            "given. A generic category is not a specific named integration partner even when "
            "the quote genuinely describes the subject vendor connecting to something of that "
            "type."
        )
    )
    page_frames_them_as_users: bool = Field(
        default=True,
        description=(
            "For an Adopts relationship only: true when the page's own title, heading or "
            "opening declares the organizations it lists to be using this product. False when "
            "the list belongs to someone else -- a consultancy's clients, a directory's "
            "coverage, sponsors, award winners -- so the organization appears beside the "
            "product without the page saying it uses it."
        ),
    )
    subject_is_the_claimant: bool = Field(
        # No default, unlike nothing else in this model. A default of True meant
        # "the subject is the claimant", so a response that simply omitted the
        # field skipped the rejection below -- fail-open on the one check that
        # exists to catch a third party's own list.
        description=(
            "False when the page belongs to some other company and the claim is written in "
            "the first person -- the claim is then that company's, and the subject is merely "
            "one of the things being named. Also false when the subject appears only as one "
            "entry in a list the page owner is describing."
        ),
    )
    reason: str = Field(
        default="",
        description=(
            "One or two sentences in plain language saying why you answered as you did. "
            "Name the exact words in the quote that convinced you, and say who those words "
            "are about. If you answered true above, state which phrase states the "
            "relationship between these two specific products."
        ),
    )
    relationship_shape_matches_profile: bool = Field(
        description=(
            "Applies to both Adopts and Integrates relationships: true if the extracted "
            "relation type matches how the quote actually describes the relationship. Adopts "
            "fits when a named clinic/practice/health system is plainly adopting, "
            "implementing, or using the OTHER product for its own clinical or operational "
            "workflow -- a genuine end-customer relationship, even when the quote also uses "
            "technical words like 'integration' or 'connectivity' to describe how they use it. "
            "Integrates fits only when the quote describes a genuine peer-to-peer technical "
            "connection between two independently-operated software products, with neither "
            "side described as a named clinic/practice/health system adopting the other for "
            "patient-facing use. False if the extracted relation type contradicts this shape "
            "-- e.g. a named practice plainly adopting a product for itself, extracted under "
            "Integrates instead of Adopts; or a genuine software/EHR product described as "
            "being integrated with, extracted under Adopts as if it were a customer "
            "organization."
        )
    )


async def semantic_rejection_reason(
    assertion: Assertion,
    *,
    evidence_context: str = "",
    target_name: str = "",
    source_url: str = "",
    chat_model=None,
) -> str | None:
    """Async, model-backed counterpart to `grounding_rejection_reason` -- callers should only
    invoke this after the assertion has already passed that cheap check. Returns None (accept)
    when `chat_model` is omitted, so existing callers/tests without one are unaffected.

    `target_name` is optional and defaults to "" -- passed straight into the prompt as
    `target_product_name` (aliases not wired to a real per-vendor data source yet, same
    caveat as extraction's target injection, so passed empty). Existing prompts that don't
    reference `{{target_product_name}}` at all (customers' semantic check, as of this
    change) render it as an unused variable -- a no-op, not a behavior change. This exists
    so a prompt that DOES reference it (e.g. integrations', to recognize a sub-brand
    variant like "Suki Platform" as the same target) can opt in without a second
    function signature."""
    if chat_model is None:
        return None
    prompt = await _load_semantic_check_prompt(assertion.relation)
    if prompt is None:
        return None
    # evidence_context gives the model the isolated source subsection around the
    # quote (see _semantic_evidence_context) -- without it the prompt's
    # {{evidence_context}} slot renders empty and the model judges off the bare
    # quote alone, with no surrounding page context to confirm a logo-wall/
    # testimonial attribution actually supports adoption.
    built = prompt.build(
        quote=assertion.quote,
        subject=assertion.subject,
        subject_type=assertion.subject_type,
        relation=assertion.relation,
        object=assertion.object,
        object_type=assertion.object_type,
        evidence_context=evidence_context,
        target_product_name=target_name,
        target_product_aliases="",
        source_url=source_url,
    )
    structured_model = chat_model.with_structured_output(
        _SemanticVerdict, method="function_calling"
    )
    # ELI-1304: only Integrates gets voted -- that's where the flicker was observed;
    # customers (Adopts) stays the original single call.
    # ELI-1304 scoped voting to Integrates because that is where the flicker
    # was observed, not because Adopts was shown to be steadier. It is the same
    # close judgment, by the same model, on the same kind of evidence, and on a
    # single call it had no protection at all.
    votes = SEMANTIC_CHECK_VOTES
    # return_exceptions=True (caught by review): the plain default propagates the
    # first exception immediately and leaves the other in-flight calls orphaned
    # (unawaited, their eventual result/exception never retrieved) instead of voting
    # on whichever of the N calls actually succeeded. Only re-raise (preserving
    # full_rejection_reason's existing fail-open behavior) when every single vote
    # failed -- a real systemic issue, not one flaky call.
    outcomes = await asyncio.gather(
        *(structured_model.ainvoke(built["messages"]) for _ in range(votes)),
        return_exceptions=True,
    )
    successes = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
    if not successes:
        raise outcomes[0]
    verdicts = [_SemanticVerdict.model_validate(result) for result in successes]
    verdict = _SemanticVerdict(
        is_named_organization=_majority([v.is_named_organization for v in verdicts]),
        relationship_actually_happened=_majority(
            [v.relationship_actually_happened for v in verdicts]
        ),
        relationship_claim_supported=_majority([v.relationship_claim_supported for v in verdicts]),
        is_third_party_vendor=_majority([v.is_third_party_vendor for v in verdicts]),
        is_customer_facing_integration=_majority(
            [v.is_customer_facing_integration for v in verdicts]
        ),
        is_specific_named_entity=_majority([v.is_specific_named_entity for v in verdicts]),
        relationship_shape_matches_profile=_majority(
            [v.relationship_shape_matches_profile for v in verdicts]
        ),
        subject_is_the_claimant=_majority([v.subject_is_the_claimant for v in verdicts]),
        page_frames_them_as_users=_majority([v.page_frames_them_as_users for v in verdicts]),
    )
    # is_named_organization only means something for a HealthSystem subject (customers
    # profile) -- an integrations edge's subject is a Product, which is never a hospital/
    # clinic/practice, so enforcing this field there would reject every valid integration.
    # ELI-1544: the quote's claim belongs to whoever owns the page. On a third
    # party's own integrations list, "we've interfaced with X" is that company
    # speaking -- the target is just another name on the list.
    #
    # Integrates only, gated like the integration-specific fields below (Codex
    # review): a customers quote is routinely the vendor speaking about its own
    # deployment ("we deployed Product at Mercy"), where the subject -- the
    # health system -- is never the speaker. Ungated, that rejects every valid
    # Adopts edge, and the customers prompt doesn't describe this field at all.
    if assertion.relation == "Integrates" and not verdict.subject_is_the_claimant:
        return "subject_not_the_claimant"
    # ELI-1549: the customers counterpart. The extraction prompt already describes
    # this rule -- a page's own framing can establish its listed organizations as
    # adopters, a sponsor or award list cannot -- but is told to defer the call,
    # and until now nothing downstream made it. Adopts only: on an integrations
    # page there is no list of organizations to own.
    if assertion.relation == "Adopts" and not verdict.page_frames_them_as_users:
        return "page_does_not_frame_them_as_users"
    if assertion.subject_type == "HealthSystem" and not verdict.is_named_organization:
        return "subject_not_real_organization"
    if not verdict.relationship_actually_happened:
        return "relationship_not_actually_happened"
    if not verdict.relationship_claim_supported:
        return "relationship_claim_not_supported"
    # ELI-1304: is_third_party_vendor only means something for Integrates -- gated the
    # same way as is_named_organization above. Catches same-vendor cases the cheap
    # word-overlap check in grounding_rejection_reason can't (acquisitions/sub-brands
    # with no lexical overlap with the target name, e.g. ExampleModule for ExampleCare).
    if assertion.relation == "Integrates" and not verdict.is_third_party_vendor:
        return "integration_partner_not_third_party"
    # ELI-1305: is_customer_facing_integration only means something for Integrates --
    # gated the same way as is_third_party_vendor above. Catches the case that field
    # can't: the object is a genuine third-party company, but the quote describes the
    # vendor building its own product on top of it as backend infrastructure (a cloud
    # host, an LLM provider), not a customer-facing product-to-product integration.
    if assertion.relation == "Integrates" and not verdict.is_customer_facing_integration:
        return "integration_partner_is_backend_infrastructure"
    # ELI-1314: is_specific_named_entity only means something for Integrates -- gated the
    # same way as the other Integrates-only fields above. Catches a real gap: nothing
    # currently checks whether an integration's object is a specific named product versus
    # a generic category ("EHR/PMS", "practice management system") -- the customers-side
    # equivalent, is_named_organization, only ever gated the subject, not this side.
    if assertion.relation == "Integrates" and not verdict.is_specific_named_entity:
        return "integration_partner_not_specific_entity"
    # ELI-1315: relationship_shape_matches_profile applies to BOTH relation types,
    # unlike every field above it -- it catches a relationship extracted under the
    # wrong profile entirely (a named customer practice plainly adopting the vendor's
    # product, extracted under Integrates instead of Adopts; or a genuine software/EHR
    # product extracted under Adopts as if it were a customer organization).
    if not verdict.relationship_shape_matches_profile:
        return "relationship_shape_contradicts_profile"
    return None


def _with_extra_context(context: str, extra: str, reasoning: str = "") -> str:
    """Append what the referent found to the subsection the semantic check
    judges against (ELI-1544). Labelled, so the model can tell it apart from
    the page subsection itself.

    Two parts, because they are different kinds of thing. `extra` is the page's
    own sentence, located and copied so no model could add words to it.
    `reasoning` is the referent's argument for why it settles the relation, and
    it is the part that carries a case no single sentence can -- the referent
    exists precisely because a page can establish a relation across two
    sentences or through its own structure. The check never sees the whole
    page, only a subsection matched to the quote; sent the sentence alone it
    has to re-derive the connection the referent already worked out.

    Measured on ELI-1544's 113 pages, three runs: passing the argument added
    about eight correct relations per run with an identical count of incorrect
    ones -- 7 and 7, 8 and 8, 6 and 6.

    Labelled as a claim rather than as evidence, so the check weighs it as an
    argument instead of reading it as something the page says."""
    if not extra.strip() and not reasoning.strip():
        return context
    out = context
    if extra.strip():
        out = f"{out}\n\nElsewhere on the same page:\n{extra.strip()}"
    if reasoning.strip():
        out = (
            f"{out}\n\nAn earlier step read the whole page and argued this "
            f"establishes the relation:\n{reasoning.strip()}"
        )
    return out


async def full_rejection_reason(
    assertion: Assertion,
    content: str,
    *,
    profile: str = "",
    target_name: str = "",
    source_url: str = "",
    assume_grounded: frozenset[str] = frozenset(),
    extra_context: str = "",
    extra_reasoning: str = "",
    chat_model=None,
) -> str | None:
    """The one complete grounding check -- deterministic checks, then the semantic
    check if those pass. Caught by review (Macroscope): with the cue regexes gone,
    `grounding_rejection_reason` alone silently accepts a bare co-mention (two named
    entities with no stated relationship between them), and its replacement lives
    only in the separate, opt-in `semantic_rejection_reason` -- a caller using only
    the synchronous function no longer gets a complete answer without knowing to
    also call the async one. This is the single entry point that always does both,
    so there's no way to accidentally run just the incomplete half.

    A transient model error (timeout, malformed structured output) during the
    semantic half fails open (no rejection) rather than propagating -- it must not
    abort the whole ingestion graph before persist/finish_extraction run; falls back
    to the deterministic-only result this check sits on top of. The deterministic
    half is pure and synchronous, so it's never expected to raise and isn't caught."""
    reason = grounding_rejection_reason(
        assertion,
        content,
        profile=profile,
        target_name=target_name,
        assume_grounded=assume_grounded,
    )
    if reason:
        return reason
    try:
        return await semantic_rejection_reason(
            assertion,
            evidence_context=_with_extra_context(
                _semantic_evidence_context(
                    assertion.quote, content, endpoints=(assertion.subject, assertion.object)
                ),
                extra_context,
                extra_reasoning,
            ),
            target_name=target_name,
            source_url=source_url,
            chat_model=chat_model,
        )
    except Exception:
        return None
