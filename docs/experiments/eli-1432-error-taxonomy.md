# ELI-1432 — Error taxonomy across a 61-product run

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Scope and headline findings

This audit expanded ELI-1252's seven-product analysis to a historical 61-product LangGraph run using `openai/gpt-5.6-luna`, with `--mode initial --skip-external --first-party-only`. It analyzed existing extraction results and reference labels without new extraction calls or graph writes. Categorization used reasoning over retained evidence.

After excluding six stale-source rows, the pooled counts were **212 true positives, 431 accepted candidates absent from the reference set, and 654 false negatives**. Reference absence is not independently established falsity: 191 of the 431 unmatched candidates were judged potential reference additions.

Two findings changed the priority order: missing seeded partner nodes explained little of low integration recall, while insufficient evidence and reference incompleteness were now comparably large FP categories. Reused testimonial and marketing templates accounted for many evidence failures.

## Three non-interchangeable scoring views

| View | Customer F1 / precision / recall | Integration F1 / precision / recall |
|---|---|---|
| A: persisted edges with marketplace identifiers, macro-average | 20.06% / 22.81% / 30.86% | 2.37% / 7.95% / 26.04% |
| B: also include candidates lacking seeded endpoints, macro-average | 20.06% / 22.81% / 30.86% | 2.33% / 7.02% / 26.13% |
| C: audit's pooled candidate counts | 31.9% / 32.3% / 31.6% | 8.2% / 58.8% / 4.4% |

A and B average per-product metrics. C pools candidate counts first and is dominated more strongly by high-volume products; it also uses the audit's candidate population. These figures must not be substituted for one another.

| Profile | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| Customers | 202 | 424 | 438 | .323 | .316 | .319 |
| Integrations | 10 | 7 | 216 | .588 | .044 | .082 |
| Combined pooled | 212 | 431 | 654 | .330 | .245 | .281 |

Thirteen product/profile pairs were reported skipped because no extraction run existed under the current model. The source's later wording about the skip population is inconsistent with its earlier count/list; no missing rows are inferred here. Public-sample FP contributor counts reported were Abridge 27, Cadence 26, and Suki 21. The aggregate cohort is not the released ten-product dataset sample.

## Stale sitemap contamination

Six of an original 1,303 audit rows used sitemap or robots content: one TP, four FP, and one FN. All six were removed from the totals above.

The underlying investigation found 43 stale fetched frontier entries across eight products and 280 post-exclusion extraction runs against them; 60 runs succeeded. The extraction version and completion time were current, but retained source content predated URL exclusions. Preventing new discovery and rescreening pending URLs did not retroactively exclude already fetched sources.

This was a source-eligibility invalidation problem, not evidence that an older model's extraction had been mixed into the current run. The audit identified it but did not perform the pipeline backfill or cleanup.

## False-positive categories

| Category | Count |
|---|---:|
| Evidence insufficient | 215 |
| Judged valid but absent from reference | 191 |
| Not a specific named organization | 15 |
| Partnership rather than usage | 5 |
| Hypothetical/future relationship | 3 |
| Other | 2 |

Accepted candidates lacked machine-readable rejection reasons, so all 431 required evidence judgment. Insufficient evidence represented 50% of unmatched output, compared with 55/228 (24%) in ELI-1252. Much of this concentrated in a few products with repeated page templates rather than appearing uniformly across the cohort.

## False-negative categories

| Cause | Count |
|---|---:|
| Correct page fetched, extraction/grounding failed | 238 |
| First-party evidence never fetched | 189 |
| Off-site evidence never fetched | 182 |
| No reference evidence URL to check | 21 |
| Relationship not established / insufficient evidence | 16 |
| Not a specific named organization | 5 |
| Name only in URL | 1 |
| Blocked at extraction time, resolves now; stale result | 1 |
| Wrong relationship shape | 1 |

Initial FN categorization was mechanical: 24 proposed-but-rejected records had grounding reasons; 630 never-proposed records were split using fetch coverage and reference-citation hostnames. Hostnames were used instead of stored source-type labels because at least one source-type label disagreed with its actual host. Rejection codes encode the pipeline's judgment, not independent proof that the reference relationship is false.

### Seed-catalog hypothesis

A separate full-history check found 70 `unresolved_endpoint` integration rejections among approximately 8,740 rejections. Including recoverable candidates moved macro integration F1 down 0.04 percentage points, recall from 26.04% to 26.13%, and precision from 7.95% to 7.02%.

Only one FN in the primary pass had this rejection reason. Live lookup subsequently resolved its endpoints uniquely. `unresolved_endpoint` can mean absence or ambiguity, so its code alone did not prove a missing seed. The likely explanation was changed seed state without re-extraction of a terminal source/profile pair. That causal explanation remained an inference.

