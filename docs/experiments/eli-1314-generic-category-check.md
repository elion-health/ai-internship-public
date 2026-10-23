# ELI-1314 — Generic categories extracted as named entities

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Problem and diagnosis

The larger-crawl audit found 111 candidates that treated software categories, second-person placeholders, generic calls to action, or anonymized case-study subjects as specific named entities. These spanned seven products and comprised 74 integration and 37 customer cases.

The customer-side `is_named_organization` check existed but did not adequately cover these patterns. Before correction, three real failing cases tested three times each were accepted in all nine runs. Integration objects had no equivalent specific-name check.

## Correction

- Strengthen `is_named_organization` for customer subjects to cover second-person placeholders, generic promotional labels, and anonymous organizational descriptions.
- Add `is_specific_named_entity` for integration objects: the endpoint must identify a particular product or company, not a software category.
- Include the integration field in the existing majority-vote mechanism.
- Synchronize the semantic prompt instructions with the schema descriptions; earlier experiments showed schema-only changes were insufficient.

Generic terms such as EHR/PMS or practice management system illustrate a category, not a dataset relationship. No authentic evidence rows are reproduced.

## Historical verification

All 111 documented cases were tested against `openai/gpt-5.4-mini`, three repeated runs per case. Every case was rejected in all three runs: 111/111 fully rejected, zero intermittent acceptance, zero complete misses. Fifteen genuine named-entity regression cases remained accepted.

This expanded an initial seven-error/three-regression pilot to the entire documented failure set. Some cases were rejected by a combination of the new field and existing checks, so the result establishes final-decision coverage rather than exclusive attribution to the new field. It does not measure full-corpus precision or recall after deployment.
