# ELI-1122 — Time and Cost of Stateful Graph Reuse

> Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Objective and method

Measure the value of persistent Neo4j state in the LangGraph research pipeline. Three repetitions on Flatiron OncoEMR alternated a target-state reset, a cold run and an immediate warm run, using initial mode and the customers profile.

Cold runs removed target-owned crawl and extraction state. Shared entities remained because other targets could reference them. Warm runs reused completed inventory and skipped terminal extraction work matching the same `extractor_version`.

Wall-clock duration used `time.monotonic()` around `graph.ainvoke`; per-phase metrics were logged. LLM dollar costs came from OpenRouter call metrics in historical tracing spans, grouped by `langgraph_node`. Server-side trace filtering by nested thread metadata replaced slow client-side pagination over the whole project.

Historical helper scripts were `reset_target_state.py`, `run_stateful_research.py` and `fetch_run_cost.py`. Their private graph state and trace store are unavailable; these names describe the historical method rather than a supported standalone command sequence.

## Results: time

| Repetition | Cold (s) | Warm (s) | Speedup |
|---|---:|---:|---:|
| 1 | 475.82 | 89.50 | 5.3× |
| 2 | 283.18 | 86.65 | 3.3× |
| 3 | 412.12 | 72.32 | 5.7× |

Every warm run had zero first-party map/fetch provider calls and approximately zero time in those phases. Reuse consistently reduced wall-clock time in these three repetitions.

## Results: cost

| Repetition | Cold ($) | Warm ($) |
|---|---:|---:|
| 1 | 0.0981 | 0.0279 |
| 2 | 0.0961 | 0.1173 |
| 3 | 0.2241 | 0.0463 |

| Repetition | Referent resolution, cold → warm | Extraction, cold → warm | External search reasoning, cold → warm |
|---|---|---|---|
| 1 | $0.0137 → $0.0000 | $0.0632 → $0.0113 | $0.0213 → $0.0166 |
| 2 | $0.0196 → $0.0000 | $0.0559 → $0.0951 | $0.0206 → $0.0222 |
| 3 | $0.0184 → $0.0000 | $0.1804 → $0.0159 | $0.0253 → $0.0304 |

Referent resolution, restricted to first-party content, fell to exactly zero cost on every warm run. Extraction call counts dropped from over 100 cold to 16–20 warm, but token volume depended strongly on newly discovered page lengths. In repetition 2, extraction cost increased despite roughly seven times fewer calls.

External-source search reasoning reran each invocation without inventory/version gating and remained roughly flat. Thus graph reuse reliably saved time here, but did not reliably reduce total measured LLM dollars. The table does not establish an all-provider billing total.

## Reset correctness

Two reset bugs were found and corrected:

1. **Shared retained sources.** `RetainedSource` was globally merged by canonical URL and content hash, so multiple targets could share a source. Deleting every source linked to the reset target could destroy another target's extraction state. Scope gathering was changed to exclude sources with another owning target, including all downstream extractions, episodes and relationships. Four shared sources were correctly excluded in a live duplicate-target check, with a unit regression test.
2. **Early failed discovery runs.** Traversal through discovered URLs or retained sources missed runs that failed before producing either. Direct matching against target identity in `DiscoveryRun.config` found all four historical runs where traversal found only two. A unit test covered this failure shape.

Trace pagination was also checked against a complete trace's span count; the body cursor behaved correctly.

## Limitations

Only one target and three repetitions were measured. They support the observed timing result but not a tight cost confidence interval or broad generalization. External acquisition was entirely ungated, limiting reuse benefits and introducing variable extraction inputs. Shared graph entities also mean “cold” was target-state cold, not an empty database.
