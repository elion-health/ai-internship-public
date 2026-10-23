# ELI-1446 — Bias external discovery toward off-domain source categories

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Question and scope

Would category-based external discovery recover relations missed by first-party crawling? These are reported exploratory measurements from a private 61-product corpus, with intervention validation on four selected products. They are not results on the public ten-product dataset.

Hostname analysis found 38.8% of reference evidence off the seeded vendor domain. Recurring source categories included press wires, customer-story aggregators, research publications, industry news and application marketplaces. Twelve recurring hosts accounted for 94 matching entries across more than 30 products. About 12% of apparent off-domain evidence was instead associated with incorrect vendor seed URLs. The reference `source_type` field was not useful: it labeled every entry `vendor_website`, including external hosts. Actual host matching was required.

## Method and implementation

The LangGraph discovery workflow received a second, domain-scoped seed search alongside its generic keyword search. It ran before agent reasoning. Both explicit seeds were needed because prepopulating the frontier bypassed the agent's internal seed-if-empty behavior.

Fallback page selection added a deterministic host-category bonus of 2. This used source categories, not answer-key page URLs. Duplicate hardcoded discovery instructions were consolidated into one versioned prompt per profile, including a preference for pages naming specific organizations.

A separate tracing fix recursively truncated strings longer than 4,000 characters in chain inputs, outputs and model responses. LangGraph callbacks received accumulated state containing scraped page text; repeatedly logging that state created payloads up to 48 MB against a 10 MB logging limit, causing HTTP 413 errors and timeouts. Truncation was applied at chain start, chain end and model end.

## Validation and results

The first resumed validation was invalid: outputs were byte-identical because persisted frontier URLs were excluded from rediscovery. The corrected experiment tagged frontier entries by `first_party` or `external` origin and reset external state only. First-party frontier counts of 47, 588, 191 and 948 were checked before and after reset.

| Metric | First-party-only baseline | After external-discovery change |
|---|---:|---:|
| TP | 6 | 30 |
| FP | 3 | 37 |
| FN | 69 | 45 |
| Precision | 66.7% | 44.8% |
| Recall | 8.0% | 40.0% |
| F1 | 14.3% | 42.3% |

Per-product counts are retained below under anonymous study labels; these are not public dataset rows.

| Study product | TP before → after | FP before → after | FN before → after |
|---|---|---|---|
| A | 0 → 1 | 1 → 4 | 24 → 23 |
| B | 0 → 20 | 0 → 23 | 20 → 0 |
| C | 6 → 9 | 2 → 10 | 15 → 12 |
| D | 0 → 0 | 0 → 0 | 0 → 0 |

A press-wire filing listing approximately 31 clients drove the largest gain. Four generic, unnamed organization descriptions still survived extraction; the discovery prompt preference did not solve downstream entity validity.

The historical report records six new tests covering prompt loading, both seed searches, host-score tie breaking and truncation at the three callback boundaries. It reports 416 passing tests and one pre-existing referent-resolution failure. These are historical checks, not checks rerun for this public edition.

## Limitations

The four products were selected for unusually many off-site false negatives, so the observed gain cannot be generalized to all 61. Precision fell as recall rose. Seed-URL corrections and generic-entity rejection remained separate work. The historical reset scripts, corpus and run outputs are private and unavailable here.
