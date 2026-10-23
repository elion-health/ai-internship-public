# ELI-1010 — Evaluate Stateful Research Graph on Marketplace Customers

> Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Objective and scope

Evaluate the LangGraph stateful research pipeline for FirstHx, Flatiron OncoEMR and Fold Health against a historical customer golden set. Integrations extraction ran but was not scored: this evaluation had no integrations golden set. The historical dataset and evaluation traces are not bundled; the public sample is not the full evaluation corpus.

The goals were to measure recall and precision, inspect the persistent graph, and distinguish discovery, extraction, resolution and grounding failures.

## Architecture and method

The research flow was `initialize → map first-party → fetch → plan/run extractions → one external round → finish`. Per source/profile, ingestion followed `extract → resolve → verify_grounding → persist`. Neo4j stored `ResearchTarget`, `FrontierUrl`, `RetainedSource`, `ExtractionRun`, typed entities and active `RELATES_TO` edges. Resume reused the corpus and retried pending or failed work; initial mode remapped.

- Golden data consisted of three product-level records, each containing expected customer names.
- Matching normalized names with lowercase and removal of whitespace/hyphens.
- Predictions were active `HealthSystem -[RELATES_TO {name: "Adopts"}]-> Product` edges, scoped by `marketplace_id` rather than display name.
- Historical evaluation used `scripts/eval_stateful_research.py`, with product filtering and a score-only mode. This artifact and its private run state are not provided as a reproduction path.

The seed graph already contained 347 `Adopts`, 60 `Integrates`, 132 `Makes` and 3,363 `Addresses` edges. A run increased node count from 5,747 to 6,288. Existing seed relationships must therefore be distinguished from extraction-produced edges. Duplicate Vendor and Product display names also made name-only queries ambiguous.

Extraction planning deduplicated by `(source, profile, extractor_version)`, skipping terminal outcomes unless forced. External acquisition hardcoded an empty forced-profile list. Consequently, a surrounding Python grounding fix could fail to affect a resume run: extractor versions tracked prompt/model/schema, not all implementation changes.

## Baseline results

“Found / Truth” below is predicted-name count versus golden-name count, not true positives divided by truth.

| Product | Recall | Precision | F1 | Found / Truth |
|---|---:|---:|---:|---:|
| FirstHx | 40.00% | 18.18% | 25.00% | 11 / 5 |
| Flatiron OncoEMR | 0% | 0% | 0% | 2 / 22 |
| Fold Health | 0% | 0% | 0% | 0 / 5 |
| **Mean** | **13.33%** | **6.06%** | **8.33%** | |

FirstHx had two matches and nine predictions outside the golden set. The audit had not yet determined whether those nine represented missing labels or genuine over-extraction.

## Failure analysis

| Failure | Observation |
|---|---|
| Thin corpus | Flatiron had only 9 crawled URLs versus FirstHx's 184. |
| Off-topic discovery | Fold Health acquired unrelated competition and job-board pages. |
| Exact-string grounding | Product-name variants caused all 4 and all 7 candidates in two inspected batches to be rejected. |
| Generic endpoint | A descriptive phrase was treated as a company name. |
| Fabricated quote | Proposed evidence was absent from the source. |

## Flatiron investigation

1. **Broaden discovery.** An opt-in `MergedSearchProvider` combined Exa and Firecrawl results, deduplicating URLs; Firecrawl still fetched and mapped. Frontier URLs rose from 9 to 70 and retained sources from 5 to 25.
2. **Relax page relevance.** Requiring the full product name as one contiguous phrase excluded legitimate spelling variants. The check changed to require significant words rather than adjacency.
3. **Relax endpoint matching.** Exact equality between target and extracted endpoint rejected abbreviated product names. `_endpoint_matches_target()` used bidirectional token containment.
4. **Normalize decorative symbols.** `normalize_text()` began stripping `®™©`, which otherwise remained attached to tokens.

The same exact-string problem appeared at several independent gates: page relevance, endpoint presence in evidence, endpoint identity and tokenization. Individual failing cases changed from rejected to accepted after their respective fixes. Historical testing reported 143 passing tests and two pre-existing unrelated failures.

**The scored Flatiron result remained 0% recall after those four changes.** Near-verbatim quotes still failed the exact quote check, while verbatim quotes could support self-referential or generic false positives. Corpus growth and gate-level fixes did not establish an end-to-end metric improvement.

## Later update and limitations

A later change resolved abbreviated target names onto the existing Product node instead of creating duplicates and replaced exact quote matching with a difflib similarity threshold of 0.75. Local testing reported Flatiron recall of 45% (10/22). That result had not been confirmed by a scored evaluation rerun when this report was written; the baseline table above was not updated.

Priorities were broader discovery, flexible name matching, quote-copying or similarity improvements, stronger self-reference/generic-phrase rejection, off-topic filtering, and an audit of FirstHx predictions outside the golden set. Relaxing textual checks could improve recall while admitting semantically wrong relationships; these needed separate measurement.

Historical graph-inspection queries, database identifiers and access details are omitted. The architectural lesson is to query by stable product identity and extraction provenance, rather than counting all relationships already present in a seeded graph.
