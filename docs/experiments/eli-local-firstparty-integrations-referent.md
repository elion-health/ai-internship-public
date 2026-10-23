# Integration referent resolution on first-party pages

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Question and method

Does the integration referent work on vendor-owned pages, and does improving it change final retained relations? The referent reads the full page for one-sided quotes, then passes recovered evidence to a five-vote semantic check.

The historical corpus contained 340 pages from 32 products, selected by integration-related URL shapes because only 34 pages had existing integration extraction records. This biases recall toward integration-shaped pages, not whole websites. Extraction produced 325 relations and 180 one-sided cases. A `gpt-5.6-sol` page-based key was reviewed before prompt scoring. Four full-pipeline comparisons then evaluated baseline and candidate prompts.

These are private broader-corpus measurements, not public ten-product results. Named catalogue products, evidence quotations and page URLs are omitted.

## Answer-key correction

One catalogue page supplied 42/180 cases, 23% of the set. Its table listed more than 1,000 named products. The model accepted 25 rows and rejected 17 despite their common structure. The page had 135,757 characters and was not truncated, so this was not attributed to an input context cutoff.

Under the benchmark's requirement for relationship evidence beyond co-listing, all 42 rows were relabeled reject. The key changed from 100 accept / 80 reject to 75 / 105. The corresponding vendor had nineteen reference integrations; treating every table row as sufficient would imply roughly a thousand edges. This was an evidence-policy distinction, not a claim that all catalogue endpoint pairs were false in the world.

## Referent-only findings

Across three baseline runs, approximately 73/75 true cases were accepted but only 66/105 reject cases were rejected, including about 14/42 catalogue rows. The positive prompt example resembled broad vendor integration listings, encouraging acceptance.

A first candidate targeted large round totals. It improved catalogue rejection but cost six accepts on prose sentences that named specific products alongside totals. Retargeting the rule to table structure recovered those six in the comparison described by the source. The narrative reports 31 false accepts fixed without losing accepts; repeated-run means below are more qualified, with accepts 73.0 → 72.0.

| Version | Accept /75 | Reject /105 | Key F1 | Reference F1 | Catalogue rejects /42 |
|---|---:|---:|---:|---:|---:|
| v21 baseline | 73.0 | 66.3 | 0.782 | 0.398 | 14.3 |
| v22 total-focused | 68.7 | 99.0 | 0.918 | 0.283 | 40.7 |
| v23 table-focused | 72.0 | 96.7 | 0.927 | 0.335 | 40.0 |

Baseline variation across three runs: accept sd 0.8, reject sd 2.5, F1 sd 0.014. This is a heavily concentrated tuning set, not a held-out validation of table handling.

## Full-pipeline results

| Run | Baseline edges | Candidate edges | Difference | Catalogue rows extracted |
|---|---:|---:|---:|---:|
| 1 | 148 | 151 | +3 | 1 |
| 2 | 152 | 153 | +1 | 1 |
| 3 | 113 | 113 | 0 | 40 |
| 4 | 153 | 153 | 0 | 1 |

The referent improvement had little effect downstream. In the run emitting 40 catalogue rows, the baseline referent allowed approximately 26 through, but the full pipeline kept only three. The semantic check was already rejecting most of the same errors.

Across four runs, the candidate uniquely kept 16 edges, three in the reference dataset; the baseline uniquely kept 12, two in the reference dataset (19% versus 17%). These small reference-overlap counts establish no precision advantage, particularly with an incomplete reference set.

Extraction variance was larger than the prompt effect: three identical calls on the catalogue page returned 1, 39 and 2 relations. A single end-to-end run could not isolate the referent change when proposal counts varied this much.

Eleven of the 42 rejected catalogue rows corresponded to actual reference integrations. Rejecting insufficient page evidence therefore lowered reference F1 from 0.398 to 0.335. Page-evidence validity and world-level reference membership answer different questions.

## Decision and reproducibility limits

The report did not justify deployment on the strength of referent-only F1. Improving the intermediate decision could be useful for its own correctness, but downstream gain was unproven. Extraction stability was identified as the larger intervention point.

The baseline prompt was verified against its deployed content before scoring. Cases, correction records, leaderboards, four paired pipeline outputs and prompt files remain private and unavailable here. Their reported results are preserved without exposing dataset payloads or private workflow instructions.
