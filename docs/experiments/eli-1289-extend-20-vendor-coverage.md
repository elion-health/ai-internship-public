# ELI-1289 — Extending coverage from seven to twenty products

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Experiment

The stateful LangGraph research workflow processed 13 additional products using first-party websites only, with both customer and integration profiles. Historical settings were `--skip-external --first-party-only`. This increased coverage from seven to twenty products and exposed evaluation bugs unrelated to the additional targets.

## Results

| Public-sample product in the added cohort | Customer F1 | Integration F1 |
|---|---:|---:|
| AgileMD Clinical Pathways | 44.4% | 0.0% |
| Cadence | 57.1% | 0.0% |

Across all 13 additions, six products had nonzero customer F1 (18.2%–57.1%); seven had zero. All 13 had zero integration F1. The historical cohort is larger than the released ten-product dataset sample; this report exposes aggregate findings rather than additional product datasets.

## Evaluation corrections

### Unreliable graph identifiers and directionality

The evaluator joined products by `marketplace_id`. Five of the 13 products had no such value and six had a value different from the configured target. The historical correction matched trimmed, case-insensitive product names while retaining node-label restrictions.

That exposed a separate direction bug: adoption lookup matched a target name on either endpoint. Adoption has a fixed HealthSystem→Product direction; a same-named organization on the wrong side could leak its own adoptions into the target's customer list. The query was restricted to the correct endpoint. Integration lookup retained either-side matching for Product–Product relationships, with self-loop exclusion.

After rescoring, one product's customer F1 changed from 16.7% to 18.2%. This was an evaluation correction, not improved extraction.

### Historical-output drift

Reexecuting an existing comparison notebook resolved extractor versions against current state and changed previously published results for the original seven products. The older sections were restored from their original snapshot: 72 of 73 cells were byte-identical, with only introductory scope prose intentionally changed. New sections retained their new output.

This demonstrated why analysis should pin extractor versions rather than resolve a live model/prompt configuration during every rerun. A second historical utility used the same unreliable identifier join and remained outside this correction's execution path.

## Limitations

- A zero score can arise from discovery gaps even when map inventory is not reported truncated: site navigation may never expose relevant pages, and reference URLs can redirect to other undiscovered pages.
- Nonzero integration candidate counts alongside zero matched F1 suggested naming or product-family normalization problems, but this experiment did not establish their root cause.
- Repeated near-identical source pages inflated graph edge counts. The reported set-based scores deduplicated names; raw edges should not be interpreted as unique relationships.
- The additional products were not run through a second model, so this was coverage expansion rather than a model comparison.
