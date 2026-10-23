# ELI-1489 — Historical 61-vendor discovery baseline

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Method

One unchanged-agent pass over all 61 historical study vendors on 2026-09-18 established a larger external-discovery baseline. Reported cost was $2.19, with zero failures. These are private broader-corpus measurements, not public ten-product results.

| Measure | Result |
|---|---:|
| External-only reference customers found | 88 / 239 (37%) |
| Any reference customer named | 228 / 749 (30%) |
| Kept pages naming a reference customer | 290 / 887 |
| Searches used | 1,324 / 1,525 |
| Page reads used | 462 / 915 |

## Interpretation

Only about one third of kept pages named a reference customer. This measures reference-set matching, not proof that all other pages were useless. Search budgets were nearly exhausted while roughly half the read budget remained unused, suggesting read selection as an intervention point.

Performance varied strongly by vendor. Of 41 vendors with external-only targets, ten found all their targets and six found none. Allowed-product metrics included ArriveHealth 0/4 and FlatironOncoEMR 0/3. One other zero-result case had an incorrect seed URL, illustrating the need to separate data defects from agent limitations.

## Comparability and next experiments

All runs shared one historical `discovery_version` stamp. That field was later removed; its role here is provenance of this recorded experiment, not a current pipeline requirement. Proposed comparisons included a judge at the page-read decision and another agent model, with repeated arms, plus separate seed-URL fixes.

The single pass does not estimate run variance. Per-vendor baseline data, action logs and the historical run script remain private and unavailable here.
