# ELI-1192 — Remove Brittle Customer Endpoint Checks

> Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Result

A historical seven-product production-pipeline evaluation using `openai/gpt-5.6-luna` through OpenRouter reported:

| Metric | Before | After |
|---|---:|---:|
| Mean F1 | 12.3% | 29.4% |
| Mean precision | 27.2% | 30.8% |
| Mean recall | 11.4% | 33.7% |
| Evidence-verified extraction misses recovered | 0/77 | 29/77 |

The intervention combined prompt target-awareness, grounding changes, semantic context repair and diagnostic corrections. These results should not be attributed to removal of a single regex in isolation.

## Diagnosis and case-set construction

Five-model diagnostics suggested models often proposed the right customer, only for whole-word endpoint checks to reject it before semantic validation ran. Product-name variants, shortened references and scraping whitespace artifacts were common problems.

The diagnostic denominator changed as its definition improved:

- **86:** initial misses across nine products, selected when a golden customer name appeared as a substring in fetched content. This included raw sitemap URL text and unsupported bare-name labels.
- **78:** after excluding non-prose sources and requiring cited supporting evidence in the golden set.
- **77:** the actual seven-product production evaluation excluded one case because its nearly empty usable corpus was not re-extracted.

Word-boundary matching also prevented short names matching inside unrelated longer words. These corrections changed the evaluation population; 86, 78 and 77 are not interchangeable denominators.

## Implementation decisions

1. **Tell extraction the target.** The prompt received `target_product_name` and `target_product_aliases` rather than asking the model to infer the product. `LLMAssertionExtractor.extract()` gained `target_name`, passed through from `work.target_name`.
2. **Remove local endpoint-presence gates for customers.** `object_not_in_evidence` and `subject_not_in_evidence` were skipped for the customers profile. Semantic `relationship_claim_supported` became responsible for customer-subject support.
3. **Keep target identity enforcement.** `target_not_endpoint` remained active for every profile at this stage. A supplied target name reduced wrong-product guesses but did not guarantee their absence; co-mentioned platforms could still be returned.
4. **Supply semantic context.** The semantic prompt's context variable had rendered empty. Page content was passed through and the final implementation used `_semantic_evidence_context`.
5. **Clarify relationship semantics.** Semantic validation distinguished actual product provision from investment, awards and co-marketing without treating all partnership wording alike. Exact hosted prompt text is omitted.
6. **Align harness and production.** The Extractor protocol and model-comparison harness were updated to pass the same target argument as production, avoiding a runtime signature mismatch and an easier diagnostic-only path.

## Referent-resolution tradeoff

Removing `subject_not_in_evidence` meant the ELI-1146 customer referent retry no longer triggered from that code. This was an intentional behavior change: customer-subject support moved to the semantic check instead of a reject-and-retry route. Tests were updated to assert this behavior, while still requiring target identity checking.

## Verification

An isolated Suki page-level investigation recovered 11/14 original misses, or 10/12 after the evidence filter. The clean production run yielded 29/77 recovered across seven products. A first attempt was discarded after a trace-service DNS outage left extraction records stuck in `running`; the results above came from the subsequent clean run.

**Historical full suite: 248 passed.** Checks covered removal of customer endpoint-presence gates, retention of target identity enforcement, disabled customer referent retry and consistent extractor signatures.

The diagnostic case builder, model harness, per-page traces and notebook are historical unavailable artifacts, not bundled runnable inputs. Named miss lists, evidence URLs and private review material are omitted.

## Unresolved issues

`target_product_aliases` was wired but passed empty because it had no data source. Recovery was uneven, indicating additional failure classes. Later integration-specific work changed grounding behavior again; this report records the customer-focused intervention at its own historical stage.
