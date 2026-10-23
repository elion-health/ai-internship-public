# Pipeline walkthrough

This guide follows the merged research code while describing the standalone, ten-product release. All Python paths below are relative to `src/ai_internship/`.

## 1. Find and retain pages

The outer graph in `research/graph.py` initializes a target, maps its first-party domain, fetches pages, plans extraction, and runs it. External discovery then supplies additional pages through the same extraction path.

- `research/acquisition.py` maps the domain root and prioritizes URLs under the product's path. Redirect-host selection keeps the inventory on the relevant site. Fetch concurrency is bounded by `RESEARCH_FETCH_CONCURRENCY` (default 8).
- Completed inventories and frontier state support reuse and retries. Cache reuse is not a freshness guarantee.
- Product-path priority applies only to URLs the mapper returned within its limit; it cannot recover product pages omitted by that initial map.
- `discovery/deep_agent.py` implements budgeted external search, mapping, scraping and crawling. Firecrawl supplies these operations by default; Exa is optional and disabled by default.
- `discovery/store.py` stores the frontier, retained source content and hashes, target associations, run outcomes and extraction versions. It is more than a page cache.
- An optional external evidence judge reads selected pages before retaining them. It remains off by default and has separate cost/failure behavior described in [reproduction](reproduction.md).

## 2. Extract and resolve names

The inner graph in `enrichment/graph.py` runs:

```text
load_source → extract → resolve → verify_grounding → persist → record_outcome
```

Extraction reads retained content rather than fetching the live web. Customer and integration prompts propose structured assertions. `graph/graphiti_resolve.py` resolves their endpoints against the catalog; it does not guarantee that every spelling or product-family ambiguity is resolved.

The example catalog contains only identities derived from the ten bundled products and their reference labels. It contains no pre-seeded answer relationships and is smaller than the original research catalog.

## 3. Check the evidence

Within `_ground_assertions`:

1. Check that a relationship has usable endpoints.
2. If a non-contiguous quote is nevertheless supported by source-text matching, replace it with a contiguous span from the page and force a whole-page referent check. An invented quote is not automatically repaired.
3. When a quote does not name both endpoints, ask the referent model to read the page and confirm the relationship. First-party pages use profile-specific first-party prompts; external pages use the shared prompts. A declined or failed confirmation rejects that recovery.
4. Apply deterministic grounding checks and the semantic panel. The referent's proof sentence and reasoning reach the panel, but the reasoning is identified as an argument, not quoted page evidence.
5. Five concurrent semantic calls vote independently per criterion. Only successful votes participate. If all calls fail, the existing semantic wrapper can fail open. No-model test configurations also bypass model checks.
6. Persist accepted relationships and record rejection reasons and outcomes.

Text normalization and similarity tolerance mean the runtime is not a strict byte-equality verifier. Quote repair changes newly generated assertions; it does **not** modify the bundled sample's original quotations.

## 4. Know what changes invalidate reuse

`enrichment/profiles.py::extractor_version()` hashes the model and the extraction, semantic, shared-referent and first-party-referent prompt versions. A changed referent prompt therefore changes the extraction version even before a source's origin is known. Explicit profile forcing is available for controlled reruns.

Prompts are bundled under `assets/prompts/`; `public_assets.py` loads them offline by default. Hosted mode points to the user's configured Braintrust project. The original broader datasets, private graph and review artifacts are not dependencies of this release.
