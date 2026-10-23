# ELI-1195 — Integration Grounding and Target Awareness

> Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Objective and change

Remove endpoint string checks that could reject an integration before semantic validation, and supply target identity to the semantic checker. At this stage, integration extraction received a target pinned to a schema field rather than choosing the target freely.

`grounding.py` extended the relevant profile skip conditions to include integrations for `target_not_endpoint`, `subject_not_in_evidence` and `object_not_in_evidence`. `target_name` was threaded through `semantic_rejection_reason()` and `full_rejection_reason()` into prompt construction so target sub-brand variants could be interpreted in context.

This report's historical description treated the existing customer path as already skipping all three checks. ELI-1192 explicitly records retaining `target_not_endpoint` at its own stage. These reports therefore should not be read as a single code snapshot; the customer target-check chronology cannot be resolved from the write-ups alone.

## Suki results

| Condition | Precision | F1 | Recall |
|---|---:|---:|---:|
| Regex checks active | 24.0% | 38.7% | 100% |
| Regex checks removed | 15.8% | 27.3% | 100% |

Removing the checks reduced measured precision while leaving recall at 100%. This is an observed tradeoff, not proof that relaxed checks are generally safe. The originally explored product-alias graph approach did not ship, so the precision decrease remained unrecovered. Integration scoring continued at product level.

## Verification and limitations

Historical grounding tests had one new expected failure corresponding to the old target-endpoint requirement and the same five pre-existing failures. The report observed no new customer-side regressions; it did not report a fully passing test suite.

Only Suki was evaluated with this change. The other six products were not rerun, and product identification/normalization remained follow-up work. The private production graph and scoring artifacts are unavailable; no named integration rows are included here.
