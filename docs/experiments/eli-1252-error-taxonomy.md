# ELI-1252 — Extraction error taxonomy and intervention estimates

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Scope and baseline

Read-only analysis of seven scoreable products under the historical `openai/gpt-5.6-luna` extractor used retained graph output and reference labels. No new extraction calls or database writes were made. Every accepted-but-unmatched candidate and missed reference relationship was categorized. “False positive” below initially means absent from the reference set, not independently disproven.

| Measure | Value |
|---|---:|
| True positives | 75 |
| Reference-relative false positives | 228 |
| False negatives | 190 |
| Precision | 0.248 |
| Recall | 0.283 |
| F1 | 0.264 |

## Error categories

| FP category | Count |
|---|---:|
| Judged valid but absent from reference | 127 |
| Evidence insufficient | 55 |
| Not a specific named organization | 28 |
| Relationship-type mismatch | 18 |
| Relationship not yet established | 0 |

| FN cause | Count |
|---|---:|
| First-party evidence never fetched | 44 |
| Off-site evidence never fetched | 57 |
| Never fetched; no evidence in reference | 13 |
| Correct page fetched, extraction/grounding failed | 57 |
| Proposed using sitemap content | 8 |
| Proposed from wrong or irrelevant page | 4 |
| Reference-label problem | 7 |

Among 22 proposed-but-rejected rows, three had the actual reference page fetched but failed grounding, eight used sitemap content, four used the wrong page, five had no reference evidence, and two had incorrect reference evidence. These distinctions separate a locally justified rejection from a globally missed relationship.

### Public-sample product breakdown

| Product | Relationship mismatch FP | Nonspecific-entity FP | Thin-evidence FP | Reference-gap FP | Total FP |
|---|---:|---:|---:|---:|---:|
| Suki | 11 | 1 | 15 | 37 | 64 |
| Infermedica | 0 | 6 | 6 | 42 | 54 |

| Product | First-party fetch miss | Off-site evidence | No evidence | Extraction/grounding | Sitemap | Wrong page | Reference issue | Total FN |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Suki | 1 | 2 | 4 | 2 | 0 | 0 | 1 | 10 |
| Infermedica | 1 | 2 | 0 | 9 | 0 | 1 | 0 | 13 |

The aggregate totals retain all seven products. Relationship-level tables and evidence URLs are omitted.

## Eight intervention areas

1. **Recover first-party evidence.** Across the broader actionable set, 45 customer and 35 integration misses had first-party citations. This includes cases proposed from an unsuitable source, not just the 44 never-fetched rows above. Three already used the correct page and required grounding changes; the other 77 were modeled as recoverable through better evidence acquisition.
2. **Evaluate external discovery.** The broader off-site set contained 70 customer and nine integration citations. Increasing first-party crawl depth cannot reach these. External-source scope introduces its own source-quality and cost tradeoffs.
3. **Resolve unsupported reference entries.** Twenty-six entries lacked citations; five independently matched extracted evidence, leaving 21 unverifiable misses. Removing an unsupported reference entry reduces FN without creating a TP.
4. **Exclude sitemap and robots content from extraction.** Twenty-five accepted candidates used machine-readable URL lists as evidence. A URL string can pass literal grounding while proving no relationship. Discovery may use a sitemap, but extraction needs the underlying page. ELI-1307 subsequently addressed this exclusion.
5. **Evaluate reference-set completeness.** The audit judged 127 unmatched extractions valid: 71 customers and 56 integrations. These are historical judgments, not additions made by this report.
6. **Reject same-vendor integrations.** Nine candidates represented the target's own products/features rather than independent third-party products. Ownership can require explicit aliases or contextual evidence beyond lexical overlap.
7. **Distinguish backend infrastructure from customer-facing integration.** Six candidates described components used to build or host a product rather than named systems with which customers interoperated.
8. **Normalize spelling consistently.** Accented and unaccented variants could count as both FP and FN. A known instance was already merged into the baseline, so fixing normalization had no additional modeled effect here. ELI-1306 subsequently addressed diacritics and apostrophe variants.

## Isolated impact estimates

These are counterfactual reclassifications, not measured post-fix pipeline runs. They assume each intervention has exactly the stated effect and no collateral changes.

| Intervention | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | 75 | 228 | 190 | .248 | .283 | .264 |
| Recover 77 first-party misses | 152 | 228 | 113 | .400 | .574 | .471 |
| Recover 79 off-site misses | 154 | 228 | 111 | .403 | .581 | .476 |
| Remove 21 unsupported references | 75 | 228 | 169 | .248 | .307 | .274 |
| Remove 25 sitemap FPs | 75 | 203 | 190 | .270 | .283 | .276 |
| Reclassify 127 reference gaps | 202 | 101 | 190 | .667 | .515 | .581 |
| Remove nine same-vendor FPs | 75 | 219 | 190 | .255 | .283 | .268 |
| Remove six infrastructure FPs | 75 | 222 | 190 | .253 | .283 | .267 |

## Unresolved accounting and interpretation

The original combined projection was TP=358, FP=61, FN=5, yielding P=.854, R=.986, F1=.916. **That projection is not reconciled:** baseline expected relationships (265), plus 127 additions, minus 21 removals implies 371, whereas projected TP+FN is 363. Eight relationships are unaccounted for, and the original reclassification needed to resolve this was not preserved. Do not use the combined projection as a verified attainable score.

The source also alternates between eight sitemap-proposed rejections in the category table and twelve sitemap-related misses in intervention discussion. Those counts are retained here as an unresolved scope/count discrepancy rather than silently combined. Intervention populations overlap and do not sum directly to the exclusive FN-category table.

The broader lesson is that acquisition gaps, insufficient evidence, reference incompleteness, entity matching, and semantic errors need separate measurements. A larger crawl alone cannot guarantee the modeled gain; it can also introduce new false positives.
