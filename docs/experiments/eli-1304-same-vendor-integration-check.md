# ELI-1304 — Rejecting same-vendor integrations

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Problem and approach

Nine documented false positives represented a target vendor's own products or features as independent integration partners. Only two had lexical overlap with the vendor name; the others required self-reference cues or ownership knowledge absent from the quoted text.

The correction combined three layers:

| Layer | Purpose |
|---|---|
| Existing `self_edge` | Reject identical subject and object |
| `_quote_names_compound_brand` | Detect strict adjacent compound-brand tokens using whole-word boundaries |
| `is_third_party_vendor` | Judge ownership/self-reference for integration assertions |

The deterministic compound-brand test required zero intervening words, avoiding rejection of genuine relationships connected by conjunctions or integration language. A broader lexical-overlap rejection was removed: an inclusive endpoint matcher was unsafe as a hard rejection and caught none of the nine documented errors.

## Semantic stability

The schema field alone did not reliably steer the model; matching semantic-prompt instructions were necessary. The integration check then used five model calls and majority voting per field. Customer adoption checks remained single-call. Historical validation used `openai/gpt-5.4-mini` against real stored evidence.

| Iteration | Observation |
|---|---|
| Schema field alone | 4/9 caught; one true integration rejected |
| Matching prompt text | 6/9 caught; regression persisted |
| Broader unified-solution wording | 7/9 caught; a different true integration rejected |
| Narrower wording | Regression corrected, but repeated calls still varied |
| Deterministic compound-brand check plus voting | 7/9 caught stably; 0/7 true-integration regressions |

Three final repeated validation runs were stable. Six cases were handled through the other cheap/semantic checks and one compound-brand case was caught deterministically. Two ownership-dependent cases remained uncaught even when tested with full retained page context.

## Robustness corrections

- Replace prefix/substring matching with word boundaries.
- Return safely for whitespace-only names rather than indexing an empty token list.
- Collect vote exceptions with `return_exceptions=True`; use successful votes and re-raise only when all fail, retaining the existing fail-open contract.
- Preserve the distinction between evidence supplied to the model and ownership facts it cannot infer from that evidence.

## Limitations

The checks assumed the target was pinned to the expected assertion endpoint; the schema did not structurally enforce that orientation. Reversed endpoints could therefore reject a real integration. Cited product aliases derived from first-party pages were the proposed remedy for ownership information absent from quotes.

No full-corpus precision/recall/F1 rerun was performed. The previously suggested F1 change from .279 to .283 was not measured here; evidence is limited to the nine error cases and seven regression cases. Dataset quotes and named relationship examples are omitted.
