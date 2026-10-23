# ELI-1170 — Model Comparison: gpt-5.4-mini vs gpt-5.6-luna

> Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Question and method

Would a stronger extraction model improve quality enough to justify additional cost and latency? The historical experiment compared the model identifiers `gpt-5.4-mini` and `gpt-5.6-luna` through OpenRouter within the same LangGraph pipeline. These are historical experiment labels, not a claim about current provider availability.

Both models ran real extraction on the same seven-product corpus used for the pipeline diagnostic. Per-model `extractor_version` hashes kept one pass from overwriting the other's baseline. Customers and integrations were scored against the full golden set and an evidence-verified subset. Both models received the same confusion-matrix and disagreement analysis.

Integrations used product-name matching, without brand/product-family alias resolution. The proposed `PRODUCT_ALIAS_OF` approach had not been adopted. Only aggregate metrics are included here; per-product results remain private.

## Real F1 results

Each cell is Luna / Mini.

| Aggregate | Customers | Integrations |
|---|---|---|
| **Mean** | **27.0% / 26.5%** | **17.6% / 14.3%** |

There was no universal winner across products and profiles. Average customer F1 differed little, while Luna's integration mean was higher.

## Limits and historical artifacts

No cost or latency measurements were collected, so the original quality-versus-cost question remained only partially answered. Disagreements had not been classified into wrong-type, hypothetical-language and co-mention failure categories. Two other planned model variants were not run.

The historical comparison notebook and shared pipeline helpers contained per-model diagnostics and row-level evidence links. They are unavailable in this public edition. This report preserves the aggregate F1 comparison; it does not invent missing precision, recall, ceiling or confusion-matrix numbers.
