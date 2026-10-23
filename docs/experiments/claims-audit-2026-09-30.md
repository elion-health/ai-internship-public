# Claims audit — 2026-09-30 (abridged methodology and aggregate edition)

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Scope and method

The historical audit inspected 440 claims emitted by the LangGraph pipeline between 2026-09-29 05:20 and 05:39. It separated customers from integrations and recorded five observable properties: membership in the reference dataset, whether the evidence quote spelled out the claimed organization, whether it spelled out the vendor, whether that quote was reused for another claim, and whether the source page was classified as on the vendor's own domain.

The original report then enumerated claims absent from the reference dataset with their stored evidence. That enumeration is omitted entirely. This edition preserves the aggregate observations, not a new adjudication of the claims.

## Results

| Measure | Customers | Integrations |
|---|---:|---:|
| Claims | 323 | 117 |
| In reference dataset | 157 | 58 |
| Not in reference dataset | 166 | 59 |
| Quote spells out claimed organization | 251 | 104 |
| Quote spells out vendor | 195 | 66 |
| Quote reused for another claim | 82 | 24 |
| Page classified as on vendor's own domain | 0 | 0 |

These properties overlap and should not be added together. In total, 215 claims matched the reference dataset and 225 did not. Reused quotes affected 106 claims; that is a claim count, not a count of unique repeated passages.

## Interpretation and limitations

- Absence from the reference dataset is not proof of a false relationship: the reference set may be incomplete.
- Literal name presence is a diagnostic, not semantic verification. Aliases, abbreviations, surrounding page context, and product-versus-vendor naming can change the interpretation.
- Repeated evidence can support multiple entities in a genuine collective statement, or incorrectly attribute generic boilerplate to unrelated entities. Reuse alone does not distinguish those cases.
- The zero first-party classification is preserved as reported. The historical write-up did not validate that classifier, so it should not be treated as independently established source provenance.
- The report did not establish precision or recall from these mechanical counts, nor provide a controlled comparison against another pipeline configuration.

This abridged edition contains no claim-level evidence, relationship lists, or source-URL inventory.
