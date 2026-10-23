"""ELI-1306: `_normalize()` strips accents and differing apostrophe/
variation-selector styles, so a real correct match doesn't get silently
double-counted as both an FP and an FN instead of one TP.
"""

from ai_internship.evaluation.scorers import _normalize


def test_normalize_strips_accents():
    """Real case: golden's 'Dôvera' vs the model's 'Dovera'."""
    assert _normalize("Dôvera") == _normalize("Dovera")


def test_normalize_strips_accents_regardless_of_which_side_has_them():
    """Real case: golden's 'Medis' vs the model's 'Médis' -- the accent can be
    on either side, not just golden's."""
    assert _normalize("Medis") == _normalize("Médis")


def test_normalize_treats_curly_and_straight_apostrophes_the_same():
    """Real case: "St. Mary's Healthcare" with a straight apostrophe (golden)
    vs a curly one (model output)."""
    assert _normalize("St. Mary's Healthcare") == _normalize("St. Mary’s Healthcare")


def test_normalize_treats_reversed_comma_quotation_mark_as_apostrophe_too():
    """Macroscope (PR #60 review): U+201B is a less common but real encoding of
    the same reversed-comma apostrophe shape as U+2019 -- not covered by NFD
    decomposition, so it needs its own entry in the curly-apostrophe map."""
    assert _normalize("St. Mary‛s Healthcare") == _normalize("St. Mary's Healthcare")


def test_normalize_does_not_fold_compatibility_symbols_into_plain_text():
    """Macroscope (PR #60 review): NFKD's *compatibility* decomposition folds
    semantically distinct symbols into plain letters/digits ("™" -> "TM", "Ⅰ" ->
    "I", "①" -> "1") -- switched to NFD (canonical-only) so these stay distinct
    from the plain text they were previously silently credited as matching.
    This also resolves the case-dependent flakiness Codex flagged separately
    (Care™ used to match CareTM but not care™), just by making them
    consistently NOT match instead of consistently matching."""
    assert _normalize("Care™") != _normalize("CareTM")
    assert _normalize("Clinic Ⅰ") != _normalize("Clinic I")
    assert _normalize("Clinic ①") != _normalize("Clinic 1")


def test_normalize_preserves_katakana_voiced_sound_mark():
    """Macroscope (PR #60 review): "ガ" (GA) and "カ" (KA) are genuinely
    different words, not an accented/unaccented pair -- NFD's canonical
    decomposition splits "ガ" into "カ" + a combining voiced-sound mark, and
    the blanket `unicodedata.combining` filter was stripping that mark like
    any decorative accent, silently collapsing the two into the same match."""
    assert _normalize("ガ") != _normalize("カ")


def test_normalize_strips_stray_variation_selector():
    """Macroscope (PR #60 review): a variation selector (the invisible
    codepoint that picks a glyph style, e.g. text- vs emoji-style) has
    combining class 0, so it isn't caught by `unicodedata.combining()` -- one
    riding along on a curly apostrophe survived normalization untouched and
    silently broke what should have been an exact match."""
    assert _normalize("Mary’︀s") == _normalize("Mary's")


def test_normalize_still_lowercases_and_strips_spaces_and_hyphens():
    """Regression guard: the original ELI-1120-era behavior must survive."""
    assert _normalize("eVisit NB") == _normalize("eVisitNB")
    assert _normalize("MD Connected") == _normalize("MDConnected")


def test_normalize_handles_none_and_empty_string():
    assert _normalize(None) == ""
    assert _normalize("") == ""
