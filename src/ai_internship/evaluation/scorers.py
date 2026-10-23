"""Customer and integration evaluation scorers."""

import re
import unicodedata

# ELI-1306: maps every curly/smart apostrophe variant to a plain straight one,
# so "St. Mary's" (model output) and "St. Mary's" (golden, or vice versa) are
# never silently double-counted as an FP and an FN instead of one TP. U+201B
# added per Macroscope review (PR #60) -- same reversed-comma shape, just a
# less common encoding of it. U+2032 (prime) deliberately kept even though
# it's technically a distinct Unicode character from an apostrophe -- real
# scraped content uses it as a straight-apostrophe substitute (the original
# reason it's here), and no real case of it meaning something else (e.g. a
# foot/minute unit) has turned up in this dataset.
_CURLY_APOSTROPHES = "‘’ʼ′‛"

# Katakana/hiragana voiced and semi-voiced sound marks -- these are a
# *canonical* decomposition (NFD splits "ガ" into "カ" + this mark), not a
# decorative accent, so blanket-stripping every combining mark collapses
# genuinely different words together (Macroscope review, PR #60).
_PRESERVED_COMBINING_MARKS = {"゙", "゚"}


def _is_variation_selector(c: str) -> bool:
    """True for a variation selector (the invisible codepoint that follows a
    base character to pick a specific glyph, e.g. text- vs emoji-style) --
    these have combining class 0, so `unicodedata.combining()` doesn't catch
    them, but they're just as invisible-yet-not-actually-gone as a combining
    mark: a stray one riding along on a curly apostrophe or a CJK character
    survives normalization untouched and silently breaks what would
    otherwise be an exact match (Macroscope review, PR #60)."""
    return "\ufe00" <= c <= "\ufe0f" or "\U000e0100" <= c <= "\U000e01ef"


def _normalize(s: str | None) -> str:
    if not s:
        return ""

    s = str(s).strip().lower()
    # strip accents/diacritics -- NFD decomposes "é" into "e" + a combining
    # accent mark, which we then drop; leaves plain ASCII letters unaffected.
    # Deliberately NFD, not NFKD: NFKD's *compatibility* decomposition also
    # folds semantically distinct symbols into plain letters/digits ("™" ->
    # "TM", "Ⅰ" -> "I", "①" -> "1"), which wrongly credited those as matches
    # (Macroscope review, PR #60) -- NFD's canonical-only decomposition
    # leaves those untouched while still handling every real accented-letter
    # case this ticket exists for.
    s = "".join(
        c
        for c in unicodedata.normalize("NFD", s)
        if (not unicodedata.combining(c) and not _is_variation_selector(c))
        or c in _PRESERVED_COMBINING_MARKS
    )
    for curly in _CURLY_APOSTROPHES:
        s = s.replace(curly, "'")
    # lowercase, remove all spaces and hyphens — handles near-matches like
    # "eVisitNB" vs "eVisit NB", "MD Connected" vs "MDConnected", etc.
    s = re.sub(r"[-\s]+", "", s)
    return s


def _get_customer_match_name(customer: dict) -> str:
    # Reads normalized_organization if present, falling back to raw name --
    # but nothing in this repo actually sets normalized_organization today,
    # so in practice this always falls back to raw "name".
    return _normalize(customer.get("normalized_organization") or customer.get("name", ""))


def _multilabel_recall(expected: list, predicted: list) -> float:
    if not expected and not predicted:
        return 1.0
    if not expected:
        return 1.0
    if not predicted:
        return 0.0
    return len(set(expected) & set(predicted)) / len(set(expected))


def _multilabel_precision(expected: list, predicted: list) -> float:
    if not expected and not predicted:
        return 1.0
    if not predicted:
        return 0.0
    if not expected:
        return 0.0
    return len(set(expected) & set(predicted)) / len(set(predicted))


def _get_expected_customers(expected: dict) -> list:
    if isinstance(expected, dict):
        return expected.get("customers", [])
    return getattr(expected, "customers", [])


def customers_recall(input, output, expected) -> float:
    """% of ground-truth customers found in agent output."""
    expected_customers = _get_expected_customers(expected)
    if not expected_customers:
        return 1.0
    if not output:
        return 0.0
    output_customers = output.get("customers", []) if isinstance(output, dict) else []
    expected_names = {_normalize(c.get("name", "")) for c in expected_customers}
    output_names = {_get_customer_match_name(c) for c in output_customers}
    return _multilabel_recall(list(expected_names), list(output_names))


def customers_precision(input, output, expected) -> float:
    """% of agent output customers that appear in ground truth."""
    expected_customers = _get_expected_customers(expected)
    if not output:
        return 0.0
    output_customers = output.get("customers", []) if isinstance(output, dict) else []
    if not output_customers:
        return 0.0
    expected_names = {_normalize(c.get("name", "")) for c in expected_customers}
    output_names = {_get_customer_match_name(c) for c in output_customers}
    return _multilabel_precision(list(expected_names), list(output_names))


def customers_f1(input, output, expected) -> float:
    """Harmonic mean of recall and precision."""
    r = customers_recall(input, output, expected)
    p = customers_precision(input, output, expected)
    if p + r == 0:
        return 0.0
    return 2 * (p * r) / (p + r)


def _get_integration_match_name(integration: dict) -> str:
    # No "normalized_product" enrichment field exists, and
    # customers' normalized_organization isn't a real distinction
    # either (nothing in this repo ever sets it, so _get_customer_match_name
    # always falls back to raw "name" in practice too). Product entities
    # aren't resolved to a canonical name anywhere in the pipeline today, so
    # this is raw "name" only. Kept as its own function, mirroring
    # _get_customer_match_name, so a future normalized-product field slots
    # in here without touching the recall/precision functions below.
    return _normalize(integration.get("name", ""))


def _get_expected_integrations(expected: dict) -> list:
    if isinstance(expected, dict):
        return expected.get("integrations", [])
    return getattr(expected, "integrations", [])


def integrations_recall(input, output, expected) -> float:
    """% of ground-truth integrations found in agent output."""
    expected_integrations = _get_expected_integrations(expected)
    if not expected_integrations:
        return 1.0
    if not output:
        return 0.0
    output_integrations = output.get("integrations", []) if isinstance(output, dict) else []
    expected_names = {_normalize(i.get("name", "")) for i in expected_integrations}
    output_names = {_get_integration_match_name(i) for i in output_integrations}
    return _multilabel_recall(list(expected_names), list(output_names))


def integrations_precision(input, output, expected) -> float:
    """% of agent output integrations that appear in ground truth."""
    expected_integrations = _get_expected_integrations(expected)
    if not output:
        return 0.0
    output_integrations = output.get("integrations", []) if isinstance(output, dict) else []
    if not output_integrations:
        return 0.0
    expected_names = {_normalize(i.get("name", "")) for i in expected_integrations}
    output_names = {_get_integration_match_name(i) for i in output_integrations}
    return _multilabel_precision(list(expected_names), list(output_names))


def integrations_f1(input, output, expected) -> float:
    """Harmonic mean of recall and precision."""
    r = integrations_recall(input, output, expected)
    p = integrations_precision(input, output, expected)
    if p + r == 0:
        return 0.0
    return 2 * (p * r) / (p + r)
