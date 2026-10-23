# ELI-1488 — Curated opening searches and discovery budgets

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Question and design

Could spending search budget up front on nine curated evidence hosts and known customer names improve external discovery? The historical experiment used the same ten broader-corpus study vendors as ELI-1485 and 89 reference customers with third-party-only proof. It is not an evaluation of the public ten-product dataset.

| Design | Targets found /89 |
|---|---:|
| A — production agent | 19 |
| C — press-wire searches, hints and two-phase instructions | 18 |
| B — one search per known host plus hints | 15 |

A further evidence-based setup was compared twice with the production agent, at reported cost $1.31:

| Design | Run 1 | Run 2 | Mean |
|---|---:|---:|---:|
| A — production agent | 20 | 13 | 16.5 |
| D — evidence-based setup | 20 | 15 | 17.5 |

## Findings and limits

The four coded opening-search designs led to target pages in 2/40, 3/90, 1/30 and 3/172 searches. The agent's contextual name searches had performed substantially better in ELI-1487. Supplying eight hints and explicit strategy instructions still resulted in only about one in ten searches using a hint.

Identical production-agent runs differed by seven targets. The original report described this as a ±7 noise scale, but two observations do not establish a confidence interval. The one-target mean advantage for D did not establish improvement. This variation also weakened confidence in ELI-1485's single-run +11 result.

The experimental `system_seeds` and `extra_instructions` parameters were reverted. A larger baseline and repeated comparisons of a page judge or a different agent model were proposed. No successful curated-budget intervention was established; private scripts and outputs are unavailable here.
