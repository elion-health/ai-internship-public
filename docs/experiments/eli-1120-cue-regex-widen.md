# ELI-1120 — Widen Customer and Integration Cue Coverage

> Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Problem

`grounding_rejection_reason()` checked quotes for customer or integration cue words before accepting relationships. `CUSTOMER_CUE` recognized fixed phrases “partnered with” and “partnership with,” but missed plurals, present tense and partnership wording without an adjacent “with.” `INTEGRATION_CUE` lacked partnership and implementation language altogether.

The ELI-1143 audit reviewed all 71 `relationship_cue_missing` rejections and found 17 false rejections representing nine distinct relationships. This was a concentrated recall problem rather than evidence that all grounding checks should be relaxed.

## Change

Fixed phrases became the stem `partner\w*`, consistent with existing adoption/deployment/implementation stems. The integration regex also gained `implement\w*`.

```python
CUSTOMER_CUE = re.compile(
    r"\b(?:adopt\w*|deploy\w*|implement\w*|integrat\w*|launch\w*|powered by|"
    r"partner\w*|roll(?:ed|ing)? out|select\w*|use[sd]?|using|utiliz\w*)\b",
    re.IGNORECASE,
)
INTEGRATION_CUE = re.compile(
    r"\b(?:combin\w*|connect(?:ed|s)? (?:to|with)|embed\w*|implement\w*|interfac\w*|"
    r"integrat\w*|interoperab\w*|partner\w*|sync\w*)\b",
    re.IGNORECASE,
)
```

This is historical grounding logic, not a recommendation to restore these checks in the current pipeline.

## Validation

The revised regexes were applied to all 70 FirstHx first-party cue rejections in the audit. That product accounted for 70/71 entries in the bucket; Flatiron OncoEMR and Fold Health contributed no partnership-language misses.

**11/70 previously rejected assertions passed the revised cue check.** This is a gate-level change, not eleven newly verified correct relationships. Historical checks reported 24/24 grounding tests passing and clean lint/format checks. The rejection data is not bundled.

## Precision costs and limitations

Two new false positives were found: a cue matched a word inside an organization's proper name, and an implementation cue appeared in a hypothetical statement. For illustration only, **Fictional Cedar Partners** contains “Partners” without asserting a partnership; “if Fictional Willow had implemented the product” does not establish actual use. These fictional examples are not evidence rows.

Widening the text window would not solve every missing cue: an inspected relationship had no cue word even in its heading-section context. That required semantic interpretation, not a larger regex search span. Soft endorsement wording also remained uncovered.

The findings supported a later semantic reviewer rather than continued one-phrase-at-a-time regex expansion. Subsequent work replaced relationship-cue acceptance checks with model judgment.
