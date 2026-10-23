# ELI-1136 — Extraction Yield Methods and Aggregate Findings

> Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Objective and corpus

Measure pre-grounding output, useful-source rates and relation distribution by page type on a fixed first-party corpus. This used the same nine-product fresh crawl as ELI-1135, with a 200-page limit. Vendor identities and per-product metrics are omitted from this public edition.

The LangGraph ingestion order was `load_source → extract → resolve → verify_grounding → resolve_referents → persist → record_outcome`. Pre-grounding yield concerns proposed assertions, including ones later rejected; it is not a count of verified relationships.

## Method

A read-only historical `vendor_extraction_yield.py` queried `ExtractionRun`, `RetainedSource`, `FrontierUrl` and entities. It made no model calls or writes. First-party scope used frontier origin and `latest_source_id`, rather than a source's frozen entrypoint.

- Assertions were reconstructed from accepted edges plus `grounding_rejections`, using only the latest revision per `(source, profile, extractor_version)`.
- Distinct HealthSystem/Product names were counted at either endpoint position.
- A source was useful if any profile/version proposed at least one assertion.
- The denominator was sources with at least one ExtractionRun, not every fetched source. Eligibility filtering and content-hash deduplication excluded many retained pages before extraction.
- Page types used seven priority-ordered URL-pattern buckets, not content classification.
- Extractor version was a grouping key, allowing later model/prompt comparisons without mixing outputs.

The historical script, corpus and generated charts/metrics are unavailable in this edition. This edition retains methods and qualitative findings only.

## Interpretation

Every product/profile had exactly one extractor version, so this pass established a yield baseline, not a model comparison. High useful-source rates do not establish precision: rejected assertions count as yield.

Retained and attempted counts diverged because eligibility filtering, domain-policy exclusion and content-hash deduplication reduced the extraction corpus. Stale error pages could leave little usable content despite substantial retrieval activity.

Integrations generally produced more assertions than customers. This was consistent with integration-heavy marketing content, but yield alone did not verify those claims. Vendor-level yields and page-type distributions remain private.

## Measurement corrections

- Runs were scoped to `target_id` so a shared source could not import another target's extraction results.
- Primary variant selection used deterministic ordering: source coverage, relation count, then version string.
- One accepted edge counted as one assertion. Splitting stored facts on ` | ` had overcounted quotes containing literal table separators.
- Per-profile numerator/denominator annotations used actual set unions.
- Outcome charts included every recorded outcome rather than only three predefined statuses.

## Limitations

`collapse_edges` merges same-triple assertions, so distinct supporting quotes for one triple are undercounted as one accepted assertion. Page-type analysis uses each profile's primary version only and remains a URL-shape heuristic. “Resources/collateral” does not imply a gated document; URL keywords cannot establish access restrictions. The corpus was freshly reset and crawled, but no second model/prompt variant was measured here.
