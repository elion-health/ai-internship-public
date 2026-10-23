# ELI-1197 — Per-Vendor Pipeline Diagnostics

> Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Objective and diagnostic structure

Model comparisons needed to show where quality was lost: fetching, eligibility, extraction, grounding or evidence availability. A historical notebook applied the same diagnostic sequence to seven products and both customer and integration profiles.

Fetch diagnostics were shared once per product. Each profile then followed golden-set size → grounding funnel and confusion matrix → real precision/recall/F1 → evidence-verified count → ceiling metrics → summary. This separated acquisition limitations from extraction or validation losses instead of reporting only final F1.

The notebook and its embedded outputs are not bundled. This report retains its method and numerical findings without exposing the underlying relationship rows or evidence links.

## Scoring decisions

- **Grounding confusion matrices reported counts, not F1.** Scoring only the candidates a model happened to propose creates a selectable denominator and can conceal missed golden relationships.
- **Raw mentions and deduplicated entities were separate units.** They were not drawn as successive stages of a single funnel unless an actual filtering relationship existed.
- **The evidence-qualified denominator included every golden entry with at least one citation, regardless of domain.** A citation being off-domain did not prove the relationship absent from the vendor site. For Suki, direct search verified 12/14 customers while only 7/14 golden citations were on-domain.
- **Integration scoring used product-name matching.** Brand/product-family alias resolution was explored but not adopted; spelling variants therefore remained a potential scoring or entity-resolution gap.

The term “ceiling” here describes an evidence-qualified evaluation denominator. It is not the stricter first-party extractability ceiling used in ELI-1020. Those metrics should not be compared as if their denominators were identical.

## Fetch-metric correction

The earlier `attempted_fetches = fetched + errored` calculation mixed URL counts with failed-attempt counts. A URL that failed and then succeeded was counted in both components.

A live historical Suki check showed **155 attempted / 1 errored** under the old calculation, versus **154 distinct URLs and 0 currently erroring**. The single retry belonged to an already successful URL.

The revised method used four mutually exclusive, exhaustive URL buckets:

`excluded + errored + blocked + clean == total_urls`

Retries were reported separately rather than folded into those buckets. Content-quality eligibility and content-hash deduplication stages also connected retained-page counts to the smaller extraction-attempt denominator, making losses between fetching and extraction explicit.

## Historical implementation notes and limits

The notebook contained 1.42 MB of output across seven products and two profiles. Historical repository configuration allowed that size and notebook-specific long HTML expressions/top-level await. Those configuration changes and notebook outputs are not part of this public research edition.

The full diagnostic did not solve same-entity spelling variation. Production integrations-scoring wiring was separate work, not an outcome established by this notebook. The report provides a diagnostic framework and the fetch-count correction, not new unreported per-product performance numbers.
