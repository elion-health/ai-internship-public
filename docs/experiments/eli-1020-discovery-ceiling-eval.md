# ELI-1020 — Discovery-Ceiling Evaluation

> Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Objective

Measure first-party website coverage and extraction performance for FirstHx, Flatiron OncoEMR and Fold Health. Separate failure to recover available evidence from the absence of extractable evidence on the vendor's own site. External augmentation was excluded from the final scored runs.

Raw recall against all golden customers conflates extraction failures with first-party evidence that does not exist or cannot be scraped. A discovery ceiling estimates how many golden relationships have usable first-party evidence before assessing pipeline recovery.

## Method and coverage

The investigation compared sitemap size, first-party frontier records, retained content and expected evidence availability. Missing cases were checked against retained pages, directly fetched where possible, and tested repeatedly through extraction and grounding. Fold Health also received independent site-restricted searches and direct attempts to fetch plausible case-study pages.

| Product | Sitemap total | First-party discovered/fetched | Coverage |
|---|---:|---|---|
| Flatiron OncoEMR | 1,063 | 161: 159 fetched, 2 excluded | ~15.1% |
| FirstHx | 54 | 49 fetched | ~90.7% |
| Fold Health | No sitemap found | 5 fetched | Not computable |

Flatiron's sitemap count combined 40 main-site pages and 1,023 resource-subdomain pages. Fold Health's sitemap endpoint returned 404. Its five fetched pages did not imply five useful content pages; the investigation described only two pages as its broadly crawlable footprint.

Some expected pages had frontier status `fetched` after an external round but no corresponding retained source. This is distinct from never discovering the URL. Raising the first-party crawl limit recovered one such page through a fresh first-party path, but did not explain the original fetched-to-persisted gap.

## Scored results

Fresh initial-mode runs forced extraction. The persistence fix described below was active, title injection was reverted, and a proposed recency filter remained disabled.

| Product | Recall | Precision | F1 | Matched / Golden |
|---|---:|---:|---:|---:|
| FirstHx | 80% | ~28.6% | ~42% | 4 / 5 |
| Flatiron OncoEMR | 66.67% | ~16.7% | ~26.7% | 4 / 6 |
| Fold Health | 0% | 0% | 0% | 0 / 4 |
| **Mean** | **~48.9%** | **~15.1%** | **~22.9%** | |

**Mixed settings:** Flatiron used a later rerun with `RESEARCH_FIRST_PARTY_PAGE_LIMIT` and `DISCOVERY_MAX_CRAWL_PAGES` raised from 50 to 1200. The others retained the default. The mean is not a controlled same-settings comparison.

**Suspect recovery:** one of Flatiron's four accepted matches contained model-inserted referent annotations absent from the original source. The fuzzy quote check accepted the mostly copied passage, and the inserted name then satisfied endpoint matching. The crawl-limit improvement was real, but that accepted edge did not prove legitimate extraction recovery.

## Product-level ceilings

### Flatiron OncoEMR

The historical golden set had been revised to six customers. Five had usable first-party evidence; one had only weak on-site references and clear off-domain evidence. The first-party ceiling was therefore **5/6**, not 6/6.

The persistence fix increased matches from 2/6 to 3/6. The raised crawl limit increased the reported score from 50% to 66.67%, subject to the fabricated-annotation limitation above. A remaining testimonial used a pronoun where the product name appeared about 1,000 characters earlier. Free-text testing ruled out structured-output conservatism as the sole cause. Manually placing the product name beside that testimonial recovered it in 2/3 trials.

Directly fetching another missing page did not by itself recover its relationship: a customer-side referent-distance problem remained. Discovery and extraction were separate blockers, so better crawl coverage was not sufficient for every missing case.

### FirstHx

All five golden customers had strong first-party evidence: **ceiling 5/5**. Earlier corpus probes found three clean or mostly reproducible recoveries, one discovery/persistence gap and one customer-side referent-distance failure. The later scored initial run recovered the discovery case, producing 4/5.

Repeated direct extraction produced 3/3 success for two stable cases and 2/3 for a case whose failed quote lacked a recognized adoption cue. A directly fetched missing page produced an accepted match in 3/3 runs. The referent-distance case succeeded 0/3 as originally scraped and 3/3 after manual name insertion. These are historical case-level trial counts, not a published relationship list.

### Fold Health

The four golden customers were checked through retained-content search, direct fetch and independent site-restricted search. Three had no on-domain presence; one appeared only on a gated download page. Extraction on that page rejected all three trials, and the visible page contained no groundable adoption statement.

- Extractable first-party evidence: **0/4**.
- Any on-domain mention: **1/4**.
- Historical golden relationships considered real regardless of domain: **4/4**.

Thus 0% first-party recall did not establish that the labels were false. A lead-generation form is a structural blind spot: the supporting document is not in the visible scraped text. A separate glued-word tokenization issue was also found: `FoldHealth` did not match `Fold Health`.

## Technical interventions

**Persistence revision semantics.** `activate_extraction` previously deactivated all prior extraction edges for a source, even when a stochastic rerun simply omitted a previously grounded fact. It was changed to deactivate an earlier edge only when the new revision produced a replacement for the same `(source, relation, target)` triple. Four scenarios were tested, including an adversarial UUID-mismatch limitation.

**Page-title injection, reverted.** Tested across 25 sources, with two consequential findings replicated four times. It recovered 0/3 targeted misses and introduced reproducible quote-attribution errors without a compensating benefit. Similarity matching detects token overlap, not who made a statement.

**Recency filter, disabled.** An `evidence_stale` check was implemented behind `reject_stale_evidence=False`. A proposed blanket age rule conflicted with retained golden examples, so it was not enabled. Private annotation rules and discussions are omitted; the technical issue was inconsistent evaluation assumptions.

**Referent-distance preprocessing, proposed only at this stage.** The idea was to recover a missing endpoint from elsewhere on the same first-party page and retry grounding. Manual insertion worked on both product-side and customer-side failures, but required corpus-wide replication and precision checks before deployment. Subsequent reports document the self-validation risk of altering evidence text.

## Conclusions and remaining uncertainty

The main levers were referent resolution, reliable discovery-to-retention and precision controls. Crawl limits mattered on the large Flatiron site, not on Fold Health's five-page discovery result. The original external-fetch persistence gap was unresolved. Historical scripts, row-level evidence, annotation material and source URLs are unavailable in this edition; the reported metrics describe the original corpus, not the bundled sample.
