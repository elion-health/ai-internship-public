"""Recover referent-distance grounding failures: a name (product or customer)
that is real and on the page, but not in the same evidence span as the quote
grounding checks against (e.g. a testimonial that says "this system" or "she"
instead of the actual name, which only appears elsewhere on the page).

Introduced for first-party pages only (ELI-1056); ELI-1544 extended it to
external pages as well, and ELI-1550 gave the customers case a prompt of its
own for the vendor's own site. See
docs/experiments/eli-1550-firstparty-customers-referent.md and
docs/experiments/eli-1020-discovery-ceiling-eval.md.
"""

from __future__ import annotations

import asyncio
import difflib
import functools
import re

from pydantic import BaseModel

from ai_internship.enrichment.contracts import GroundingRejection
from ai_internship.enrichment.grounding import (
    _contains_endpoint,
    normalize_text,
    quote_is_grounded,
)
from ai_internship.public_assets import load_prompt

# These cue-word lists were replaced by model judgment in the acceptance gate,
# but remain here as a pure text-positioning heuristic for
# _inject_near_other_mentions below, a different job: finding *where* on the
# page to splice a resolved name, not deciding whether to accept an assertion.
_CUSTOMER_CUE = re.compile(
    r"\b(?:adopt\w*|deploy\w*|implement\w*|integrat\w*|launch\w*|powered by|"
    r"partner\w*|roll(?:ed|ing)? out|select\w*|use[sd]?|using|utiliz\w*)\b",
    re.IGNORECASE,
)
_INTEGRATION_CUE = re.compile(
    r"\b(?:combin\w*|connect(?:ed|s)? (?:to|with)|embed\w*|implement\w*|interfac\w*|"
    r"integrat\w*|interoperab\w*|partner\w*|sync\w*)\b",
    re.IGNORECASE,
)
_CUE_BY_RELATION = {"Adopts": _CUSTOMER_CUE, "Integrates": _INTEGRATION_CUE}

FIRST_PARTY_ENTRYPOINT = "first_party_acquisition"

# ELI-1544: this prompt used to be an inline f-string here -- the only prompt in
# the pipeline that wasn't editable without a code change. Same text, same three
# variables, now versioned in Braintrust alongside the extraction and semantic-
# check prompts.
# One prompt per relation type. They ask different questions -- whether two
# products are connected, versus whether an organisation uses a product -- and
# the wording that works for one scores badly on the other.
REFERENT_PROMPT_SLUGS = {
    "Integrates": "referent-solution-0cd9",
    "Adopts": "referent-solution-customers-7f21",
}

# ELI-1550: a prompt written for the vendor's own site beats the shared one
# there, 0.975 against 0.955 over ten runs
# (docs/experiments/eli-1550-firstparty-customers-referent.md).
#
# Routed rather than merged, because each was only measured on first-party
# pages: the shared slugs also serve external ones, which are most of the
# traffic.
#
# The integrations one scores 0.927 against 0.782 at this step, but four
# full-pipeline runs put it at +3, +1, +0 and +0 edges: the five-vote check
# downstream was already rejecting what the shipped prompt wrongly accepted.
# Strictly better here, costs nothing, expect the graph not to move
# (docs/experiments/eli-local-firstparty-integrations-referent.md).
FIRST_PARTY_REFERENT_PROMPT_SLUGS = {
    "Adopts": "referent-solution-customers-firstparty-1eb4",
    "Integrates": "referent-solution-integrations-firstparty-71e0",
}


def referent_slug(relation: str, origin: str | None) -> str | None:
    """The referent prompt for this relation on a page of this origin, or None
    when the relation has no referent prompt at all."""
    if origin == FIRST_PARTY_ENTRYPOINT and relation in FIRST_PARTY_REFERENT_PROMPT_SLUGS:
        return FIRST_PARTY_REFERENT_PROMPT_SLUGS[relation]
    return REFERENT_PROMPT_SLUGS.get(relation)


async def _load_referent_prompt(relation: str, origin: str | None = None):
    slug = referent_slug(relation, origin)
    if slug is None:
        return None
    return await asyncio.get_running_loop().run_in_executor(
        None,
        functools.partial(load_prompt, slug=slug),
    )


# Matches the similarity bar quote_is_grounded already uses -- a quote the
# extractor produced is routinely near-verbatim rather than byte-identical to
# the raw markdown (e.g. a page's "[OncoEMR®](url)" becomes plain "OncoEMR®"
# in the model's quote), so locating it for injection needs the same
# tolerance grounding itself already applies, not an exact match.
_QUOTE_LOCATE_MIN_SIMILARITY = 0.75

# Locating where a justification appears in content for tier-2 validation
# uses the same similarity threshold as quote grounding elsewhere. A small
# amount of context is included around the match to ensure we capture the
# surrounding sentence structure where the name might appear.
_SPAN_CONTEXT_CHARS = 50


