# ELI-1248 — Reference-label review and isolated scoring impact

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Research question

How much of low extraction performance reflects reference-label error rather than actual pipeline misses? This follow-up evaluated semantic flags from ELI-1227 and rescored fixed extraction output against an in-memory corrected reference set. It did not edit the source dataset or rerun extraction.

## Sampling and method

The review targeted three semantic flags, rather than all mechanical evidence-quality flags. Four small buckets were covered in full; a larger bucket used a seeded sample.

| Bucket | Available rows | Coverage |
|---|---:|---|
| Customers: unsupported relationship | 31 | Full |
| Customers: subject not a specific organization | 4 | Full |
| Integrations: relationship not established | 19 | Full |
| Integrations: unsupported relationship | 46 | Full |
| Customers: relationship not established | 132 | 40-row sample, seed 1248 |

Rows qualifying for a full-review bucket were excluded from the sampling pool to avoid duplicate review. The initial 140-row scope was extended by 25 rows to complete coverage for products with scoreable extraction output, yielding 165 reviewed records.

Judgments used stored evidence, not newly fetched pages. The outcomes were incorrect label, supported label, or ambiguous. Cross-record consistency checks corrected 18 verdicts, including inconsistent treatment of identical evidence, context attribution, temporal interpretation, and evidence-attachment gaps. Private review materials and annotation wording are omitted.

## Review results

| Outcome | Customers | Integrations | Total |
|---|---:|---:|---:|
| Reviewed | 100 | 65 | 165 |
| Classified incorrect | 62 | 24 | 86 (52%) |
| Supported labels | 26 | 33 | 59 (36%) |
| Ambiguous | 12 | 8 | 20 (12%) |

Of the 86 classified incorrect, 55 concerned partnership, investor, or prospective-deployment language; 31 concerned evidence integrity. The latter inherited ELI-1227's retrieval and snippet-check limitations: no fresh retrieval distinguished actual dead links from bot blocking. Consequently, “incorrect” in that subgroup is conditional on the earlier audit's evidence standard, not independent live verification.

Ambiguity included non-prose evidence, collective entities, and insufficiently specified temporal boundaries. Those 20 rows were not counted as errors or removed in the experiment.

## Isolated scoring experiment

Score the same extracted output twice: once with original labels, once after removing confirmed-wrong records from a deep copy. This isolates label changes from discovery, extraction, and model variance.

Only 50 of the 86 corrections could be scored: 36 belonged to products without live extraction output. Although nine products were configured, only eight had relevant output; the reported corrections affected nine product/profile passes.

| Measure | Before | After |
|---|---:|---:|
| False negatives across nine affected passes | 133 | 89 |
| False positives across nine affected passes | 104 | 110 |

Thus 44 false negatives (33.1%) were removed. The 50 corrections combined 33 relationship-language corrections and 17 evidence-integrity corrections, so the experiment does not isolate those two root causes.

### Suki: correlated model and label errors

| Measure | Before | After |
|---|---:|---:|
| Corrected labels | — | 8 |
| False negatives | 9 | 7 |
| False positives | 39 | 45 |
| Precision | 0.220 | 0.100 |
| Recall | 0.550 | 0.417 |
| F1 | 0.314 | 0.161 |

Six of eight corrected Suki labels had also been extracted by the model. Removing them exposed false positives previously counted as true positives. Reference error and model error were therefore correlated, rather than independent. The other eight affected product/profile passes showed no false-positive change.

## Conclusions and limits

Correcting labels improved most affected scores only modestly and reduced Suki's score. Real extraction misses remained a major problem; reference cleanup alone did not explain low performance. The selected flagged population is not a random estimate of whole-dataset error. Evidence-integrity cases need better retrieval validation, ambiguous cases need a consistent public evaluation specification, and separate rescoring by correction type would improve causal interpretation. No relationship examples or row-level judgments are included here.
