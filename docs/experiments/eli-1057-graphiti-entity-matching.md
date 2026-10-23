# ELI-1057 — Graphiti Fuzzy Entity Matching

> Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Objective

Replace hand-written exact-name resolution with Graphiti's normalized exact, MinHash/Jaccard fuzzy and LLM fallback matching inside the LangGraph ingestion pipeline. The motivating failure was duplicate Product nodes for variants such as `OncoEMR` and `OncoEMR®`.

The primary outcome was a working hybrid resolver, not a proven aggregate score increase. One live duplicate case verified the mechanism; evaluation-score improvements did not reproduce.

## Integration and candidate retrieval

The historical integration used `graphiti_core.resolve_extracted_nodes()` with `existing_nodes_override`. Entities written by `persist_mentions`, rather than Graphiti's episode ingestion, lacked Graphiti-compatible name embeddings. Its default semantic retrieval therefore did not supply a usable pool.

`resolve_entities_via_graphiti()` retained two paths:

1. **Seeded fast path.** Exact matches among entities already identified on the page resolved without an LLM. Two seeded candidates sharing a name remained ambiguous. This behavior mattered because Graphiti's tie handling escalated to an LLM rather than preserving abstention.
2. **Graphiti fallback.** Fetch existing entities of the relevant label, convert candidates and existing entities to `EntityNode` objects, then call Graphiti with that pool. The target itself was in the pool, allowing product-name paraphrases to resolve without a special-case resolver.

`IngestionDependencies.resolve_candidate_entities()` replaced both direct resolver call sites. `USE_GRAPHITI_RESOLUTION` controlled client construction; callers without a client retained the exact-match fallback. The historical LLM client used OpenRouter's compatible endpoint. Connection credentials and private deployment configuration are omitted.

## Direct verification

A new candidate spelled `OncoEMR(R)` resolved to an existing canonical `OncoEMR®` entity rather than creating another duplicate. The result was checked both through the resolver directly and through the ingestion dependency wiring.

## Evaluation comparison

Flatiron OncoEMR was run in initial mode with forced customer extraction, toggling resolution on and off against the same golden set.

| Run | Recall | Precision | F1 |
|---|---:|---:|---:|
| Graphiti on, attempt 1 | 56.25% | 37.50% | 45.00% |
| Graphiti off | 56.25% | 34.62% | 42.86% |
| Graphiti on, clean attempt 2 | 56.25% | 34.62% | 42.86% |

Recall was identical. The first precision gain disappeared on replication, consistent with extraction variance in whether duplicate-prone names were proposed. A separate run was discarded after network failures left extraction records in `running` state.

The golden set contained 16 rows at this point, having grown from six during the investigation. These percentages cannot be directly compared with earlier six-row evaluations. A single duplicate-resolution success is also a small signal within an aggregate evaluation whose other cases do not exercise the changed path.

## Correctness fixes

- **Cross-label matching:** a shared pool could resolve a Product to a same-named HealthSystem. Candidates were grouped by label and matched only within same-label pools.
- **Driver lifecycle:** the Graphiti client opened its own Neo4j driver, separate from the main graph driver. Discarding the client leaked connection pools across graph rebuilds. The client was returned and closed in callers' `finally` blocks.
- **Canonical-name preservation:** a fuzzy match originally retained the extracted variant, which persistence would write over the canonical name. Both seeded and fallback paths were changed to retain the existing entity's name.
- **Extracted-name lookup:** preserving a different canonical name broke assertion-endpoint lookups keyed by the resolved name. Lookups were changed to use the original extracted name while persistence retained the canonical name.

Illustrative fictional type collision: a Product and HealthSystem both called **Fictional Cedar** must remain separate entities even if their names match exactly.

## Limitations

The live example established that matching worked for one duplicate; embedding retrieval and LLM-tier variance were not stress-tested at scale. Fetching all entities of a label was a workaround for missing embeddings, not a demonstrated large-graph retrieval solution. The integration point was not a documented public API, creating upgrade risk. Historical evaluation scripts and traces are unavailable in this edition.
