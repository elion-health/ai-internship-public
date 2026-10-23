# ELI-1484 — Where reference evidence lives

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Method

Classify reference proof URLs by whether the seeded vendor site can reach them. The historical analysis covered 61 vendors and approximately 1,068 links, with no agent runs or API expenditure. These aggregates describe the private historical corpus, not the public ten-product dataset.

| Evidence classification | Customers | Integrations |
|---|---:|---:|
| Reference entries | 749 | 249 |
| Provable on the vendor's own site | 396 (53%) | 179 (72%) |
| Only third-party proof | 239 (32%) | 51 (20%) |
| Nothing first-party crawling can reach | 294 (39%) | 64 (26%) |

The final row includes evidence on vendor-controlled domains outside the configured crawl. It is therefore broader than strictly third-party evidence.

## Findings

1. The curated host list covered only 17% of off-vendor proof. Much of the remainder was on customer sites or infrequent hosts; searching names in context was a stronger candidate strategy than extending a fixed host list.
2. The excluded social-network-class hosts contained zero reference proofs in this corpus.
3. PDFs accounted for 11% of customer proof, making PDF parsing a retrieval requirement.
4. Two incorrect vendor seed URLs stranded 37 and 11 proofs on the actual domains. This was a data configuration defect, not an agent reasoning failure.

## Historical reproduction

The original command was `PYTHONPATH=src python scripts/analyze_golden_evidence_hosts.py --out report.md --export-targets targets.json`. That corpus-analysis script, its private input corpus and exported targets are unavailable here. This is a record of the method, not a runnable public command.
