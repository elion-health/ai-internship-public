# ELI-1308 — Crawl limits, coverage gains, and precision losses

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Root cause and experiment

`research_first_party_page_limit` capped both the discovery map and subsequent fetches. Its default of 50 truncated discovery before relevant evidence URLs were even candidates. In the seven reference products examined, no mapped site exceeded 948 pages, while known missing evidence appeared at positions 217–445 in inventories of roughly 450–950 URLs. The map order was not a relevance ranking.

The historical experiment raised the default to 1000 and reran twenty products: the original seven plus thirteen from ELI-1289. Settings were `--mode initial --skip-external --first-party-only`. The larger crawl fetched approximately eleven times more page content per product.

## Measured outcome

| Metric, seven-product evaluation | Result | Change |
|---|---:|---:|
| Customer F1 | 27.97% | −1.46 percentage points |
| Customer precision | 19.80% | −11.00 points |
| Customer recall | 52.52% | +18.82 points |
| Integration F1 | 14.86% | No comparison baseline reported |
| Integration precision | 8.96% | No comparison baseline reported |
| Integration recall | 62.63% | No comparison baseline reported |

The recall gain did not produce the clean overall improvement projected by the earlier taxonomy. Increased evidence coverage also increased unmatched accepted relationships, some genuine reference gaps and some extraction errors.

## Separate data/configuration defects

- One product was seeded from a narrow subpage, exposing only five pages rather than a root inventory of 645. Eleven of twelve reference evidence URLs lay elsewhere on the site. Reseeding recovered 689 new pages and 404 extraction items; map and retained-page counts are different measurements.
- Four additional products had the same subpath-seed issue and were rerun from corrected roots.
- Suki had 439 graph edges but lacked the identifier used by the evaluator, causing a zero-result lookup. The identifier was backfilled for scoring. This was a provenance/join issue, not a crawl-limit effect.

These concurrent corrections limit attribution of every per-product change solely to the page-limit increase.

## Audit of newly introduced unmatched output

The audit selected active customer/integration edges sourced only from pages newly fetched in this experiment and absent from the reference set: 695 candidates across seven products.

The judgment procedure needed calibration. Early passes rejected legitimate testimonial wording, confused first-person integration descriptions with profile errors, and overclassified positive marketing language as vendor self-reference. A mislabeled few-shot example also produced inconsistent profile decisions. An initial 18-case validation set grew to 23 cases; validation reached 20–21 correct out of 23 before the large pass. Three-vote majority decisions reduced run-to-run variation. Four later spot-check rounds found no further systematic pattern, but this was not independent ground truth.

| Category | Full cohort | Infermedica | Suki |
|---|---:|---:|---:|
| Potential reference additions | 345 | 94 | 1 |
| Wrong-profile candidates | 127 | 108 | 1 |
| Generic categories | 111 | 6 | 2 |
| Name inferred from URL | 58 | 15 | 0 |
| Self-reference | 27 | 2 | 1 |
| Developer-documentation artifacts | 27 | 0 | 23 |
| Total | 695 | 225 | 28 |

These are candidate counts, not unique relationships. In Infermedica's wrong-profile bucket, 87 of 108 candidates reused the same boilerplate sentence across roughly 87 pages; the distinct-relationship count was estimated closer to 15–20. Suki's apparent lack of reference gaps was largely explained by developer-documentation artifacts.

Subsequent targeted reports revised several category sizes substantially: ELI-1315 found 11 definite wrong-profile rows, ELI-1316 found ten definite self-reference rows, and ELI-1317 found nine URL-only-name cases. The initial table should therefore be read as automated triage, not final adjudication.

## Technical decisions and follow-up

The follow-up work separated generic entities, wrong profiles, self-reference, URL-derived names, and developer documentation into ELI-1314 through ELI-1318. Reference-set additions were a separate evaluation-quality task. Full-corpus F1 after those fixes was not measured in this report.

The historical environment template still specified a limit of 100, overriding the new code default when loaded. It was updated to 1000. Existing explicit environment values also continued to override defaults; changing a code default alone was insufficient to change effective behavior. No private configuration or runnable historical command bundle is included here.
