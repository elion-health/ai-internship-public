# ELI-1227 — Auditing reference-label quality

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Research question

Does the reference dataset used to score extraction contain evidence-quality or relationship-label problems that distort reported performance? The read-only audit examined 516 customer records and 135 integration records. Automated flags were diagnostic signals, not final error verdicts.

## Method

Deterministic checks inspected missing evidence, source-domain classification, failed evidence retrieval, snippet grounding, and entity-name presence. Semantic checks reused the pipeline's relationship-verification logic to identify unsupported claims, relationships not established as having happened, and subjects that were not specific organizations. Each evidence entry was evaluated; one supporting entry could establish support for a record.

The first run flagged approximately 97% of both datasets. Investigation showed substantial checker error, requiring corrections before interpreting label quality:

1. Strip markup, scripts, and styles and decode HTML entities before comparing visible text.
2. Remove a layer of stored wrapping quotation marks.
3. Use near-verbatim grounding rather than exact substring matching.
4. Send a browser User-Agent to reduce false dead-link classifications.
5. Parse PDF text rather than comparing snippets against binary content; offload CPU-bound PDF parsing from the asynchronous event loop.
6. Match source hostnames and their subdomains, check HTTP status, and distinguish a deep-link redirect to a homepage from a successful evidence retrieval.
7. Use word boundaries for names, require a nonempty evidence URL, handle explicit null snippets without borrowing another entry's text, and isolate semantic failures per record.
8. Reject non-positive concurrency and escape rendered output.

These changes mattered empirically: intermediate flagged rates fell from approximately 97% to 95.3%/95.6%, then 77.5%/81.5%, and then 69.6%/79.3% for customers/integrations. Threading and later robustness fixes preceded the final results below. Some intermediate changes affected concurrent fetch timeouts as well as their direct check, so these stages are not isolated causal estimates. Historical test coverage reached 48 tests.

## Final results

| Measure | Customers | Integrations |
|---|---:|---:|
| Records | 516 | 135 |
| Passed every check | 169 (32.8%) | 34 (25.2%) |
| Flagged at least once | 347 (67.2%) | 101 (74.8%) |

| Flag | Customers | Integrations |
|---|---:|---:|
| `name_not_in_snippet` | 191 | 55 |
| `relationship_not_actually_happened` | 132 | 19 |
| `fabricated_snippet` | 128 | 33 |
| `dead_link` | 75 | 17 |
| `relationship_claim_not_supported` | 31 | 46 |
| `missing_evidence` | 29 | 2 |
| `subject_not_real_organization` | 4 | 0 |
| `aggregator_source` | 2 | 3 |

Flags overlap. The roughly 230 semantic-flag records were the main proposed follow-up population; the full flagged count is not an estimate of incorrect labels.

## Findings and limitations

The recurring semantic issue was evidence of a partnership, proposed deployment, or association being treated as proof of an established customer or technical integration. However, the semantic flags had not yet been independently validated at scale.

Name absence often reflected evidence selection rather than a wrong entity: a snippet could omit a name established by the page title or surrounding text. The check did not distinguish this referent-distance problem from genuine entity confusion.

The labels `dead_link` and `fabricated_snippet` overstate what the checks prove. Bot protection can block plain HTTP access to a working page, and inaccessible content cannot independently establish that a snippet was fabricated. Browser-rendered retrieval remained a follow-up.

The audit also found specification ambiguity around non-prose evidence, multi-organization entities, and entity-level attribution. This is a label-standard consistency issue, distinct from extraction failure. Private annotation rules and their exact wording are omitted.

## Evaluation design for follow-up

Review a scoped semantic-flag sample, then score the same fixed extraction output against original and corrected reference labels. The scorer must actually load the revised labels: a graph-only extraction-yield calculation cannot measure reference-dataset edits. No reference records were changed in this audit. ELI-1248 documents the subsequent review and isolated scoring experiment.