The evidence therefore did not support seeded-node coverage as the main integration bottleneck. Acquisition and extraction/verifier behavior were much larger categories.

### External-source scope

The 182 off-site misses comprised 145 customers and 37 integrations and were spread across products rather than concentrated in one. The source compared this with an earlier 68-row scope estimate; ELI-1252's broader intervention table used 79, so those historical populations should not be conflated. This audit refreshed the scale of external evidence dependence but did not measure the cost or precision risk of expanding external discovery.

The 189 first-party misses were actionable coverage candidates, not a guaranteed lower bound on recovered true positives: successful fetching still requires qualifying evidence and successful extraction.

## Detailed review of the two largest diagnostic buckets

All 238 fetched-page FN cases and all 215 insufficient-evidence FP cases were inspected, rather than sampled.

| Fetched-page FN subcategory | Count |
|---|---:|
| Qualifying prose genuinely missed | 111 |
| Logo/image strip without prose | 50 |
| Bare name list/directory/dropdown | 49 |
| Entity only named as a person's employer in unrelated context | 14 |
| Unclear from page content | 14 |

The 111 genuine prose misses spanned 27 products. Infermedica accounted for 13 and Suki for five. Case-study headings, testimonials, and explicit integration statements were represented. Whether long-page truncation, prompt weighting, or another mechanism caused these misses was not resolved.

The 99 non-prose cases exposed an evaluation-specification question rather than an established extractor defect: a text-extraction task and a reference set accepting visual/list evidence may have different scopes. Employer mentions also needed contextual attribution rather than name matching alone.

| Insufficient-evidence FP subcategory | Count |
|---|---:|
| Reused or near-identical evidence templates | 139 |
| Prose does not establish the relationship, or quote unavailable | 62 |
| Apparently valid evidence requiring reconsideration | 9 |
| Nonspecific organization / interface placeholder | 4 |
| Future or hypothetical statement | 1 |

An initial exact-string detector found only 40 reuse cases; full inspection found 139. Count changes, lightly varied phrasing, substituted names, and fictional scenario templates escaped exact matching. Public-sample counts were Cadence 15, Suki three, FirstHx one, and Abridge one; no template quotes or relationship identities are reproduced.

The nine potentially miscategorized cases were not silently reassigned. Collective multi-organization mentions also remained a specification question: co-mention alone does not settle which entity has which relationship.

## Counterfactual impact, not measured improvements

| Assumed intervention | Pooled precision | Pooled recall | Pooled F1 |
|---|---:|---:|---:|
| Baseline | 33.0% | 24.5% | 28.1% |
| Recover 111 genuine prose misses | 42.8% | 37.3% | 39.9% |
| Remove 139 template-evidence FPs | 42.1% | 24.5% | 30.9% |
| Both | 52.5% | 37.3% | 43.6% |

These estimates change only the specified TP/FP/FN counts. They assume perfect targeted recovery or removal, with no regressions, and do not predict macro-average scores. Proposed work was to investigate missed qualifying prose and test entity-specific evidence checks sensitive to near-template reuse. Neither intervention was implemented by this audit.

## Reference-quality checks

| Population reviewed | Size | Supported | Classified wrong | No verdict |
|---|---:|---:|---:|---:|
| TP sample, approximately 30% stratified by product/profile | 74 of 212 | 62 | 10 | 2 |
| FN records rejected after content was found | 24 | 13 | 7 | 4 |

Among the 17 labels classified wrong, twelve involved partnership or prospective-deployment language, four involved evidence integrity, and one involved a partner classified as a customer. These were checks of the reference citations, not just the pipeline's emitted quote.

The 98 reviewed records were a selected sample, not a simple random draw. The source's rough extrapolation of 17/98 to approximately 40 errors in a 236-record population was not an observed count or a statistically established estimate.

A broader review examined 188 TP records with captured quotes and flagged 44 as ambiguous. Twenty-four TP records had no quote available for that method. Ambiguity flags were a lower-confidence category than the 17 wrong-label judgments and must not be added to them as confirmed errors. A separate evidence-completeness inventory reported 65 reference entries without supporting evidence.

## Limitations of this public edition

The audit's historical scripts, spreadsheets, categorized candidate files, private review worksheets, and exact annotation instructions are omitted. Aggregate findings preserve the methodological distinctions between model errors, reference gaps, evidence ambiguity, source staleness, and scoring populations. They do not independently validate individual relationships or supply a runnable reconstruction of the private 61-product experiment.
