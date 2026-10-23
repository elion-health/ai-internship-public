# ELI-1544 — False integrations from a third-party directory

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Failure mechanism

A third-party directory listed approximately 250 platforms connected to its own product. The extractor incorrectly proposed 14 integrations between a study product and neighboring entries. It assembled an evidence quote from a prose sentence and names elsewhere on the page, joined by an ellipsis. The semantic check matched the prose section and accepted claims whose two endpoints were absent from that section.

The original filename is retained for historical continuity. Actual page URLs, product-counterparty combinations and evidence quotations are omitted.

## Pipeline change

1. **Extraction:** propose endpoint names and an evidence quote. Quotes should be copied as contiguous page text rather than constructed from disconnected fragments.
2. **Deterministic sorting:** require a single contiguous match covering 95% of the quote. A quote naming both endpoints can proceed to semantic assessment; it is not, by itself, proof of a valid relationship. A one-sided quote is sent to referent resolution.
3. **Referent resolution:** read the full page with both endpoints, the weak quote and page address; decide whether the relation holds and return a proof sentence. Locate that sentence in the page and store the page's own text. After three unsuccessful attempts, drop the unresolved relation.
4. **Semantic check:** assess the quote, local page context, URL and any recovered proof sentence. Integrations used five votes and a majority; customers used one at this historical stage.

The referent's structured response placed its reason before its decision and proof sentence. URL context helped distinguish the page owner's integrations from relationships between directory entries. The integration-only ownership safeguard could not be applied indiscriminately to customer pages, where a vendor normally describes its own users.

Historical implementation affected `enrichment/grounding.py`, `enrichment/referent_resolution.py` and `enrichment/graph.py`. Referent failures were isolated from page-level processing; missing model configuration skipped referent resolution rather than automatically rejecting the page. Later reports describe further changes to these gates.

## Measurement

The experiment replayed 113 cached third-party pages. The old pipeline had no referent or contiguity check; the new pipeline ran twice. A stronger `gpt-5.6-sol` model judged accepted relations against their source pages. A second scoreboard matched the private reference integration dataset. Neither corpus nor row-level judgments is included here, and these are not public ten-product results.

| Page-based judgment | Kept | Correct | Wrong | Precision |
|---|---:|---:|---:|---:|
| Old | 214 | 176 | 38 | 82% |
| New, run 1 | 171 | 165 | 6 | 96% |
| New, run 2 | 152 | 146 | 6 | 96% |

“Correct” here means accepted by the page-based evaluator, not independently guaranteed truth.

| Reference-set judgment | Matches | Nonmatches | Precision |
|---|---:|---:|---:|
| Old | 54 | 96 | 36% |
| New, run 1 | 48 | 82 | 37% |
| New, run 2 | 49 | 60 | 45% |

Reference nonmatches are not necessarily false relations: the reference set is incomplete and its scoring unit differs from the page-based table. The original interpretation that many nonmatches were missing reference rows remains a hypothesis, not a measured count.

The motivating directory proposed 23 relations and retained zero in both new runs. Across two runs the source reports 307 referent inputs, 159 confirmations and 9 rejections. Those categories do not account for all 307 inputs; the report does not resolve the remainder, so no complete outcome distribution is inferred.

## Prompt experiments and limitations

Forty-five versions were evaluated on 98 referent cases: 87 accepts and 11 rejects. Contrasting a directory false-positive example with a supported positive moved rejection coverage from 0/11 to 11/11 at a cost of three accepts. Reported ablations cost 9 points for removing the user-oriented relationship question, 6 for removing the page-owner warning and 4 for removing the careers-page rule. These were tuning-set observations, not held-out results.

The key distinction was evidence about two products working together versus shared infrastructure or a company-level description. Exact private prompts, examples and review material are omitted. Repeated tuning on a small case set risks overfitting; a stronger model judge is still fallible.

The source identified first-party proof-sentence propagation and customer referent resolution as follow-up work. Cached pages, benchmark scripts, prompt versions and traces are private and unavailable here.
