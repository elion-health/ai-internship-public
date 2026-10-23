# ELI-1316 — Pronoun self-reference and own-brand names

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Problem and scope

ELI-1304 did not fully cover references to a target's own acquired products through pronouns, or a parent/sub-brand name extracted as a customer. The larger-crawl audit initially assigned 27 rows to self-reference; rechecking actual evidence and reference data yielded:

| Reclassification | Rows |
|---|---:|
| Definite self-reference errors | 10 |
| Real distinct third parties | 6 |
| Generic or anonymized entities | 10 |
| Real product without the claimed business relationship | 1 |

The ten errors represented seven patterns. Eight test cases covered them, collapsing four near-duplicate rows into two representative variants.

## Changes

1. Extend integration-side `is_third_party_vendor` with a concrete pronoun-based ownership pattern. Abstract first-person wording had not stabilized this boundary.
2. Extend customer-side `is_named_organization` to reject a target's own brand or sub-brand as its customer.
3. Include `target_product_aliases` in the customer prompt. The calling code already passed the variable, but the template did not consume it. Without aliases or contextual ownership cues, a verifier limited to supplied evidence could not identify bare own-brand names.

Validation also exposed a harness error: one case appeared to fail because the test omitted real stored aliases. Passing the aliases corrected that test without changing product logic.

## Historical results

Using `openai/gpt-5.4-mini`, real retained evidence, and stored aliases, all eight cases were tested five times: 40/40 error-case runs were rejected. Two genuine-relationship controls, also tested five times each, yielded 10/10 correct acceptances. Rejections sometimes came from a secondary semantic field rather than the intended field. The suite reported 342 passing tests and no regressions.

## Remaining limitation

An alias list could contain product sub-brands while omitting the parent company itself. Context was sufficient in the tested cases, but complete alias coverage was not established. This report preserves the mechanism and aggregate outcomes without revealing alias inventories or named relationship examples.
