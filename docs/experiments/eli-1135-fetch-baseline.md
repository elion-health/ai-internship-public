# ELI-1135 — Fetch-Stage Baseline Methods and Aggregate Findings

> Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Objective and scope

Establish an LLM-independent baseline of first-party crawl/fetch health: fetch failures, returned but unusable content, and content-length distributions. The measured LangGraph stages were `map_first_party → fetch_first_party`; external acquisition was excluded.

This pass used nine marketplace products, replacing an earlier three-product exploratory corpus. Each target's prior state was reset and freshly mapped at `RESEARCH_FIRST_PARTY_PAGE_LIMIT=200`, up from the default 50. Vendor identities and per-product metrics are omitted from this public edition.

## Definitions

- **Errored:** the fetch/scrape failed without retrieving content.
- **Blockade:** the fetch returned content, but it represented a non-2xx response, a bot/CAPTCHA challenge or a gated-document form rather than the requested material. The term includes stale 404 pages, not just access denial.
- **Thin content:** accessible short text; this is not automatically a blockade.

A page need not spell out a vendor's full product name to be successfully fetched. The earlier “unnamed” category was removed because it measured extraction relevance rules rather than fetch health.

## Historical measurement method

A read-only `vendor_fetch_baseline.py` queried durable `FrontierUrl` and `RetainedSource` records without scraping or invoking an LLM. Targets were keyed by canonical URL, not display name.

First-party sources were reached through `FrontierUrl.origin == "first_party"`, `status == "fetched"` and that frontier's `latest_source_id`. This avoided a provenance trap: globally content-addressed sources retain their original `entrypoint` even when subsequently acquired by another path.

The historical error rate used the sum of failed-attempt counters across all first-party URLs, regardless of current status, divided by that sum plus the count currently fetched. This preserved failed retries that a status snapshot would hide. **It mixed attempt counts and URL counts; ELI-1197 later replaced it with mutually exclusive URL-status buckets and separate retry counts.** Retain that distinction when interpreting this baseline.

Blockade rates used retained sources and these checks:

1. HTTP status outside 2xx, labeled `http_<code>`.
2. Narrow text patterns for a named document behind a lead-generation form, labeled `gated_content`.
3. CAPTCHA/browser-challenge wording, labeled `bot_detection`.

Length rates independently measured raw content greater than 100, 200, 300, 400 and 500 characters. They did not depend on blockade classification.

The historical command was `RESEARCH_FIRST_PARTY_PAGE_LIMIT=200 uv run python scripts/vendor_fetch_baseline.py --thresholds 100 200 300 400 500`. Its source corpus, generated metrics and charts are unavailable; use the standalone setup referenced above instead of treating this as a bundled reproduction command.

## Findings

- Domain policy can eliminate coverage before fetching. Cross-host redirects require explicit canonical-host handling.
- Stale sitemap entries can return short error bodies rather than useful evidence.
- Gated documents and server errors are distinct from accessible but sparse navigation pages.
- Successful retrieval alone does not establish that a page is usable for extraction.

Across all nine products, no outright fetch failures were recorded in this pass. Fetch success was therefore a weak proxy for usable evidence. The earlier three-product pass had found one blockade, 20% of a five-page Fold Health corpus; broader and deeper crawling exposed more failure classes.

## Why full-name presence was removed

The earlier “unnamed” metric classified 23/44 Flatiron pages (52.3%) as failures. A generic company page could repeatedly name the vendor without mentioning its specific product. Requiring every product-name token can be a valid extraction relevance policy, but it is not a fetch-health measure.

Likewise, the 100-character threshold was below the pipeline's default 200-character eligibility gate. Very short pages were still retained with excluded quality status, so raw lengths remained inspectable. A length check independent of stored labels could catch inconsistencies, but thresholds around 300 characters were more informative for many sites in this pass.

## Methodological corrections and limits

- Failed retry attempts were counted even after a later success overwrote current status; later work further separated attempt and URL units.
- Source inclusion followed the target's first-party frontier, avoiding frozen source-level acquisition provenance.
- Canonical URL identity avoided name collisions between target records.
- Blockade detection was heuristic. `bot_detection` did not fire in either the original or expanded corpus, so real-data coverage of that detector remained unverified.
- The 200-page crawl was controlled and fresh, unlike earlier exploratory measurements of shared development state. Deeper crawling plausibly exposed long-tail failures, but no matched 50-versus-200 causal comparison was reported.
- Fetch errors and blockades lived in different schema fields and required different queries.

Charts, URL dumps, caches and machine-readable metrics are omitted. Vendor-level numerical baselines remain private.
