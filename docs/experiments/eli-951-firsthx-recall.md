# ELI-951 — Verify search-tool behavior before tuning recall

> Public research edition of the historical report. Implementation comparisons outside this release's scope and private request/response artifacts are omitted. This is a historical diagnosis, not a current provider compatibility guarantee. See [reproduction](../reproduction.md) for supported setup.

## Question

The experiment investigated whether the search settings passed by a LangGraph server-tool variant actually controlled the provider's search behavior. It evaluated one product, FirstHx; changes needed broader repeated evaluation before becoming defaults.

## Method

Hold the model, prompt and product constant and change only the provider request shape. Inspect actual request payloads and reported tool usage, not just the client-side configuration. Compare result quality, latency, cost and the number of searches performed.

## Findings

- The presumed five-result cap did not describe actual behavior: one observed request reported 27 search requests. The intended search engine and result-limit settings could not be assumed to have taken effect.
- The provider's documented plugin schema behaved differently, supplying one search's results rather than an explicit iterative search loop.
- A rerun of the same configuration moved F1 by 3.85 percentage points, recall by 14.28 and precision by −5.56 on the single-product sample. This illustrates why one rerun cannot establish an improvement.
- The measured request cost differed substantially between configurations, approximately $0.31 versus $0.018. A nominal result budget was not a reliable cost estimate when the tool path itself differed.

## Implication for the retained LangGraph approach

Verify request routing and tool-use accounting before increasing budgets. When iteration is required, use a LangGraph loop with an explicit search tool and observable call limits. A server-managed search binding does not by itself prove which engine runs or which limit it honors.

The original raw request/response bodies and benchmark traces are not included. These findings do not certify current server-tool behavior; provider APIs and model availability can change.
