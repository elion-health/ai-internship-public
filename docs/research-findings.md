# Research approach and aggregate findings

The research studies evidence-backed customer and integration enrichment. It combines a reusable source store, structured extraction, entity resolution, and evidence validation. Research code and aggregate findings are shared here; the underlying broader datasets remain private.

## Workflow

1. Map and fetch a vendor's site under a configurable budget.
2. Store sources, content hashes, provenance and extraction versions in Neo4j.
3. Extract customer and integration assertions separately.
4. Resolve names against catalog entities and check the quoted evidence.
5. Use external discovery to seek additional sources, optionally judging page relevance before retention.
6. Record accepted relationships, rejections and run metrics for diagnosis and reuse.

The catalog in this proposed release is derived only from the ten example products. Reusing sources saves repeated acquisition work, but is not a freshness policy. A stored relationship still requires valid evidence and lifecycle handling.

## Earlier aggregate observations

- A historical 61-product external-discovery run found 88 of 239 reference names in retrieved text. Discovery LLM cost was $2.1914, mean discovery duration was about 52.9 seconds, and 32 provider calls failed. This is a name-presence proxy, not verified incremental adoption or all-in cost.
- A historical extraction audit counted 202 customer matches, 424 accepted names outside the reference set and 438 missed reference names. Integration counts were 10 matches, 7 accepted names outside the reference set and 216 misses. Incomplete labels and missing cases mean these are overlap statistics, not independently adjudicated accuracy.
- Retrieval coverage, evidence interpretation, entity identity and incomplete reference labels are distinct failure sources. Improving one does not establish an end-to-end gain.

## Earlier update: optional evidence judge

Upstream revision `7c0e863` adds a read → judge → keep step after external search. Customer questions ask about adoption by a named organization; integration questions ask about interoperability with a named product. The judge reads up to 8,000 characters of each selected page, with structured output. It also adds content-hash deduplication across retained URLs, crawl-cap enforcement before look-ahead mapping, and batched graph-state deletion.

The upstream study ran external discovery plus extraction on 61 products, with three runs per arm and first-party crawling stubbed. Reported aggregate scores:

| Reference scope | Profile | Judge | Precision | Recall | F1 |
|---|---|---|---:|---:|---:|
| External-only | Customers | Off | .178 | .187 | .182 |
| External-only | Customers | On | .195 | .210 | .202 |
| External-only | Integrations | Off | .136 | .213 | .166 |
| External-only | Integrations | On | .186 | .291 | .226 |
| All reference | Customers | Off | .456 | .188 | .265 |
| All reference | Customers | On | .478 | .202 | .284 |
| All reference | Integrations | Off | .528 | .214 | .304 |
| All reference | Integrations | On | .499 | .202 | .287 |

Only the external-only integration F1 ranges separated across the repeated runs: .220–.238 with judging versus .143–.199 without. Other differences were within run-to-run spread. The judge stays off by default. These are historical reported findings, not a new replication or a demonstrated production improvement. The ten examples cannot reproduce the full study.

The A/B harness is retained with optional caller-provided leads; no private prior-run corpus is bundled.

## October 15 update: caching and evidence interpretation

Upstream main `93586bb` merges PRs #90, #91 and #93–98. This release includes the runtime changes and an adapted cache-only CLI:

- Map domain roots, prioritize product-path URLs, handle redirected inventories, and bound concurrent first-party fetching.
- Use separate first-party customer and integration referent prompts. External pages keep the shared prompts.
- Pass whole-page referent proof and reasoning to a five-call semantic panel; hash both referent versions into extraction reuse keys.
- Repair supported spliced quotes from source text and force referent confirmation, rather than discarding every non-contiguous quotation. Text normalization is now idempotent.

The first-party studies report better referent-step agreement, but that does not necessarily improve final graph output: the downstream semantic panel can reject the same claims. The integrations study reported only small, variable final-edge gains across repeated runs.

### Quote repair and customer framing

The private post-extraction study reports these aggregate results over 6,429 `(page, relationship)` candidates against its reviewed answer key:

| Configuration | Precision | Recall | F1 |
|---|---:|---:|---:|
| Original extraction run | .944 | .634 | .758 |
| With quote-splice repair | .940 | .685 | .792 |
| Also updated customer referent prompt | .940 | .710 | .809 |

These are reported replay results on the affected candidates, not a new full live-web run or an independent replication. Repeated site navigation can reproduce the same relationship on many pages; page-level recall can therefore overstate the gain in distinct vendor relationships. The answer key and written row-level adjudications remain private.

### Confidence-score research

The merged research explores logistic scoring and shallow trees over semantic votes, referent decisions, quote structure and other recorded features. Its feature search reports best F1 around .907 on the reviewed page-level key, compared with .758 for the original fixed gate; a shallow tree reached about .900. These exploratory results are not production accuracy or calibration guarantees. Feature and threshold selection used the same corpus, and page-level folds can share related claims; a held-out vendor-level evaluation is still needed.

The `customers profile` feature encodes a corpus base rate rather than evidence about an individual relationship. The next useful experiment is to remove it and evaluate transfer to new vendors. Entity resolution, repeated discovery work, read-budget use, multiple first-party seed URLs and image-only evidence also remain open questions.

Confidence scoring is **not wired into the graph**. The corpus-dependent fitting, search and adjudication tools remain in the private source repository alongside their inputs. No trained scores, full benchmark records, review sheets or broader website cache are bundled here. The ten illustrative products cannot reproduce those experiments.
