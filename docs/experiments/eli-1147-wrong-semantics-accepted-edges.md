# ELI-1147 — Wrong-Semantics Accepted Edges

> Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Problem and scope

The ELI-1143 audit found approximately 8/80 accepted edges whose text was real but did not support the asserted semantics. Persons or places were typed as HealthSystems, generic settings were treated as named organizations, and hypothetical or cancelled plans were treated as completed relationships. The separate 13/80 fabrication bucket was addressed by ELI-1146.

Quote similarity, endpoint presence and cue words cannot determine whether a stated event occurred. Illustrative fictional example: “Fictional Cedar Clinic hoped to implement Fictional Willow EHR, but the plan was cancelled” contains the expected words without establishing adoption.

## Implementation

Two layers were added after the existing inexpensive checks:

1. **Explicit person markers.** `_is_individual_name` rejected HealthSystem subjects carrying title prefixes or credential suffixes. It avoided a generic “two capitalized words” rule, which would also reject organization names. The rejection code was `subject_is_individual`.
2. **Semantic validation.** `semantic_rejection_reason` made an async structured-output model call to judge entity type and whether the quoted relationship actually happened. It ran at both the main grounding call site and referent retry, gated by `deps.chat_model`; callers without a model skipped this step.

A hosted extraction-prompt change clarified the required organization type. Case-level live checks showed this alone fixed a place-as-organization error but did not consistently prevent person-as-organization extraction, motivating the deterministic backstop. Exact private prompt wording and prompt-store identifiers are omitted.

## Subsequent corrections

- **Profile-aware entity judgment:** the original organization check rejected valid Product–Integrates–Product assertions because a product is not a hospital. The semantic verdict was adjusted to account for relationship/profile type.
- **Model failures:** unhandled semantic-model errors could terminate ingestion before recording an outcome. Error handling was corrected and tested.
- **Relationship support:** `relationship_claim_supported` was added to distinguish actual relationships from co-mentions, affiliations, awards or investment context.
- **Cue regex replacement:** customer/integration cue checks were removed from grounding in favor of semantic relationship judgment. Keyword lists were fragile and costly to maintain across phrasing variations. Historical references to cue helpers elsewhere do not mean source-text injection was restored after ELI-1146.
- Semantic and integration prompts used the same remotely managed prompt mechanism as customer extraction, enabling iteration without a code deployment.

## Verification

Grounding tests checked person titles/credentials and ensured ordinary organization names still passed. The shared test model became schema-aware and tracked referent and semantic calls separately so an added model call did not invalidate unrelated test intent.

The initial change reported **207 tests passed**; subsequent corrections reported **208 passed**. Live case-level semantic checks rejected the generic-setting and cancelled-plan cases and accepted two known-good assertions. Named assertions and source quotes are omitted.

This was not a full-corpus re-extraction. The measured evidence supported specific failure-case behavior, not a new overall precision/recall estimate.

## Cost and limits

The semantic check added one LLM call for each assertion surviving the inexpensive checks. It was bounded to candidates otherwise eligible for persistence but was not free. Prompt changes alone did not enforce type correctness consistently. Boilerplate-induced subject confusion and duplicate-page inflation remained separate issues.