def _locate_source_span(content: str, quote: str) -> str | None:
    """Find the actual source text span in `content` where `quote` matches.

    Returns the matching span from the source content (not the quote itself),
    or None if no sufficiently similar match is found. Includes a small amount
    of surrounding context to capture sentence structure.
    """
    normalized_quote = normalize_text(quote)
    normalized_content = normalize_text(content)
    if not normalized_quote:
        return None

    # Exact substring match in normalized content
    exact_idx = normalized_content.find(normalized_quote)
    if exact_idx != -1:
        start = max(0, exact_idx - _SPAN_CONTEXT_CHARS)
        end = min(len(normalized_content), exact_idx + len(normalized_quote) + _SPAN_CONTEXT_CHARS)
        return normalized_content[start:end]

    # Fuzzy match using same approach as quote_is_grounded
    matcher = difflib.SequenceMatcher(None, normalized_quote, normalized_content, autojunk=False)
    blocks = [block for block in matcher.get_matching_blocks() if block.size > 0]
    if not blocks:
        return None
    matched_chars = sum(block.size for block in blocks)
    if matched_chars / len(normalized_quote) < _QUOTE_LOCATE_MIN_SIMILARITY:
        return None

    # Extract the span from the first to last matching block in content
    first_block = blocks[0]
    last_block = blocks[-1]
    start = max(0, first_block.b - _SPAN_CONTEXT_CHARS)
    end = min(len(normalized_content), last_block.b + last_block.size + _SPAN_CONTEXT_CHARS)
    return normalized_content[start:end]


_SUBJECT_CODES = frozenset({"unnamed_subject", "subject_not_in_evidence"})
_OBJECT_CODES = frozenset({"unnamed_object", "object_not_in_evidence"})
MISSING_NAME_CODES = frozenset(_SUBJECT_CODES | _OBJECT_CODES)


class ReferentResolution(BaseModel):
    """ELI-1544: field order is deliberate. With function calling the model fills
    these in order, so it writes its argument before committing to a verdict --
    the shape that scored 94/98 on the fixed case set, where asking for the
    verdict first scored 34.

    No `resolved_name` any more: extraction supplies both product names, and
    asking the model to re-spell one "as the page writes it" was what the old
    version failed on.
    """

    reason: str | None = None
    integrated: bool | None = None
    proof_sentence: str | None = None


# The resolution call is plain non-deterministic: the same prompt against the
# same page can return a correct, verifiable name on one attempt and a bare
# "unknown" on the next. Retrying a few times before giving up costs little
# and matches the replication rigor this project already holds every other
# grounding-adjacent change to (see ELI-1056/1020.3).
_TIER2_MAX_ATTEMPTS = 3

# Re-extraction on the injected content is a single live LLM call too, and
# it's just as non-deterministic -- observed live, the exact same injected
# page recovered a grounded edge on most attempts but returned zero
# assertions outright on others. One call isn't enough to trust either.
REEXTRACTION_MAX_ATTEMPTS = 3


def missing_endpoint_type(rejection: GroundingRejection) -> str | None:
    """The label (e.g. "HealthSystem", "Product") of whichever endpoint this
    rejection is missing a usable name for, or None if the code isn't one of
    ours to handle."""
    assertion = rejection.assertion
    if rejection.code in _SUBJECT_CODES:
        return assertion.subject_type
    if rejection.code in _OBJECT_CODES:
        return assertion.object_type
    return None


def missing_endpoint_field(rejection: GroundingRejection) -> str | None:
    """Which `Assertion` field ("subject" or "object") this rejection is
    missing a usable name for, or None if the code isn't one of ours."""
    if rejection.code in _SUBJECT_CODES:
        return "subject"
    if rejection.code in _OBJECT_CODES:
        return "object"
    return None


async def confirm_relation(
    *,
    subject: str,
    object_name: str,
    relation: str,
    quote: str,
    unnamed: str,
    content: str,
    source_url: str,
    chat_model,
    origin: str | None = None,
) -> tuple[str, str] | None:
    """Second look at a relation whose quote names only one of its two sides.

    The quote that got here is by definition too weak on its own, so this reads
    the whole page and asks whether the relation really holds -- whether two
    products are connected, or whether an organisation uses a product, depending
    on `relation`. Returns the page's own copy of the sentence that proves it,
    plus the model's reasoning, or None.

    `unnamed` is whichever side the quote doesn't name; the integrations prompt
    uses it to say what is actually missing.

    The sentence is verified against the page before being returned: a model
    retyping a sentence can quietly add words, and this one is passed on to the
    semantic check as evidence.
    """
    if chat_model is None:
        return None
    prompt = await _load_referent_prompt(relation, origin)
    if prompt is None:
        return None
    messages = prompt.build(
        subject=subject,
        object=object_name,
        quote=quote,
        # Integrations only. That prompt says which side the quote failed to
        # name; the customers one reads the page without being told, which
        # measured better than anchoring it on a quote already known to be weak.
        unnamed_product=unnamed,
        named_product=object_name if unnamed == subject else subject,
        source_url=source_url,
        page_content=content,
    )["messages"]
    structured_model = chat_model.with_structured_output(
        ReferentResolution, method="function_calling"
    )
    for _ in range(_TIER2_MAX_ATTEMPTS):
        parsed = ReferentResolution.model_validate(await structured_model.ainvoke(messages))
        if not parsed.integrated:
            return None
        sentence = parsed.proof_sentence
        if not sentence or sentence.strip().lower() == "unknown":
            continue
        if not quote_is_grounded(sentence, content):
            continue
        span = _locate_source_span(content, sentence)
        if not span:
            continue
        return span, parsed.reason or ""
    return None


