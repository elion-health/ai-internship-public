# Experiment reports

These are reviewed public editions of the historical research reports, prepared in this private repository for final release review. They preserve methods, results and limitations without republishing the broader dataset. Quantitative results describe the original experiments, not a fresh replication using the ten bundled products.

## Reading guide

- Start with [the pipeline walkthrough](../pipeline.md) for current behavior and [reproduction](../reproduction.md) for runnable commands.
- Historical reports describe earlier pipeline versions. A superseded regex rule or prompt is not necessarily used today.
- References to unavailable prompt artifacts mean experiment-local files and tuning cases. The standalone repository bundles one public adaptation of each prompt's latest version, described in [reproduction](../reproduction.md); historical prompt versions are excluded.
- Some reports are abridged because their original contents were mostly dataset rows or private review material. Necessary illustrative examples are identified as fictional rather than presented as source quotations.
- Raw JSON/CSV outputs, outside-sample vendor-level results, fitted model weights, spreadsheets, evidence inventories, notebook outputs, charts and review sheets remain private. In particular, the nested `hand_review_sample.md` and `hand_judging_sample.md` files are row-level datasets, not additional narrative reports, and are excluded.
- The original experiment index compared implementations outside this release's scope; this navigation index replaces it. ELI-884 is a scope note, and ELI-951 retains the search-tool measurement lesson without the excluded implementation comparisons. Only the LangGraph research approach is supported here.

## Pipeline development and evaluation

- [ELI-1010 — Stateful graph evaluation](eli-1010-stateful-graph-eval.md)
- [ELI-1020 — Discovery ceiling](eli-1020-discovery-ceiling-eval.md)
- [ELI-1056 — Referent-distance recovery](eli-1056-referent-distance-fix.md)
- [ELI-1057 — Graphiti entity matching](eli-1057-graphiti-entity-matching.md)
- [ELI-1120 — Cue-regex experiment](eli-1120-cue-regex-widen.md)
- [ELI-1122 — Time and cost of graph reuse](eli-1122-graph-value-time-cost.md)
- [ELI-1130 — Extraction hygiene](eli-1130-extract-hygiene.md)
- [ELI-1135 — Fetch-stage baseline](eli-1135-fetch-baseline.md)
- [ELI-1136 — Extraction yield](eli-1136-extraction-yield.md)
- [ELI-1143 — Grounding-rejection audit](eli-1143-grounding-audit.md)
- [ELI-1146 — Referent retry validation](eli-1146-referent-resolution-fix.md)
- [ELI-1147 — Semantic validation](eli-1147-wrong-semantics-accepted-edges.md)
- [ELI-1170 — Model comparison](eli-1170-model-comparison-mini-vs-luna.md)
- [ELI-1192 — Removing brittle regex gates](eli-1192-remove-cheap-regex.md)
- [ELI-1195 — Integration grounding changes](eli-1195-integrations-regex-removal.md)
- [ELI-1197 — Pipeline diagnostic design](eli-1197-pipeline-diagnostic-notebook.md)

## Reference quality and error analysis

- [ELI-1227 — Reference-label audit](eli-1227-annotation-guideline-audit.md)
- [ELI-1228 — Prompt comparison](eli-1228-prompt-comparison-previous-vs-new.md)
- [ELI-1236 — First-party extraction scoping](eli-1236-first-party-only-scoping.md)
- [ELI-1248 — Flagged-label review methodology](eli-1248-hand-review-flagged-rows.md)
- [ELI-1252 — Error taxonomy](eli-1252-error-taxonomy.md)
- [ELI-1289 — Expanding evaluation coverage](eli-1289-extend-20-vendor-coverage.md)
- [ELI-1304 — Same-vendor integration rejection](eli-1304-same-vendor-integration-check.md)
- [ELI-1305 — Backend infrastructure distinction](eli-1305-backend-infrastructure-check.md)
- [ELI-1306 — Diacritic normalization](eli-1306-normalize-diacritics.md)
- [ELI-1307 — Sitemap exclusion](eli-1307-sitemap-exclusion.md)
- [ELI-1308 — Crawl-gap investigation](eli-1308-crawl-limit-fix.md)
- [ELI-1314 — Generic-category rejection](eli-1314-generic-category-check.md)
- [ELI-1315 — Profile-shape validation](eli-1315-wrong-profile-check.md)
- [ELI-1316 — Self-reference checks](eli-1316-self-reference-extension.md)
- [ELI-1317 — Visible-text name grounding](eli-1317-name-in-url-check.md)
- [ELI-1318 — Developer-documentation exclusion](eli-1318-developer-docs-exclusion.md)
- [ELI-1432 — Expanded error taxonomy](eli-1432-error-taxonomy.md)
- [Claim-level audit: method and aggregates](claims-audit-2026-09-30.md)

## Discovery experiments

- [ELI-1446 — External source-category bias](eli-1446-external-domain-bias.md)
- [ELI-1462 — Adaptive discovery budget](eli-1462-adaptive-discovery-budget.md)
- [ELI-1480 — Trace analysis](eli-1480-trace-analysis.md)
- [ELI-1483 — Self-evidence exclusion](eli-1483-self-evidence-exclusion.md)
- [ELI-1484 — Evidence-host distribution](eli-1484-golden-evidence-hosts.md)
- [ELI-1485 — Known names as discovery leads](eli-1485-feed-known-names.md)
- [ELI-1487 — Search-tool usefulness](eli-1487-tool-usefulness.md)
- [ELI-1488 — Curated discovery budgets](eli-1488-curated-budget.md)
- [ELI-1489 — External discovery baseline](eli-1489-baseline-61.md)
- [ELI-1490 — Judge at the keep decision](eli-1490-judge-at-keep.md)
- [ELI-1541 — Source-host exclusions](eli-1541-excluded-hosts.md)

## Whole-page interpretation and confidence

- [ELI-1544 — Integration referent investigation](eli-1544-adsc-false-integrations.md)
- [ELI-1549 — Customer referent investigation](eli-1549-customers-referent.md)
- [First-party customer referent](eli-1550-firstparty-customers-referent.md)
- [Integration experiment procedure](eli-1550-integrations-handoff.md)
- [First-party integration referent](eli-local-firstparty-integrations-referent.md)
- [First full extraction run: error analysis](first-full-run-errors.md)
- [Quote repair and subsequent changes](post-extraction-changes.md)
- [Confidence-score research](confidence-score.md)

## Early experiment context

- [ELI-884 — Scope note](eli-884-eval-summary.md)
- [ELI-951 — Verify search-tool behavior](eli-951-firsthx-recall.md)