_MARKDOWN_LINK_CLOSER = re.compile(r"^\]\([^)]*\)")


def _skip_trailing_markdown_link(content: str, end: int) -> int:
    """If `end` lands inside an unclosed markdown link -- e.g. right after the
    visible link text "OncoEMR®" but before its "](url)" -- move past the
    closer instead. Splicing text in the middle of "[OncoEMR®](url)" breaks
    the link syntax; observed live on Pontchartrain's real page."""
    match = _MARKDOWN_LINK_CLOSER.match(content[end:])
    return end + match.end() if match else end


def _locate_quote_end(content: str, quote: str) -> int | None:
    """Where `quote` ends within `content`, tolerating the same near-verbatim
    gap quote_is_grounded tolerates, rather than requiring an exact match.
    Returns None if nothing similar enough is found.

    To prevent false matches from scattered character blocks across distant,
    unrelated sections, this requires a contiguous cluster of matching blocks
    within a local window (roughly 2x the quote length) to meet the similarity
    threshold, rather than summing all blocks globally.
    """
    exact_index = content.find(quote)
    if exact_index != -1:
        return _skip_trailing_markdown_link(content, exact_index + len(quote))
    matcher = difflib.SequenceMatcher(None, quote, content, autojunk=False)
    blocks = [block for block in matcher.get_matching_blocks() if block.size > 0]
    if not blocks:
        return None

    # Find the best contiguous cluster of blocks within a local window.
    # The window allows for formatting differences (e.g., markdown links,
    # whitespace) while preventing matches from scattered distant sections.
    # A window of 2x the quote length accommodates typical near-verbatim gaps.
    max_window = len(quote) * 2
    min_matched = int(len(quote) * _QUOTE_LOCATE_MIN_SIMILARITY)

    best_end = None
    best_matched = 0

    for start_idx, anchor in enumerate(blocks):
        cluster_matched = anchor.size
        cluster_end_block = anchor

        # Extend cluster to include subsequent blocks within the window
        for block in blocks[start_idx + 1 :]:
            # Check if this block is within the local window of the anchor
            # based on position in content (b field)
            if block.b - anchor.b > max_window:
                break
            cluster_matched += block.size
            cluster_end_block = block

        if cluster_matched >= min_matched and cluster_matched > best_matched:
            best_matched = cluster_matched
            best_end = cluster_end_block.b + cluster_end_block.size

    if best_end is None:
        return None
    return _skip_trailing_markdown_link(content, best_end)


def _inject_near_other_mentions(content: str, name: str, relation: str) -> str:
    """Beyond the one quote that happened to get rejected, splice the resolved
    name next to every other paragraph on the page carrying the same kind of
    relationship cue and not already mentioning it. A page like an interview
    transcript can restate the same relationship many different ways (e.g.
    several "Oubre: ..." lines, each a valid quote for the same fact) --
    re-extraction isn't guaranteed to pick the one specific sentence we
    already know failed, so anchoring only that one sentence isn't enough.

    The name is inserted right after the cue word itself, not at the end of
    the paragraph -- a paragraph can be one long multi-clause sentence, and a
    quote the model re-extracts often covers only the clause containing the
    cue, not the whole paragraph. Observed live: appending at the paragraph's
    end left the anchor past where the model's quote actually stopped."""
    cue = _CUE_BY_RELATION.get(relation)
    if cue is None:
        return content
    paragraphs = content.split("\n\n")
    for index, paragraph in enumerate(paragraphs):
        match = cue.search(paragraph)
        if not match:
            continue
        if _contains_endpoint(normalize_text(paragraph).lower(), name):
            continue
        insert_at = match.end()
        paragraphs[index] = f"{paragraph[:insert_at]} (referring to {name}){paragraph[insert_at:]}"
    return "\n\n".join(paragraphs)


def inject_resolved_names(content: str, injections: list[tuple[str, str, str]]) -> str:
    """Splice each resolved name directly into its quote's location in the page
    content, adjacent to the ambiguous reference -- mirrors the manually
    validated fix (inserting the name next to the pronoun), so a quote
    re-extracted from this modified text carries the name intrinsically rather
    than relying on a detached note the model may not connect back to it.
    `injections` is a list of (quote, name, relation) tuples."""
    for quote, name, _relation in injections:
        if not quote:
            continue
        end = _locate_quote_end(content, quote)
        if end is None:
            continue
        content = f"{content[:end]} (referring to {name}){content[end:]}"
    return content
